import hmac
import html
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import requests

import cap
import meetcal

STATE_DIR = Path(os.environ.get("STATE_DIR", "/state"))
ATTENDEE_URL = os.environ.get("ATTENDEE_URL", "http://attendee-app:8000").rstrip("/")
ATTENDEE_API_KEY = os.environ.get("ATTENDEE_API_KEY", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://rec.bizstudio.it").rstrip("/")
BASE_PATH = os.environ.get("BASE_PATH", "/meetbot").rstrip("/")
CAP_URL = os.environ.get("CAP_URL", "http://cap-web:3000").rstrip("/")
OWNER_ID = os.environ.get("MEETBOT_OWNER_ID", "cng79m7693kkajy")
OWNER_EMAIL = os.environ.get("MEETBOT_OWNER_EMAIL", "vincenzopetronebiz@gmail.com").lower()
API_TOKEN = os.environ.get("MEETBOT_API_TOKEN", "")
BOT_NAME = os.environ.get("BOT_NAME", "Bizstudio Recorder")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
MAX_UPTIME_SECONDS = int(os.environ.get("MAX_UPTIME_SECONDS", str(3 * 3600)))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "8"))
DELETE_ATTENDEE_DATA = os.environ.get("DELETE_ATTENDEE_DATA", "true") == "true"
PUBLIC_DEFAULT = os.environ.get("MEETBOT_PUBLIC_DEFAULT", "false") == "true"
LOCAL_TZ = ZoneInfo(os.environ.get("TZ_NAMES", "Europe/Rome"))
CALENDAR_SYNC_SECONDS = int(os.environ.get("CALENDAR_SYNC_SECONDS", "300"))
CALENDAR_TICK_SECONDS = int(os.environ.get("CALENDAR_TICK_SECONDS", "20"))
CALENDAR_ALLOW_HTTP = os.environ.get("CALENDAR_ALLOW_HTTP", "false") == "true"
ICAL_FILE = STATE_DIR / "ical_url"
ICS_MAX_BYTES = 20 * 1024 * 1024

MEETING_URL = re.compile(r"^https://(meet\.google\.com/[a-z]{3,4}-[a-z]{4}-[a-z]{3,4}|teams\.(microsoft|live)\.com/\S+)$")
ATTENDEE_DONE = {"ended", "fatal_error", "data_deleted"}
OPEN_STAGES = ("active", "importing")
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre"]
STATE_LABELS = {
    "ready": "in avvio", "joining": "sta entrando", "waiting_room": "in sala d'attesa: ammettilo dal Meet",
    "joined_not_recording": "dentro, non registra", "joined_recording": "sta registrando",
    "joined_recording_paused": "registrazione in pausa", "leaving": "sta uscendo",
    "post_processing": "salva la registrazione", "ended": "terminato", "fatal_error": "errore",
    "staged": "in coda", "scheduled": "programmato",
}
REASONS = {
    "meeting_not_found": "link del Meet inesistente o riunione non ancora aperta",
    "request_to_join_denied": "la richiesta di partecipare è stata rifiutata",
    "waiting_room_timeout_exceeded": "nessuno lo ha ammesso entro 15 minuti",
    "login_required": "la riunione non accetta ospiti: serve un account Google",
    "blocked_by_captcha": "Google ha chiesto un captcha",
    "meeting_ended_before_bot_joined": "la riunione era già finita",
    "unable_to_connect_to_meeting": "non riesce a connettersi al Meet",
    "heartbeat_timeout": "il bot si è bloccato",
}
STAGE_LABELS = {"importing": "importazione in Bizstudio Recorder", "imported": "importato",
                "failed": "fallito", "no_recording": "nessuna registrazione"}

db_lock = threading.Lock()
running = True


def log(bot_id: str | None, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {bot_id or '-'} {message}", flush=True)


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DIR / "bridge.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bots (
            bot_id TEXT PRIMARY KEY,
            meeting_url TEXT NOT NULL,
            title TEXT,
            stage TEXT NOT NULL DEFAULT 'active',
            attendee_state TEXT,
            video_id TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )"""
    )
    conn.commit()
    meetcal.init_db(conn)
    return conn


conn: sqlite3.Connection


def update(bot_id: str, **fields) -> None:
    with db_lock:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE bots SET {assignments}, updated_at = ? WHERE bot_id = ?", (*fields.values(), time.time(), bot_id))
        conn.commit()


def rows(where: str = "1", params: tuple = (), limit: int = 200) -> list[sqlite3.Row]:
    with db_lock:
        return conn.execute(f"SELECT * FROM bots WHERE {where} ORDER BY created_at DESC LIMIT {limit}", params).fetchall()


def attendee(method: str, path: str, **kwargs) -> requests.Response:
    response = requests.request(
        method, f"{ATTENDEE_URL}/api/v1{path}",
        headers={"Authorization": f"Token {ATTENDEE_API_KEY}", "Content-Type": "application/json"},
        timeout=60, **kwargs,
    )
    if response.status_code >= 400 and response.status_code != 404:
        raise RuntimeError(f"Attendee {method} {path} -> {response.status_code}: {response.text[:300]}")
    return response


def video_name(title: str | None, created_at: float) -> str:
    if title:
        return f"Meet – {title}"
    when = datetime.fromtimestamp(created_at, LOCAL_TZ)
    return f"Meet – {when.day} {MONTHS[when.month - 1]} {when.year}, {when:%H:%M}"


def active_bots() -> list[sqlite3.Row]:
    return rows("stage = 'active'")


def launch(meeting_url: str, title: str | None) -> str:
    meeting_url = meeting_url.strip().split("?")[0] if "meet.google.com" in meeting_url else meeting_url.strip()
    if not MEETING_URL.match(meeting_url):
        raise ValueError("Link non valido: incolla il link del Meet (https://meet.google.com/abc-defg-hij).")
    if active_bots():
        raise meetcal.Busy("C'è già un bot in una riunione: il server ne regge uno alla volta. Fallo uscire prima.")
    payload = {
        "meeting_url": meeting_url,
        "bot_name": BOT_NAME,
        "recording_settings": {"format": "mp4", "view": "speaker_view", "resolution": "720p"},
        "automatic_leave_settings": {"max_uptime_seconds": MAX_UPTIME_SECONDS, "waiting_room_timeout_seconds": 900},
        "metadata": {"source": "meetbot", **({"title": title} if title else {})},
    }
    response = attendee("POST", "/bots", data=json.dumps(payload))
    if response.status_code >= 400:
        raise ValueError(f"Attendee ha rifiutato il bot: {response.text[:300]}")
    bot = response.json()
    now = time.time()
    with db_lock:
        conn.execute(
            "INSERT INTO bots (bot_id, meeting_url, title, attendee_state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (bot["id"], meeting_url, title, bot.get("state"), now, now),
        )
        conn.commit()
    log(bot["id"], f"launched into {meeting_url}")
    return bot["id"]


def failure_reason(bot: dict) -> str:
    for event in reversed(bot.get("events") or []):
        if event.get("sub_type"):
            return REASONS.get(event["sub_type"], f"{event['type']}: {event['sub_type']}")
    return bot.get("state") or "unknown"


def discover() -> None:
    response = attendee("GET", "/bots")
    for bot in response.json().get("results", []):
        with db_lock:
            if conn.execute("SELECT 1 FROM bots WHERE bot_id = ?", (bot["id"],)).fetchone():
                continue
        if bot.get("state") == "data_deleted":
            continue
        created = datetime.fromisoformat(bot["created_at"].replace("Z", "+00:00")).timestamp() if bot.get("created_at") else time.time()
        title = (bot.get("metadata") or {}).get("title")
        with db_lock:
            conn.execute(
                "INSERT OR IGNORE INTO bots (bot_id, meeting_url, title, attendee_state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (bot["id"], bot.get("meeting_url") or "", title, bot.get("state"), created, time.time()),
            )
            conn.commit()
        log(bot["id"], "discovered bot created outside the bridge")


def upload(video_id: str, result: Path, thumb: Path, meta: dict) -> None:
    # Meetings record third parties, so visibility is enforced before any bytes land.
    cap.set_public(video_id, PUBLIC_DEFAULT)
    cap.upload_mp4(video_id, result, meta)
    cap.upload_thumbnail(video_id, thumb)


def import_recording(row: sqlite3.Row) -> None:
    bot_id = row["bot_id"]
    workdir = STATE_DIR / "work" / bot_id
    workdir.mkdir(parents=True, exist_ok=True)
    response = attendee("GET", f"/bots/{bot_id}/recording")
    if response.status_code == 404:
        raise RuntimeError("recording not available yet")
    source = workdir / "source.mp4"
    with requests.get(response.json()["url"], stream=True, timeout=600) as download:
        download.raise_for_status()
        with open(source, "wb") as out:
            for chunk in download.iter_content(chunk_size=4 * 1024 * 1024):
                out.write(chunk)
    if source.stat().st_size == 0:
        # Attendee uploads an empty file when the bot left before capturing anything.
        shutil.rmtree(workdir, ignore_errors=True)
        update(bot_id, stage="no_recording", error="registrazione vuota")
        log(bot_id, "Attendee recording is empty, nothing to import")
        return
    result, thumb, meta = cap.prepare(source, workdir)
    source.unlink(missing_ok=True)
    log(bot_id, f"recording {meta['duration']:.0f}s {meta['width']}x{meta['height']} audio={meta['has_audio']}, {result.stat().st_size / 1e6:.1f} MB")
    video_id = row["video_id"]
    if not video_id:
        video_id = cap.create_video(video_name(row["title"], row["created_at"]), meta)
        update(bot_id, video_id=video_id)
        log(bot_id, f"created Cap video {video_id}")
    upload(video_id, result, thumb, meta)
    update(bot_id, stage="imported", error=None)
    shutil.rmtree(workdir, ignore_errors=True)
    log(bot_id, f"imported as {PUBLIC_URL}/s/{video_id} (public={PUBLIC_DEFAULT})")
    if DELETE_ATTENDEE_DATA:
        try:
            attendee("POST", f"/bots/{bot_id}/delete_data")
        except Exception as error:
            log(bot_id, f"could not delete Attendee copy: {error}")


def step(row: sqlite3.Row) -> None:
    bot_id = row["bot_id"]
    response = attendee("GET", f"/bots/{bot_id}")
    if response.status_code == 404:
        update(bot_id, stage="failed", error="bot non trovato in Attendee")
        return
    bot = response.json()
    state = bot.get("state")
    if state != row["attendee_state"]:
        log(bot_id, f"state {row['attendee_state']} -> {state} (recording {bot.get('recording_state')})")
        update(bot_id, attendee_state=state)
    if state not in ATTENDEE_DONE:
        if time.time() - row["created_at"] > MAX_UPTIME_SECONDS + 3600:
            attendee("POST", f"/bots/{bot_id}/leave")
            update(bot_id, stage="failed", error=f"bloccato in stato {state}, fatto uscire")
        return
    joined = any(event.get("type") == "joined_meeting" for event in bot.get("events") or [])
    if not joined or (state == "fatal_error" and bot.get("recording_state") != "complete"):
        # A bot that never got in still reports its (empty) recording as complete.
        update(bot_id, stage="failed", error=failure_reason(bot))
        return
    if state == "data_deleted" or bot.get("recording_state") not in ("complete", "in_progress"):
        update(bot_id, stage="no_recording", error=failure_reason(bot))
        return
    if row["stage"] != "importing":
        update(bot_id, stage="importing")
    import_recording(rows("bot_id = ?", (bot_id,))[0])


def fail(row: sqlite3.Row, error: Exception) -> None:
    attempts = row["attempts"] + 1
    message = f"{type(error).__name__}: {str(error)[:500]}"
    if attempts >= MAX_ATTEMPTS:
        update(row["bot_id"], stage="failed", attempts=attempts, error=message)
        log(row["bot_id"], f"giving up after {attempts} attempts: {message}")
        return
    delay = min(60 * 2 ** (attempts - 1), 3 * 3600)
    update(row["bot_id"], attempts=attempts, next_attempt_at=time.time() + delay, error=message)
    log(row["bot_id"], f"attempt {attempts} failed, retry in {delay}s: {message}")


def poll_loop() -> None:
    while running:
        try:
            discover()
            for row in rows("stage IN ('active', 'importing') AND next_attempt_at <= ?", (time.time(),)):
                try:
                    step(row)
                except Exception as error:
                    traceback.print_exc()
                    fail(rows("bot_id = ?", (row["bot_id"],))[0], error)
        except Exception:
            traceback.print_exc()
        (STATE_DIR / "heartbeat").touch()
        for _ in range(POLL_SECONDS):
            if not running:
                return
            time.sleep(1)


sync_lock = threading.Lock()


def setting(key: str, default: str = "") -> str:
    with db_lock:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_lock:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()


def autojoin() -> bool:
    return setting("autojoin", "1") == "1"


def ical_url() -> str:
    try:
        return ICAL_FILE.read_text().strip()
    except FileNotFoundError:
        return ""


def save_ical_url(value: str) -> None:
    value = value.strip()
    if value.startswith("webcal://"):
        value = "https://" + value[len("webcal://"):]
    parts = urlsplit(value)
    schemes = ("https", "http") if CALENDAR_ALLOW_HTTP else ("https",)
    if parts.scheme not in schemes or not parts.netloc or len(value) > 2000:
        raise ValueError("Indirizzo non valido: incolla l'indirizzo segreto in formato iCal (https://calendar.google.com/…/basic.ics).")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(ICAL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(value)
    os.chmod(ICAL_FILE, 0o600)
    set_setting("sync_error", "")
    set_setting("synced_at", "0")


def forget_calendar() -> None:
    ICAL_FILE.unlink(missing_ok=True)
    with db_lock:
        conn.execute("DELETE FROM calendar_events WHERE status IN ('pending', 'gone', 'missed')")
        conn.execute("DELETE FROM settings WHERE key IN ('sync_error', 'synced_at')")
        conn.commit()


def fetch_ics(url: str) -> bytes:
    # The URL is the secret: errors are reduced to type and status so it never reaches logs or the page.
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Google Calendar ha risposto HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=256 * 1024):
                body.extend(chunk)
                if len(body) > ICS_MAX_BYTES:
                    raise RuntimeError("calendario troppo grande")
            return bytes(body)
    except requests.RequestException as error:
        raise RuntimeError(f"calendario non raggiungibile ({type(error).__name__})") from None


def calendar_sync() -> str | None:
    """Returns an error message, or None when the feed was read and applied."""
    url = ical_url()
    if not url:
        return None
    with sync_lock:
        now = time.time()
        try:
            occurrences = meetcal.parse(fetch_ics(url), now, owners=[OWNER_EMAIL], local_tz=LOCAL_TZ)
        except Exception as error:
            message = str(error).replace(url, "<ical>")[:200] if isinstance(error, RuntimeError) else f"calendario illeggibile ({type(error).__name__})"
            set_setting("sync_error", message)
            set_setting("synced_at", str(now))
            log(None, f"calendar sync failed: {message}")
            return message
        meetcal.reconcile(conn, db_lock, occurrences, now)
        set_setting("sync_error", "")
        set_setting("synced_at", str(now))
        return None


def launch_for_calendar(meeting_url: str, title: str) -> str:
    for row in active_bots():
        if row["meeting_url"] == meeting_url:
            return row["bot_id"]
    return launch(meeting_url, title)


def calendar_loop() -> None:
    while running:
        try:
            if ical_url():
                if time.time() - float(setting("synced_at", "0") or 0) >= CALENDAR_SYNC_SECONDS:
                    calendar_sync()
                meetcal.run_due(conn, db_lock, time.time(), autojoin(), launch_for_calendar, log)
        except Exception:
            traceback.print_exc()
        for _ in range(CALENDAR_TICK_SECONDS):
            if not running:
                return
            time.sleep(1)


def status_text(row: sqlite3.Row) -> str:
    if row["stage"] == "active":
        return STATE_LABELS.get(row["attendee_state"] or "", row["attendee_state"] or "in avvio")
    return STAGE_LABELS.get(row["stage"], row["stage"])


def calendar_status(row: sqlite3.Row, on: bool) -> str:
    if row["status"] == "launched":
        bot = rows("bot_id = ?", (row["bot_id"],)) if row["bot_id"] else []
        return f"bot inviato · {status_text(bot[0])}" if bot else "bot inviato"
    if row["status"] == "missed":
        return "saltato"
    if row["status"] == "failed":
        return "non è riuscito a mandare il bot"
    if not on:
        return "ingresso automatico spento"
    if not row["enabled"]:
        return "il bot non entra"
    return "il bot entra da solo un minuto prima"


def calendar_section() -> str:
    now = time.time()
    on = autojoin()
    configured = bool(ical_url())
    items = []
    for row in meetcal.upcoming(conn, db_lock, now):
        toggle = ""
        if row["status"] == "pending":
            label, cls = ("ON", "on") if row["enabled"] else ("OFF", "ghost")
            toggle = (f'<form method="post" action="{BASE_PATH}/calendar/toggle/{row["id"]}">'
                      f'<button class="{cls}" title="Bot per questa riunione">{label}</button></form>')
        error = f'<div class="err">{html.escape(row["error"])}</div>' if row["error"] else ""
        items.append(
            f'<li><div><b>{html.escape(row["title"] or "")}</b>'
            f'<div class="meta">{meetcal.day_label(row["start"], now, LOCAL_TZ)} · {html.escape(calendar_status(row, on))}</div>{error}</div>{toggle}</li>'
        )
    if configured:
        synced = float(setting("synced_at", "0") or 0)
        sync_error = setting("sync_error")
        when = datetime.fromtimestamp(synced, LOCAL_TZ).strftime("%H:%M") if synced else "mai"
        sync_line = (f'<p class="err">Ultima lettura del calendario ({when}): {html.escape(sync_error)}</p>' if sync_error
                     else f'<p class="meta">Calendario letto alle {when}, si aggiorna ogni 5 minuti.</p>')
        empty = '<li class="meta">Nessun Meet nelle prossime 24 ore.</li>'
        switch_label, switch_cls = ("Ingresso automatico: ON", "on") if on else ("Ingresso automatico: OFF", "ghost")
        body = f"""<form method="post" action="{BASE_PATH}/calendar/autojoin"><button class="{switch_cls}">{switch_label}</button></form>
{sync_line}<ul>{"".join(items) or empty}</ul>"""
        settings_form = f"""<p class="meta">Indirizzo iCal salvato: https://calendar.google.com/••••••••</p>
<form method="post" action="{BASE_PATH}/calendar/settings"><input name="ical_url" type="password" required autocomplete="off" placeholder="Nuovo indirizzo segreto in formato iCal"><button>Sostituisci</button></form>
<form method="post" action="{BASE_PATH}/calendar/sync"><button class="ghost">Rileggi ora</button></form>
<form method="post" action="{BASE_PATH}/calendar/forget"><button class="ghost">Scollega il calendario</button></form>"""
    else:
        body = '<p class="meta">Collega Google Calendar e il bot entra da solo in ogni Meet in agenda.</p>'
        settings_form = f"""<form method="post" action="{BASE_PATH}/calendar/settings"><input name="ical_url" type="password" required autocomplete="off" placeholder="Indirizzo segreto in formato iCal">
<button>Collega</button></form>
<p class="meta">Google Calendar → Impostazioni → il tuo calendario → Integra calendario → Indirizzo segreto in formato iCal.</p>"""
    return f"""<h2>Riunioni dal calendario</h2>{body}
<details{"" if configured else " open"}><summary>Collegamento a Google Calendar</summary>{settings_form}
<p class="meta">Il bot salta le riunioni rifiutate, quelle tutto il giorno e quelle con #nobot nel titolo o nella descrizione.</p></details>"""


def calendar_json() -> dict:
    now = time.time()
    on = autojoin()
    return {
        "configured": bool(ical_url()),
        "autojoin": on,
        "synced_at": float(setting("synced_at", "0") or 0) or None,
        "sync_error": setting("sync_error") or None,
        "events": [
            {"id": row["id"], "title": row["title"], "start": row["start"], "end": row["end"], "enabled": bool(row["enabled"]),
             "status": row["status"], "bot_id": row["bot_id"], "error": row["error"], "label": calendar_status(row, on)}
            for row in meetcal.upcoming(conn, db_lock, now)
        ],
    }


def calendar_action(action: str, fields: dict) -> tuple[str, bool, dict]:
    if action == "settings":
        save_ical_url(fields.get("ical_url") or "")
        error = calendar_sync()
        if error:
            return f"Indirizzo salvato, ma il calendario non si legge: {error}", False, {"ok": False, "error": error}
        return "Calendario collegato.", True, {"ok": True}
    if action == "forget":
        forget_calendar()
        return "Calendario scollegato.", True, {"ok": True}
    if action == "sync":
        error = calendar_sync()
        return (error, False, {"ok": False, "error": error}) if error else ("Calendario riletto.", True, {"ok": True})
    if action == "autojoin":
        value = fields.get("enabled")
        enabled = (str(value).lower() in ("1", "true", "on")) if value is not None else not autojoin()
        set_setting("autojoin", "1" if enabled else "0")
        message = "Ingresso automatico acceso." if enabled else "Ingresso automatico spento: nessun bot partirà dal calendario."
        return message, True, {"ok": True, "autojoin": enabled}
    if action.startswith("toggle/"):
        enabled = meetcal.toggle(conn, db_lock, action.split("/", 1)[1])
        if enabled is None:
            raise ValueError("Riunione non trovata.")
        return ("Il bot entrerà in questa riunione." if enabled else "Il bot non entrerà in questa riunione."), True, {"ok": True, "enabled": enabled}
    raise ValueError("Azione sconosciuta.")


def render(message: str = "", ok: bool = True) -> bytes:
    items = []
    for row in rows(limit=15):
        when = datetime.fromtimestamp(row["created_at"], LOCAL_TZ).strftime("%d/%m %H:%M")
        link = f' · <a href="{PUBLIC_URL}/s/{row["video_id"]}">apri video</a>' if row["stage"] == "imported" else ""
        leave = (f'<form method="post" action="{BASE_PATH}/leave/{html.escape(row["bot_id"])}">'
                 f'<button class="ghost">Fai uscire</button></form>') if row["stage"] == "active" else ""
        error = f'<div class="err">{html.escape(row["error"])}</div>' if row["error"] and row["stage"] != "imported" else ""
        items.append(
            f'<li><div><b>{html.escape(row["title"] or row["meeting_url"])}</b>'
            f'<div class="meta">{when} · {html.escape(status_text(row))}{link}</div>{error}</div>{leave}</li>'
        )
    flash = f'<p class="{"ok" if ok else "err"}">{html.escape(message)}</p>' if message else ""
    page = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Meet bot</title>
<meta http-equiv="refresh" content="30;url={BASE_PATH}/">
<style>body{{font:16px system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px;color:#111;background:#fafafa}}
input,button{{font:inherit;padding:12px;border-radius:10px;border:1px solid #ccc;width:100%;box-sizing:border-box;margin:6px 0}}
button{{background:#111;color:#fff;border:0;cursor:pointer}} .ghost{{background:#eee;color:#111;width:auto;padding:8px 12px}}
ul{{list-style:none;padding:0}} li{{display:flex;gap:12px;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #e5e5e5}}
li b{{word-break:break-all}} .meta{{color:#666;font-size:14px}} .err{{color:#b00020;font-size:14px}} .ok{{color:#0a7a32}}
h2{{font-size:20px;margin:28px 0 4px}} .on{{background:#0a7a32;color:#fff;width:auto;padding:8px 12px}} summary{{cursor:pointer;color:#666;margin:12px 0}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}} li{{border-color:#333}} .meta{{color:#aaa}} input{{background:#222;color:#eee;border-color:#444}} button{{background:#eee;color:#111}} .ghost{{background:#333;color:#eee}} .on{{background:#1f9d55;color:#fff}} summary{{color:#aaa}}}}</style>
</head><body><h1>Registra un Meet</h1>{flash}
<form method="post" action="{BASE_PATH}/join">
<input name="meeting_url" type="url" required placeholder="https://meet.google.com/abc-defg-hij" autocomplete="off">
<input name="title" placeholder="Titolo (facoltativo)" maxlength="120">
<button>Manda il bot</button></form>
<p class="meta">Il bot entra come ospite: ammettilo dal Meet quando chiede di partecipare. A fine riunione il video compare in Bizstudio Recorder.</p>
{calendar_section()}
<h2>Ultimi bot</h2>
<ul>{"".join(items) or '<li class="meta">Nessun bot ancora.</li>'}</ul></body></html>"""
    return page.encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def auth(self) -> str:
        """'token' (API bearer), 'session' (owner's Bizstudio Recorder login), 'anonymous' or 'forbidden'."""
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            ok = bool(API_TOKEN) and self.route().startswith("/api/") and hmac.compare_digest(header[7:], API_TOKEN)
            return "token" if ok else "forbidden"
        cookie = self.headers.get("Cookie", "")
        if "next-auth.session-token" not in cookie:
            return "anonymous"
        # cap-web decodes the NextAuth JWT and rejects revoked sessions
        # (authSessionVersion) and blocked accounts, so it stays the only judge.
        response = requests.get(f"{CAP_URL}/api/auth/session", headers={"Cookie": cookie}, timeout=10)
        response.raise_for_status()
        user = (response.json() or {}).get("user") or {}
        if not user:
            return "anonymous"
        owner = user.get("id") == OWNER_ID or (user.get("email") or "").lower() == OWNER_EMAIL
        return "session" if owner else "forbidden"

    def same_origin(self) -> bool:
        # Cookie-authenticated POSTs must come from our own page; bearer clients are exempt.
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin == PUBLIC_URL
        return self.headers.get("Sec-Fetch-Site") == "same-origin"

    def send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Embedded by the dashboard's Meet Bot page, which shares this origin.
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def gate(self) -> str | None:
        """Returns the auth kind, or None after answering the request itself."""
        try:
            kind = self.auth()
        except Exception as error:
            log(None, f"session check failed: {error}")
            self.send(503, b"Bizstudio Recorder non risponde, riprova tra poco.", "text/plain; charset=utf-8")
            return None
        api = self.route().startswith("/api/")
        if kind == "anonymous":
            if api:
                self.send(401, b'{"ok": false, "error": "unauthorized"}', "application/json")
            else:
                self.send(302, b"", "text/plain", {"Location": f"{PUBLIC_URL}/login?next={BASE_PATH}/"})
            return None
        if kind == "forbidden":
            self.send(403, "Accesso riservato al proprietario.".encode(), "text/plain; charset=utf-8")
            return None
        return kind

    def route(self) -> str:
        path = urlsplit(self.path).path
        return path[len(BASE_PATH):] if path.startswith(BASE_PATH) else path

    def do_GET(self):
        route = self.route()
        if route == "/health":
            return self.send(200, b"ok", "text/plain")
        if not self.gate():
            return
        if route in ("", "/"):
            return self.send(200, render())
        if route == "/api/bots":
            data = [dict(row) for row in rows(limit=50)]
            return self.send(200, json.dumps(data).encode(), "application/json")
        if route == "/api/calendar":
            return self.send(200, json.dumps(calendar_json()).encode(), "application/json")
        self.send(404, b"not found", "text/plain")

    def do_POST(self):
        kind = self.gate()
        if not kind:
            return
        if kind == "session" and not self.same_origin():
            return self.send(403, b"cross-origin request refused", "text/plain")
        route = self.route()
        length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        is_json = (self.headers.get("Content-Type") or "").startswith("application/json")
        try:
            fields = json.loads(raw or "{}") if is_json else {k: v[0] for k, v in parse_qs(raw).items()}
        except ValueError:
            return self.send(400, b"invalid json", "text/plain")
        try:
            if route in ("/join", "/api/join"):
                title = (fields.get("title") or "").strip()[:120] or None
                bot_id = launch(fields.get("meeting_url") or "", title)
                message, ok = "Bot inviato: tra poco chiede di entrare nel Meet, ammettilo.", True
                result = {"ok": True, "bot_id": bot_id}
            elif route.startswith(("/calendar/", "/api/calendar/")):
                message, ok, result = calendar_action(route.split("/calendar/", 1)[1], fields)
            elif route.startswith("/leave/") or route.startswith("/api/leave/"):
                bot_id = route.rsplit("/", 1)[1]
                if not rows("bot_id = ?", (bot_id,)):
                    return self.send(404, b"not found", "text/plain")
                attendee("POST", f"/bots/{bot_id}/leave")
                message, ok, result = "Il bot sta uscendo, poi salva la registrazione.", True, {"ok": True}
            else:
                return self.send(404, b"not found", "text/plain")
        except ValueError as error:
            message, ok, result = str(error), False, {"ok": False, "error": str(error)}
        except Exception as error:
            traceback.print_exc()
            message, ok, result = f"Errore: {error}", False, {"ok": False, "error": str(error)}
        if route.startswith("/api/"):
            return self.send(200 if ok else 400, json.dumps(result).encode(), "application/json")
        self.send(200 if ok else 400, render(message, ok))


def stop(*_):
    global running
    running = False
    threading.Thread(target=server.shutdown, daemon=True).start()


def import_file(path: str, title: str | None) -> None:
    """Manual import for tests and recovery: python bridge.py import-file <mp4> [title]."""
    workdir = STATE_DIR / "work" / f"manual-{int(time.time())}"
    workdir.mkdir(parents=True, exist_ok=True)
    result, thumb, meta = cap.prepare(Path(path), workdir)
    video_id = cap.create_video(video_name(title, time.time()), meta)
    upload(video_id, result, thumb, meta)
    shutil.rmtree(workdir, ignore_errors=True)
    print(json.dumps({"video_id": video_id, "url": f"{PUBLIC_URL}/s/{video_id}", "public": PUBLIC_DEFAULT, **meta}))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "import-file":
        import_file(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        sys.exit(0)
    conn = connect()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=calendar_loop, daemon=True).start()
    log(None, f"meet-bridge listening on :8080{BASE_PATH} (poll={POLL_SECONDS}s, max uptime={MAX_UPTIME_SECONDS}s)")
    server.serve_forever()
    log(None, "meet-bridge stopped")
