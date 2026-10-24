"""Google Calendar auto-join: turn Calendar API events into occurrences and decide when to send a bot."""

import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

MEET_LINK = re.compile(r"https?://meet\.google\.com/([a-z]{3,4}-[a-z]{4}-[a-z]{3,4})(?![a-z-])", re.I)
NOBOT = "#nobot"
HORIZON = 7 * 86400
# Events that already started are still fetched, so a bridge restart mid-meeting can join late.
LOOKBACK = 3600
LEAD_SECONDS = 60
LATE_SECONDS = 30 * 60
# A launched occurrence moved by more than this gets a new bot at its new time.
MOVE_TOLERANCE = 10 * 60
MAX_ATTEMPTS = 5
KEEP_SECONDS = 2 * 86400


class Busy(ValueError):
    """Another bot is in a meeting: retry on the next tick instead of counting a failure."""


@dataclass(frozen=True)
class Occurrence:
    key: str
    uid: str
    start: float
    end: float
    title: str
    meeting_url: str


def event_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def meet_link(item: dict) -> str | None:
    candidates = [item.get("hangoutLink") or ""]
    candidates += [point.get("uri") or "" for point in (item.get("conferenceData") or {}).get("entryPoints") or []
                   if point.get("entryPointType") == "video"]
    candidates += [item.get("location") or "", item.get("description") or ""]
    for text in candidates:
        match = MEET_LINK.search(text)
        if match:
            return f"https://meet.google.com/{match.group(1).lower()}"
    return None


def declined(item: dict) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees") or [])


def timestamp(value: dict, local_tz: ZoneInfo) -> float | None:
    """Calendar API start/end object; None for all-day dates."""
    if not value.get("dateTime"):
        return None
    moment = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(value["timeZone"]) if value.get("timeZone") else local_tz)
    return moment.timestamp()


def parse_events(items: Iterable[dict], local_tz: ZoneInfo = ZoneInfo("Europe/Rome")) -> list[Occurrence]:
    """Calendar API events.list items (singleEvents=true) the bot should consider."""
    found: dict[str, Occurrence] = {}
    for item in items:
        if item.get("status") == "cancelled" or not item.get("id"):
            continue
        start = timestamp(item.get("start") or {}, local_tz)
        if start is None:
            continue
        title = (item.get("summary") or "").strip()
        if NOBOT in f"{title}\n{item.get('description') or ''}".lower():
            continue
        url = meet_link(item)
        if not url or declined(item):
            continue
        end = timestamp(item.get("end") or {}, local_tz) or start + 3600
        # With singleEvents an instance id is "<recurringEventId>_<original start>": it survives moves.
        found[item["id"]] = Occurrence(item["id"], item.get("recurringEventId") or item["id"], start, end,
                                       title or "Riunione senza titolo", url)
    return sorted(found.values(), key=lambda o: o.start)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calendar_events (
            key TEXT PRIMARY KEY,
            id TEXT UNIQUE NOT NULL,
            uid TEXT,
            start REAL NOT NULL,
            end REAL NOT NULL,
            title TEXT,
            meeting_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            bot_id TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()


def reconcile(conn: sqlite3.Connection, lock: threading.Lock, occurrences: list[Occurrence], now: float) -> None:
    """Mirror the feed into calendar_events. Only call it after a successful fetch."""
    with lock:
        seen = set()
        for occ in occurrences:
            seen.add(occ.key)
            row = conn.execute("SELECT * FROM calendar_events WHERE key = ?", (occ.key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO calendar_events (key, id, uid, start, end, title, meeting_url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (occ.key, event_id(occ.key), occ.uid, occ.start, occ.end, occ.title, occ.meeting_url, now),
                )
                continue
            status, bot_id, attempts, error = row["status"], row["bot_id"], row["attempts"], row["error"]
            if status in ("gone", "missed"):
                status, error = "pending", None
            elif status in ("launched", "failed") and abs(occ.start - row["start"]) > MOVE_TOLERANCE and occ.start > now:
                status, bot_id, attempts, error = "pending", None, 0, None
            conn.execute(
                "UPDATE calendar_events SET start = ?, end = ?, title = ?, meeting_url = ?, status = ?, bot_id = ?, attempts = ?, error = ?, updated_at = ? WHERE key = ?",
                (occ.start, occ.end, occ.title, occ.meeting_url, status, bot_id, attempts, error, now, occ.key),
            )
        pending = conn.execute("SELECT key FROM calendar_events WHERE status = 'pending'").fetchall()
        for (key,) in pending:
            if key not in seen:
                conn.execute("UPDATE calendar_events SET status = 'gone', updated_at = ? WHERE key = ?", (now, key))
        conn.execute("DELETE FROM calendar_events WHERE end < ?", (now - KEEP_SECONDS,))
        conn.commit()


def run_due(conn: sqlite3.Connection, lock: threading.Lock, now: float, autojoin: bool,
            launch: Callable[[str, str], str], log: Callable[[str | None, str], None] = lambda *_: None) -> None:
    """Launch at most one bot per occurrence, from LEAD_SECONDS before start until LATE_SECONDS after."""
    with lock:
        due = conn.execute(
            "SELECT * FROM calendar_events WHERE status = 'pending' AND start - ? <= ? ORDER BY start", (LEAD_SECONDS, now)
        ).fetchall()

    def set_row(key: str, **fields) -> None:
        with lock:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            conn.execute(f"UPDATE calendar_events SET {assignments}, updated_at = ? WHERE key = ?", (*fields.values(), now, key))
            conn.commit()

    for row in due:
        if now >= min(row["end"], row["start"] + LATE_SECONDS):
            reason = row["error"] or ("disattivato" if not row["enabled"] else
                                      "ingresso automatico spento" if not autojoin else "non avviato in tempo")
            set_row(row["key"], status="missed", error=reason)
            continue
        if not row["enabled"] or not autojoin:
            continue
        try:
            bot_id = launch(row["meeting_url"], row["title"])
        except Busy as error:
            if row["error"] != str(error):
                set_row(row["key"], error=str(error))
            continue
        except Exception as error:
            attempts = row["attempts"] + 1
            message = str(error)[:300]
            set_row(row["key"], attempts=attempts, error=message, status="failed" if attempts >= MAX_ATTEMPTS else "pending")
            log(None, f"calendar event {row['id']}: launch attempt {attempts} failed: {message}")
            continue
        set_row(row["key"], status="launched", bot_id=bot_id, error=None)
        log(bot_id, f"auto-joined from calendar event {row['id']}")


def upcoming(conn: sqlite3.Connection, lock: threading.Lock, now: float, limit: int = 200) -> list[sqlite3.Row]:
    with lock:
        return conn.execute(
            "SELECT * FROM calendar_events WHERE status != 'gone' AND end > ? AND start < ? ORDER BY start LIMIT ?",
            (now, now + HORIZON, limit),
        ).fetchall()


def toggle(conn: sqlite3.Connection, lock: threading.Lock, event: str) -> bool | None:
    with lock:
        row = conn.execute("SELECT enabled FROM calendar_events WHERE id = ?", (event,)).fetchone()
        if row is None:
            return None
        enabled = 0 if row["enabled"] else 1
        conn.execute("UPDATE calendar_events SET enabled = ?, updated_at = ? WHERE id = ?", (enabled, time.time(), event))
        conn.commit()
        return bool(enabled)


def day_label(start: float, now: float, local_tz: ZoneInfo) -> str:
    when = datetime.fromtimestamp(start, local_tz)
    today = datetime.fromtimestamp(now, local_tz).date()
    if when.date() == today:
        return f"oggi {when:%H:%M}"
    if when.date() == today + timedelta(days=1):
        return f"domani {when:%H:%M}"
    return f"{['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom'][when.weekday()]} {when:%d/%m %H:%M}"

