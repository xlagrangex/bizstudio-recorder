import json
import os
import re
import signal
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
import pymysql
from botocore.config import Config
from botocore.exceptions import ClientError

import brain
from transcribe import NoAudio, enrich, to_vtt, transcribe

STATE_DIR = Path(os.environ.get("STATE_DIR", "/state"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
SETTLE_SECONDS = int(os.environ.get("SETTLE_SECONDS", "60"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "8"))
BUCKET = os.environ.get("S3_BUCKET", "cap")
BRAIN_ENABLED = os.environ.get("BRAIN_ENABLED", "true") == "true"
ONLY_VIDEO_IDS = {v for v in os.environ.get("ONLY_VIDEO_IDS", "").split(",") if v}
LOCAL_TZ = ZoneInfo(os.environ.get("TZ_NOTES", "Europe/Rome"))
PROCESSABLE_SOURCES = {"desktopMP4", "webMP4", "desktopSegments"}
UNTOUCHED_STATUSES = {"COMPLETE", "SKIPPED", "NO_AUDIO", "ERROR"}
DEFAULT_NAME = re.compile(
    r"^(?:(?:Cap|Bizstudio(?: Recorder)?) (?:Recording|Screenshot) - .+|My Video|Untitled(?: Video)?)$"
)
TERMINAL = {"done", "no_audio", "skipped", "failed"}

running = True
last_skip: dict[str, str] = {}


class Permanent(Exception):
    pass


def log(video_id: str | None, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {video_id or '-'} {message}", flush=True)


def state_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DIR / "worker.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            video_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_error TEXT,
            title TEXT,
            note_path TEXT,
            note_commit TEXT,
            cost_usd REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )"""
    )
    conn.commit()
    return conn


def set_job(conn: sqlite3.Connection, video_id: str, **fields) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (video_id, stage, created_at, updated_at) VALUES (?, 'pending', ?, ?) ON CONFLICT(video_id) DO NOTHING",
        (video_id, now, now),
    )
    if fields:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE jobs SET {assignments}, updated_at = ? WHERE video_id = ?", (*fields.values(), now, video_id))
    conn.commit()


def mysql() -> pymysql.Connection:
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "mysql"),
        user=os.environ.get("MYSQL_USER", "cap"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "cap"),
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
        read_timeout=60,
        write_timeout=60,
        cursorclass=pymysql.cursors.DictCursor,
    )


def s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT", "http://minio:9000"),
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5, "mode": "standard"}),
    )


def object_exists(client, key: str) -> bool:
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def as_json(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_videos(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT v.id, v.ownerId, v.name, v.bucket, v.storageIntegrationId, v.source, v.metadata,
                      v.settings, v.transcriptionStatus, v.duration, v.createdAt, v.updatedAt,
                      u.phase AS uploadPhase, u.raw_file_key AS rawFileKey
               FROM videos v LEFT JOIN video_uploads u ON u.video_id = v.id
               WHERE v.isScreenshot = 0
               ORDER BY v.createdAt"""
        )
        rows = cur.fetchall()
    for row in rows:
        row["source"] = as_json(row["source"])
        row["metadata"] = as_json(row["metadata"])
        row["settings"] = as_json(row["settings"])
    return rows


def wait_reason(video: dict) -> str | None:
    if video["uploadPhase"] not in (None, "complete"):
        return f"upload phase {video['uploadPhase']}"
    if video["metadata"].get("editProcessing"):
        return "edit in progress"
    updated = video["updatedAt"].replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - updated).total_seconds() < SETTLE_SECONDS:
        return f"changed less than {SETTLE_SECONDS}s ago"
    return None


def fetch_source(client, video: dict, workdir: Path) -> Path:
    prefix = f"{video['ownerId']}/{video['id']}"
    for key in [f"{prefix}/result.mp4", video.get("rawFileKey")]:
        if key and object_exists(client, key):
            target = workdir / "source.mp4"
            client.download_file(BUCKET, key, str(target))
            return target
    manifest_key = f"{prefix}/segments/manifest.json"
    if not object_exists(client, manifest_key):
        raise RuntimeError("no result.mp4, raw file or segment manifest yet")
    manifest = json.loads(client.get_object(Bucket=BUCKET, Key=manifest_key)["Body"].read())
    if not manifest.get("is_complete"):
        raise RuntimeError("segment manifest not complete yet")
    entries = manifest.get("audio_segments") or []
    if not entries:
        raise NoAudio("segment manifest has no audio segments")
    indexes = sorted({int(e["index"] if isinstance(e, dict) else e) for e in entries})
    target = workdir / "source.m4a"
    with open(target, "wb") as out:
        out.write(client.get_object(Bucket=BUCKET, Key=f"{prefix}/segments/audio/init.mp4")["Body"].read())
        for index in indexes:
            key = f"{prefix}/segments/audio/segment_{index:03d}.m4s"
            out.write(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    return target


def set_status(db, video_id: str, status: str | None, only_if: str | None = None) -> None:
    with db.cursor() as cur:
        if only_if is None:
            cur.execute("UPDATE videos SET transcriptionStatus = %s WHERE id = %s", (status, video_id))
        else:
            cur.execute(
                "UPDATE videos SET transcriptionStatus = %s WHERE id = %s AND transcriptionStatus <=> %s",
                (status, video_id, only_if),
            )


def stage_transcribe(db, client, video: dict, workdir: Path) -> dict:
    cached = workdir / "transcript.json"
    if cached.exists():
        return json.loads(cached.read_text())
    set_status(db, video["id"], "PROCESSING")
    source = fetch_source(client, video, workdir)
    started = time.monotonic()
    result = transcribe(source, workdir)
    result["elapsed"] = round(time.monotonic() - started, 1)
    vtt = to_vtt(result["segments"])
    client.put_object(
        Bucket=BUCKET,
        Key=f"{video['ownerId']}/{video['id']}/transcription.vtt",
        Body=vtt.encode(),
        ContentType="text/vtt",
    )
    set_status(db, video["id"], "COMPLETE")
    cached.write_text(json.dumps(result, ensure_ascii=False))
    return result


def stage_enrich(db, video: dict, transcript: dict, workdir: Path) -> dict:
    cached = workdir / "ai.json"
    if cached.exists():
        return json.loads(cached.read_text())
    ai = enrich(transcript["segments"], transcript["duration"])
    with db.cursor() as cur:
        cur.execute(
            """UPDATE videos SET metadata = JSON_SET(COALESCE(metadata, JSON_OBJECT()),
                   '$.aiTitle', %s, '$.summary', %s, '$.chapters', CAST(%s AS JSON), '$.aiGenerationStatus', 'COMPLETE')
               WHERE id = %s""",
            (ai["title"], ai["summary"], json.dumps(ai["chapters"], ensure_ascii=False), video["id"]),
        )
        renamed = False
        name = video["name"] or ""
        ours = name == video["metadata"].get("aiTitle")
        if (DEFAULT_NAME.match(name) or ours) and not video["metadata"].get("titleManuallyEdited"):
            renamed = cur.execute("UPDATE videos SET name = %s WHERE id = %s AND name = %s", (ai["title"], video["id"], video["name"])) > 0
    ai["renamed"] = renamed
    cached.write_text(json.dumps(ai, ensure_ascii=False))
    return ai


def drop_media(workdir: Path) -> None:
    for leftover in [*workdir.glob("source.*"), *workdir.glob("*.mp3")]:
        leftover.unlink(missing_ok=True)


def process(conn, db, client, video: dict, job: sqlite3.Row | None) -> None:
    video_id = video["id"]
    workdir = STATE_DIR / "videos" / video_id
    workdir.mkdir(parents=True, exist_ok=True)
    set_job(conn, video_id)
    if video["settings"].get("disableTranscript"):
        set_status(db, video_id, "SKIPPED", only_if=video["transcriptionStatus"])
        set_job(conn, video_id, stage="skipped", last_error=None)
        log(video_id, "transcription disabled in video settings, skipped")
        return
    if video["bucket"] or video["storageIntegrationId"]:
        raise Permanent("custom bucket / storage integration not supported")

    try:
        transcript = stage_transcribe(db, client, video, workdir)
    except NoAudio as reason:
        set_status(db, video_id, "NO_AUDIO")
        set_job(conn, video_id, stage="no_audio", last_error=str(reason))
        drop_media(workdir)
        log(video_id, f"no audio: {reason}")
        return
    set_job(conn, video_id, stage="transcribed", cost_usd=transcript["cost"])
    log(video_id, f"transcribed {transcript['duration']:.0f}s audio ({transcript.get('speech_seconds', '?')}s speech) in {transcript.get('elapsed', 0)}s, "
                  f"{len(transcript['segments'])} segments, ${transcript['cost']:.4f} via {','.join(transcript['models'])}")

    ai = stage_enrich(db, video, transcript, workdir)
    set_job(conn, video_id, stage="enriched", title=ai["title"], cost_usd=transcript["cost"] + ai["cost"])
    log(video_id, f"title/summary saved (renamed={ai['renamed']}): {ai['title']}")

    if BRAIN_ENABLED:
        date = video["createdAt"].replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        path, commit = brain.publish_note(video, transcript, ai, date)
        set_job(conn, video_id, note_path=path, note_commit=commit)
        log(video_id, f"brain note {path} at {commit[:10]}")
    set_job(conn, video_id, stage="done", last_error=None)
    drop_media(workdir)


def fail(conn, db, video: dict, job: sqlite3.Row | None, error: Exception) -> None:
    attempts = (job["attempts"] if job else 0) + 1
    message = f"{type(error).__name__}: {str(error)[:800]}"
    if isinstance(error, Permanent) or attempts >= MAX_ATTEMPTS:
        set_job(conn, video["id"], stage="failed", attempts=attempts, last_error=message)
        drop_media(STATE_DIR / "videos" / video["id"])
        set_status(db, video["id"], "ERROR", only_if="PROCESSING")
        log(video["id"], f"giving up after {attempts} attempts: {message}")
        return
    delay = min(60 * 2 ** (attempts - 1), 6 * 3600)
    set_job(conn, video["id"], attempts=attempts, next_attempt_at=time.time() + delay, last_error=message)
    log(video["id"], f"attempt {attempts} failed, retry in {delay}s: {message}")


def skip_reason(video: dict, job: sqlite3.Row | None) -> str | None:
    if job and job["stage"] in TERMINAL:
        return f"already {job['stage']}"
    if not job and video["transcriptionStatus"] in UNTOUCHED_STATUSES:
        return f"transcriptionStatus {video['transcriptionStatus']} set outside the worker"
    if video["source"].get("type") not in PROCESSABLE_SOURCES:
        return f"unsupported source {video['source'].get('type')}"
    if job and job["next_attempt_at"] > time.time():
        return f"retry backoff (attempt {job['attempts']})"
    waiting = wait_reason(video)
    return f"waiting: {waiting}" if waiting else None


def tick(conn) -> None:
    db = mysql()
    try:
        client = s3()
        jobs = {row["video_id"]: row for row in conn.execute("SELECT * FROM jobs")}
        for video in load_videos(db):
            if not running:
                return
            if ONLY_VIDEO_IDS and video["id"] not in ONLY_VIDEO_IDS:
                continue
            job = jobs.get(video["id"])
            reason = skip_reason(video, job)
            if reason:
                if last_skip.get(video["id"]) != reason:
                    log(video["id"], f"skip: {reason}")
                    last_skip[video["id"]] = reason
                continue
            last_skip.pop(video["id"], None)
            log(video["id"], f"processing ({video['source'].get('type')}, status={video['transcriptionStatus']}, stage={job['stage'] if job else 'new'})")
            try:
                process(conn, db, client, video, job)
            except Exception as error:
                traceback.print_exc()
                fail(conn, db, video, job, error)
    finally:
        db.close()


def stop(*_):
    global running
    running = False


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not os.environ.get("OPENROUTER_API_KEY"):
        log(None, "OPENROUTER_API_KEY missing; idling")
    conn = state_db()
    log(None, f"worker started (poll={POLL_SECONDS}s, brain={'on' if BRAIN_ENABLED else 'off'} branch={brain.BRANCH})")
    while running:
        try:
            if os.environ.get("OPENROUTER_API_KEY"):
                tick(conn)
        except Exception:
            traceback.print_exc()
        (STATE_DIR / "heartbeat").touch()
        for _ in range(POLL_SECONDS):
            if not running:
                break
            time.sleep(1)
    log(None, "worker stopped")


if __name__ == "__main__":
    main()
