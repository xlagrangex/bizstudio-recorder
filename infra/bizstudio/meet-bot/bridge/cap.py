"""Imports a finished mp4 into Cap through the same API calls the desktop app makes:
create (desktopMP4) -> multipart initiate / presign-part / PUT / complete -> thumbnail PUT."""

import json
import math
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

CAP_URL = os.environ.get("CAP_URL", "http://cap-web:3000").rstrip("/")
CAP_API_KEY = os.environ.get("CAP_API_KEY", "")
CAP_S3_INTERNAL = os.environ.get("CAP_S3_INTERNAL", "").rstrip("/")
PART_SIZE = int(os.environ.get("CAP_PART_SIZE", str(64 * 1024 * 1024)))


class CapError(Exception):
    pass


def _api(method: str, path: str, **kwargs) -> dict:
    response = requests.request(
        method,
        f"{CAP_URL}{path}",
        headers={"Authorization": f"Bearer {CAP_API_KEY}"},
        timeout=120,
        **kwargs,
    )
    if response.status_code >= 400:
        raise CapError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
    return response.json()


def _put(url: str, body, headers: dict | None = None) -> requests.Response:
    # Presigned URLs point at the public MinIO host. Sending them straight to the
    # internal MinIO with the public Host header keeps the SigV4 signature valid
    # and keeps multi-GB meeting uploads off the public nginx hop.
    headers = dict(headers or {})
    if CAP_S3_INTERNAL:
        parts = urlsplit(url)
        internal = urlsplit(CAP_S3_INTERNAL)
        headers["Host"] = parts.netloc
        url = urlunsplit((internal.scheme, internal.netloc, parts.path, parts.query, ""))
    response = requests.put(url, data=body, headers=headers, timeout=600)
    if response.status_code >= 400:
        raise CapError(f"PUT {urlsplit(url).path} -> {response.status_code}: {response.text[:300]}")
    return response


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise CapError("recording has no video stream")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = round(float(num) / float(den or 1)) if float(den or 1) else 0
    return {
        "duration": float(info["format"].get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps or 30,
        "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
    }


def prepare(source: Path, workdir: Path) -> tuple[Path, Path, dict]:
    """Remux with the moov atom up front (playable while downloading) and grab a thumbnail."""
    meta = probe(source)
    result = workdir / "result.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-c", "copy", "-movflags", "+faststart", str(result)],
        check=True, timeout=3600,
    )
    thumb = workdir / "thumb.jpg"
    at = min(10.0, meta["duration"] / 2) if meta["duration"] else 0
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(result),
         "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "4", str(thumb)],
        check=True, timeout=300,
    )
    return result, thumb, meta


def create_video(name: str, meta: dict) -> str:
    data = _api("GET", "/api/desktop/video/create", params={
        "recordingMode": "desktopMP4",
        "name": name,
        "durationInSecs": round(meta["duration"]),
        "width": meta["width"],
        "height": meta["height"],
        "fps": meta["fps"],
    })
    return data["id"]


def upload_mp4(video_id: str, path: Path, meta: dict) -> None:
    upload_id = _api("POST", "/api/upload/multipart/initiate", json={"videoId": video_id, "contentType": "video/mp4"})["uploadId"]
    size = path.stat().st_size
    parts = []
    with open(path, "rb") as handle:
        for number in range(1, max(1, math.ceil(size / PART_SIZE)) + 1):
            chunk = handle.read(PART_SIZE)
            url = _api("POST", "/api/upload/multipart/presign-part",
                       json={"videoId": video_id, "uploadId": upload_id, "partNumber": number})["presignedUrl"]
            etag = _put(url, chunk).headers.get("ETag", "").strip('"')
            if not etag:
                raise CapError(f"part {number} returned no ETag")
            parts.append({"partNumber": number, "etag": etag, "size": len(chunk)})
    done = _api("POST", "/api/upload/multipart/complete", json={
        "videoId": video_id,
        "uploadId": upload_id,
        "parts": parts,
        "durationInSecs": round(meta["duration"]),
        "width": meta["width"],
        "height": meta["height"],
        "fps": meta["fps"],
    })
    if not done.get("success"):
        raise CapError(f"multipart complete not successful: {json.dumps(done)[:300]}")


def upload_thumbnail(video_id: str, path: Path) -> None:
    signed = _api("POST", "/api/upload/signed", json={
        "videoId": video_id, "subpath": "screenshot/screen-capture.jpg", "method": "put",
    })["presignedPutData"]
    headers = {"Content-Type": "image/jpeg", **(signed.get("headers") or {})}
    _put(signed["url"], path.read_bytes(), headers)
