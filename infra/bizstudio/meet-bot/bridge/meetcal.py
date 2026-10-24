"""Google Calendar auto-join: parse the secret iCal feed and decide when to send a bot."""

import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

MEET_LINK = re.compile(r"https?://meet\.google\.com/([a-z]{3,4}-[a-z]{4}-[a-z]{3,4})(?![a-z-])", re.I)
NOBOT = "#nobot"
HORIZON = 24 * 3600
# Occurrences that already started are still fetched, so a bridge restart mid-meeting can join late.
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


def meet_link(event) -> str | None:
    for field in ("X-GOOGLE-CONFERENCE", "LOCATION", "DESCRIPTION"):
        match = MEET_LINK.search(str(event.get(field) or ""))
        if match:
            return f"https://meet.google.com/{match.group(1).lower()}"
    return None


def declined(event, owners: set[str]) -> bool:
    attendees = event.get("ATTENDEE") or []
    if not isinstance(attendees, list):
        attendees = [attendees]
    for attendee in attendees:
        address = str(attendee).lower().removeprefix("mailto:")
        if address in owners and str(attendee.params.get("PARTSTAT", "")).upper() == "DECLINED":
            return True
    return False


def timestamp(value: datetime, local_tz: ZoneInfo) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=local_tz)
    return value.timestamp()


def parse(ics: bytes, now: float, owners: Iterable[str] = (), local_tz: ZoneInfo = ZoneInfo("Europe/Rome"),
          horizon: int = HORIZON) -> list[Occurrence]:
    """Meet occurrences overlapping [now - LOOKBACK, now + horizon] that the bot should consider."""
    calendar = icalendar.Calendar.from_ical(ics)
    owner_set = {email.lower() for email in owners if email}
    calname = str(calendar.get("X-WR-CALNAME") or "")
    if "@" in calname:
        owner_set.add(calname.lower())
    recurring = {str(c.get("UID")) for c in calendar.walk("VEVENT")
                 if c.get("RRULE") or c.get("RDATE") or c.get("RECURRENCE-ID")}
    window_start = datetime.fromtimestamp(now - LOOKBACK, timezone.utc)
    window_end = datetime.fromtimestamp(now + horizon, timezone.utc)
    found: dict[str, Occurrence] = {}
    for event in recurring_ical_events.of(calendar).between(window_start, window_end):
        dtstart = event.decoded("DTSTART")
        if not isinstance(dtstart, datetime):
            continue
        if str(event.get("STATUS") or "").upper() == "CANCELLED":
            continue
        title = str(event.get("SUMMARY") or "").strip()
        if NOBOT in f"{title}\n{event.get('DESCRIPTION') or ''}".lower():
            continue
        url = meet_link(event)
        if not url or declined(event, owner_set):
            continue
        start = timestamp(dtstart, local_tz)
        if event.get("DTEND"):
            dtend = event.decoded("DTEND")
            end = timestamp(dtend, local_tz) if isinstance(dtend, datetime) else start + 3600
        elif event.get("DURATION"):
            end = start + event.decoded("DURATION").total_seconds()
        else:
            end = start + 3600
        uid = str(event.get("UID") or f"{title}|{start:.0f}")
        key = uid
        if uid in recurring:
            # RECURRENCE-ID is the original slot, so a moved occurrence keeps its key and its toggle.
            rid = event.decoded("RECURRENCE-ID") if event.get("RECURRENCE-ID") else dtstart
            key = f"{uid}|{timestamp(rid, local_tz) if isinstance(rid, datetime) else start:.0f}"
        found[key] = Occurrence(key, uid, start, end, title or "Riunione senza titolo", url)
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


def upcoming(conn: sqlite3.Connection, lock: threading.Lock, now: float, limit: int = 30) -> list[sqlite3.Row]:
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

