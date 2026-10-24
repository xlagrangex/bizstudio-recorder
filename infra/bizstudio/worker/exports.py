"""Download formats the web app asks for, rendered from a video's result.mp4.

The web app drops `export-queue/<videoId>--<format>.json` into the bucket; this
loop renders `<owner>/<video>/exports/<file>`, stamps it with the source's ETag
so the web app can tell a stale export after an edit, and removes the marker.
"""

import json
import re
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from botocore.exceptions import ClientError

from transcribe import has_audio_stream, probe_duration, run

QUEUE_PREFIX = "export-queue/"
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
GIF_SECONDS = 30
TIMEOUT = 4 * 3600


def scale_to(height: int) -> str:
    return f"scale=-2:'min({height},ih)':flags=lanczos,format=yuv420p"


def h264(height: int) -> list[str]:
    return ["-vf", scale_to(height), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


FORMATS: dict[str, dict] = {
    "mp4-1080": {"file": "video-1080p.mp4", "type": "video/mp4", "args": h264(1080), "audio": False},
    "mp4-720": {"file": "video-720p.mp4", "type": "video/mp4", "args": h264(720), "audio": False},
    "webm": {
        "file": "video.webm", "type": "video/webm", "audio": False,
        "args": ["-vf", scale_to(1080), "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                 "-row-mt", "1", "-b:v", "0", "-crf", "34", "-c:a", "libopus", "-b:a", "128k"],
    },
    "mp3": {"file": "audio.mp3", "type": "audio/mpeg", "audio": True,
            "args": ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]},
    "wav": {"file": "audio.wav", "type": "audio/wav", "audio": True,
            "args": ["-vn", "-c:a", "pcm_s16le", "-ar", "48000"]},
    "gif": {"file": "clip.gif", "type": "image/gif", "audio": False, "args": None},
}


def parse_marker(key: str) -> tuple[str, str] | None:
    name = key[len(QUEUE_PREFIX):]
    if not name.endswith(".json") or "--" not in name:
        return None
    video_id, export_format = name[: -len(".json")].rsplit("--", 1)
    if not ID.match(video_id) or export_format not in FORMATS:
        return None
    return video_id, export_format


def ffmpeg_args(export_format: str, source: Path, target: Path) -> list[str]:
    spec = FORMATS[export_format]
    if spec["args"] is None:
        length = min(GIF_SECONDS, probe_duration(source))
        graph = (
            "fps=12,scale='min(640,iw)':-2:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"
        )
        return ["ffmpeg", "-y", "-v", "error", "-t", f"{length}", "-i", str(source),
                "-an", "-filter_complex", graph, "-loop", "0", str(target)]
    return ["ffmpeg", "-y", "-v", "error", "-i", str(source), *spec["args"], str(target)]


def source_etag(client, bucket: str, key: str) -> str:
    try:
        return client.head_object(Bucket=bucket, Key=key)["ETag"].strip('"')
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise ValueError("the video file isn't ready yet") from None
        raise


def export_etag(client, bucket: str, key: str) -> str | None:
    try:
        return client.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("source-etag")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def render(client, bucket: str, owner_id: str, video_id: str, export_format: str, log) -> None:
    spec = FORMATS[export_format]
    prefix = f"{owner_id}/{video_id}"
    source_key = f"{prefix}/result.mp4"
    target_key = f"{prefix}/exports/{spec['file']}"
    etag = source_etag(client, bucket, source_key)
    if export_etag(client, bucket, target_key) == etag:
        return
    workdir = Path(tempfile.mkdtemp(prefix=f"export-{video_id}-"))
    try:
        source = workdir / "source.mp4"
        client.download_file(bucket, source_key, str(source))
        if spec["audio"] and not has_audio_stream(source):
            raise ValueError("this video has no audio track")
        target = workdir / spec["file"]
        started = time.monotonic()
        run(ffmpeg_args(export_format, source, target), timeout=TIMEOUT)
        client.upload_file(
            str(target), bucket, target_key,
            ExtraArgs={"ContentType": spec["type"], "Metadata": {"source-etag": etag}},
        )
        log(video_id, f"export {export_format} ready in {time.monotonic() - started:.0f}s")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def owner_of(client, bucket: str, key: str) -> str | None:
    body = json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read())
    owner = body.get("ownerId") if isinstance(body, dict) else None
    return owner if isinstance(owner, str) and ID.match(owner) else None


def drain(client, bucket: str, log) -> int:
    listing = client.list_objects_v2(Bucket=bucket, Prefix=QUEUE_PREFIX)
    markers = sorted(listing.get("Contents", []), key=lambda item: item["LastModified"])
    for marker in markers:
        key = marker["Key"]
        parsed = parse_marker(key)
        owner = owner_of(client, bucket, key) if parsed else None
        if parsed and owner:
            video_id, export_format = parsed
            error_key = f"{owner}/{video_id}/exports/{export_format}.error"
            client.delete_object(Bucket=bucket, Key=error_key)
            try:
                render(client, bucket, owner, video_id, export_format, log)
            except Exception as error:
                traceback.print_exc()
                message = str(error)[:300] or type(error).__name__
                log(video_id, f"export {export_format} failed: {message}")
                client.put_object(Bucket=bucket, Key=error_key, Body=message.encode(), ContentType="text/plain")
        else:
            log(None, f"dropping malformed export marker {key}")
        client.delete_object(Bucket=bucket, Key=key)
    return len(markers)


def loop(make_client, bucket: str, log, is_running, poll_seconds: int = 5) -> None:
    client = make_client()
    while is_running():
        try:
            drain(client, bucket, log)
        except Exception:
            traceback.print_exc()
            client = make_client()
        for _ in range(poll_seconds):
            if not is_running():
                return
            time.sleep(1)
