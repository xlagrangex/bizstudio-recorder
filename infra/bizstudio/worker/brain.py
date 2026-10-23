import os
import re
import subprocess
import unicodedata
from pathlib import Path

from transcribe import clock, transcript_text

REPO = os.environ.get("BRAIN_REPO", "git@github.com:xlagrangex/bizstudio-secondbrain.git")
BRANCH = os.environ.get("BRAIN_BRANCH", "main")
BASE_BRANCH = os.environ.get("BRAIN_BASE_BRANCH", "main")
CLONE_DIR = Path(os.environ.get("BRAIN_DIR", "/state/brain"))
NOTES_DIR = "raw/transcripts/video"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://rec.bizstudio.it")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(CLONE_DIR), *args], capture_output=True, text=True, timeout=300)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout.strip()


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:60].rstrip("-") or "video"


def _yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_note(video: dict, transcript: dict, ai: dict, date: str) -> str:
    lines = [
        "---",
        f"title: {_yaml(ai['title'])}",
        f"date: {date}",
        f"video_url: {PUBLIC_URL}/s/{video['id']}",
        f"video_id: {video['id']}",
        f"duration: {_yaml(clock(transcript['duration']))}",
        f"fonte: {_yaml('registrazione Bizstudio Recorder, trascrizione automatica (' + ', '.join(transcript['models']) + ')')}",
        "nota: trascrizione grezza, errori di ASR non corretti. NON modificare.",
        "---",
        "",
        f"# {ai['title']}",
        "",
        "## Sintesi",
        "",
        ai["summary"],
        "",
    ]
    if ai["chapters"]:
        lines += ["## Capitoli", ""]
        lines += [f"- [{clock(c['start'])}] {c['title']}" for c in ai["chapters"]]
        lines.append("")
    lines += ["## Trascrizione", "", transcript_text(transcript["segments"]), ""]
    return "\n".join(lines)


def _sync() -> None:
    if not (CLONE_DIR / ".git").exists():
        CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", REPO, str(CLONE_DIR)],
            check=True, capture_output=True, text=True, timeout=600,
        )
        git("config", "user.name", os.environ.get("BRAIN_GIT_NAME", "Bizstudio Recorder"))
        git("config", "user.email", os.environ.get("BRAIN_GIT_EMAIL", "recorder@bizstudio.it"))
    git("fetch", "--prune", "origin")
    remote = f"origin/{BRANCH}" if git("rev-parse", "--verify", "-q", f"origin/{BRANCH}", check=False) else f"origin/{BASE_BRANCH}"
    git("checkout", "-q", "-B", BRANCH, remote)
    git("reset", "-q", "--hard", remote)


def _existing_note(video_id: str) -> Path | None:
    notes = CLONE_DIR / NOTES_DIR
    if not notes.is_dir():
        return None
    for path in sorted(notes.glob("*.md")):
        head = path.read_text(errors="replace")[:2000]
        if re.search(rf"^video_id: {re.escape(video_id)}$", head, re.MULTILINE):
            return path
    return None


def publish_note(video: dict, transcript: dict, ai: dict, date: str) -> tuple[str, str]:
    """Returns (repo-relative note path, commit hash). Safe to re-run: an identical note is a no-op."""
    content = render_note(video, transcript, ai, date)
    for attempt in range(3):
        _sync()
        path = _existing_note(video["id"])
        if path is None:
            path = CLONE_DIR / NOTES_DIR / f"{date}-{slugify(ai['title'])}.md"
            if path.exists():
                path = path.with_name(f"{path.stem}-{video['id']}.md")
        relative = str(path.relative_to(CLONE_DIR))
        if path.exists() and path.read_text() == content:
            return relative, git("rev-parse", "HEAD")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git("add", relative)
        git("commit", "-q", "-m", f"raw(video): {ai['title']} ({date})")
        push = subprocess.run(
            ["git", "-C", str(CLONE_DIR), "push", "origin", f"HEAD:refs/heads/{BRANCH}"],
            capture_output=True, text=True, timeout=300,
        )
        if push.returncode == 0:
            return relative, git("rev-parse", "HEAD")
        if attempt == 2:
            raise RuntimeError(f"git push failed: {push.stderr.strip()[-500:]}")
    raise RuntimeError("unreachable")
