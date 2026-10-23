import json
import os
import re
import subprocess
from pathlib import Path

from openrouter import audio_message, chat_json

TRANSCRIBE_MODELS = os.environ.get(
    "TRANSCRIBE_MODELS", "google/gemini-3.1-flash-lite,qwen/qwen3.8-omni-flash"
).split(",")
TEXT_MODELS = os.environ.get(
    "TEXT_MODELS", "google/gemini-3.1-flash-lite,qwen/qwen3.8-omni-flash"
).split(",")
CHUNK_SECONDS = int(os.environ.get("CHUNK_SECONDS", "300"))
VOCABULARY = os.environ.get(
    "WORKER_VOCABULARY",
    "Bizstudio, Bizstudio Recorder, Loom, Vincenzo Petrone, Squilloo, Hermes, OpenRouter, Claude, Claude Code, n8n, LinkedIn",
)
SILENCE_DB = -55.0
MAX_CUE_WORDS = 14
MAX_CUE_SECONDS = 7.0

BRAND_FIXES = [
    (re.compile(r"(?<![\w./@-])(?:biz+|bis*|bit?z)[\s-]?studio(?![\w.@-]*\w)", re.IGNORECASE), "Bizstudio"),
]


class NoAudio(Exception):
    pass


def run(args: list[str], timeout: int = 1800) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout + result.stderr


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.strip().splitlines()[0])


def has_audio_stream(path: Path) -> bool:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)])
    return bool(out.strip())


def extract_audio(source: Path, workdir: Path) -> tuple[Path, float]:
    if not has_audio_stream(source):
        raise NoAudio("no audio stream")
    audio = workdir / "audio.mp3"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(audio)])
    stats = run(["ffmpeg", "-v", "info", "-i", str(audio), "-af", "volumedetect", "-f", "null", "-"])
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", stats)
    if peak and (peak.group(1) == "-inf" or float(peak.group(1)) < SILENCE_DB):
        raise NoAudio("audio track is silent")
    return audio, probe_duration(audio)


def split_chunks(audio: Path, duration: float, workdir: Path) -> list[tuple[Path, float, float]]:
    chunks = []
    start = 0.0
    index = 0
    while start < duration - 0.5:
        length = min(CHUNK_SECONDS, duration - start)
        chunk = workdir / f"chunk_{index:03d}.mp3"
        if not chunk.exists():
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio), "-c", "copy", str(chunk)])
        chunks.append((chunk, start, length))
        start += length
        index += 1
    return chunks


def _seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(p) for p in str(value).replace(",", ".").split(":")]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def fix_vocabulary(text: str) -> str:
    for pattern, replacement in BRAND_FIXES:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize(raw_segments, offset: float, length: float) -> list[dict]:
    segments = []
    for raw in raw_segments or []:
        try:
            text = fix_vocabulary(str(raw.get("text", "")))
            start = min(max(_seconds(raw.get("start", 0)), 0.0), length)
            end = min(max(_seconds(raw.get("end", start)), start), length)
        except (TypeError, ValueError, AttributeError):
            continue
        if text:
            segments.append({"start": start, "end": end, "text": text})
    segments.sort(key=lambda s: s["start"])
    for i, seg in enumerate(segments):
        next_start = segments[i + 1]["start"] if i + 1 < len(segments) else length
        if seg["end"] <= seg["start"] + 0.2:
            seg["end"] = min(seg["start"] + max(1.0, len(seg["text"].split()) * 0.4), max(next_start, seg["start"] + 0.5))
        seg["start"] = round(seg["start"] + offset, 3)
        seg["end"] = round(seg["end"] + offset, 3)
    return segments


def transcribe_chunk(chunk: Path, offset: float, length: float, cache: Path) -> tuple[list[dict], float, str]:
    if cache.exists():
        cached = json.loads(cache.read_text())
        return cached["segments"], 0.0, cached["model"]
    prompt = (
        f"Trascrivi fedelmente questo audio di {length:.0f} secondi. Lingua principale: italiano; "
        "se si parla un'altra lingua, trascrivi in quella lingua senza tradurre.\n"
        'Rispondi SOLO con JSON: {"language": "it", "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]}.\n'
        "- start/end in secondi (numeri) dall'inizio di QUESTO file audio.\n"
        f"- Segmenti brevi: una frase o al massimo {MAX_CUE_WORDS} parole, mai oltre {MAX_CUE_SECONDS:.0f} secondi.\n"
        "- Non riassumere, non correggere lo stile, non inventare. Ometti i silenzi. "
        'Se non c\'è parlato rispondi {"language": null, "segments": []}.\n'
        '- Il brand si scrive sempre "Bizstudio" (una parola; mai "Biz Studio", "Bistudio", "Business Studio").\n'
        f"- Nomi propri da scrivere esattamente così: {VOCABULARY}."
    )
    parsed, model, cost = chat_json(TRANSCRIBE_MODELS, audio_message(prompt, str(chunk)), max_tokens=32000)
    segments = _normalize(parsed.get("segments") if isinstance(parsed, dict) else None, offset, length)
    cache.write_text(json.dumps({"segments": segments, "model": model, "language": parsed.get("language")}, ensure_ascii=False))
    return segments, cost, model


def transcribe(source: Path, workdir: Path) -> dict:
    audio, duration = extract_audio(source, workdir)
    segments, total_cost, models = [], 0.0, set()
    for chunk, offset, length in split_chunks(audio, duration, workdir):
        chunk_segments, cost, model = transcribe_chunk(chunk, offset, length, chunk.with_suffix(".json"))
        segments.extend(chunk_segments)
        total_cost += cost
        models.add(model)
    if not segments:
        raise NoAudio("no speech detected")
    return {"segments": segments, "duration": duration, "cost": total_cost, "models": sorted(models)}


def _split_long(segment: dict) -> list[dict]:
    words = segment["text"].split()
    span = segment["end"] - segment["start"]
    pieces = max(1, -(-len(words) // MAX_CUE_WORDS), int(span // MAX_CUE_SECONDS) + (span % MAX_CUE_SECONDS > 0))
    pieces = min(pieces, len(words))
    if pieces <= 1:
        return [segment]
    out = []
    for i in range(pieces):
        lo, hi = len(words) * i // pieces, len(words) * (i + 1) // pieces
        out.append({
            "start": segment["start"] + span * lo / len(words),
            "end": segment["start"] + span * hi / len(words),
            "text": " ".join(words[lo:hi]),
        })
    return out


def _vtt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def to_vtt(segments: list[dict]) -> str:
    cues = [cue for seg in segments for cue in _split_long(seg)]
    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, 1):
        lines += [str(index), f"{_vtt_time(cue['start'])} --> {_vtt_time(cue['end'])}", cue["text"], ""]
    return "\n".join(lines)


def clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def transcript_text(segments: list[dict]) -> str:
    return "\n".join(f"[{clock(s['start'])}] {s['text']}" for s in segments)


def enrich(segments: list[dict], duration: float) -> dict:
    wants_chapters = duration >= 180
    prompt = (
        "Questa è la trascrizione (con timestamp) di una registrazione schermo fatta con Bizstudio Recorder.\n"
        "Rispondi SOLO con JSON in italiano:\n"
        '{"title": "...", "summary": "...", "chapters": [{"title": "...", "start": 0}]}\n'
        "- title: specifico e concreto, max 70 caratteri, niente virgolette, niente emoji, niente date.\n"
        "- summary: 2-5 frasi semplici su cosa si vede/dice e a cosa serve. Niente formule di apertura.\n"
        + (
            "- chapters: 3-8 capitoli in ordine, start in secondi interi; il primo parte da 0.\n"
            if wants_chapters
            else "- chapters: lista vuota.\n"
        )
        + f'- Il brand si scrive "Bizstudio". Nomi propri: {VOCABULARY}.\n\n'
        + transcript_text(segments)
    )
    parsed, model, cost = chat_json(TEXT_MODELS, [{"role": "user", "content": prompt}], max_tokens=4000)
    title = fix_vocabulary(str(parsed.get("title") or "")).strip(" \"'«»")[:120]
    summary = fix_vocabulary(str(parsed.get("summary") or ""))
    chapters = []
    for chapter in parsed.get("chapters") or [] if wants_chapters else []:
        try:
            start = int(min(max(_seconds(chapter.get("start", 0)), 0), duration))
            chapter_title = fix_vocabulary(str(chapter.get("title", "")))[:100]
        except (TypeError, ValueError, AttributeError):
            continue
        if chapter_title and all(start > c["start"] for c in chapters):
            chapters.append({"title": chapter_title, "start": start})
    if chapters:
        chapters[0]["start"] = 0
    if not title or not summary:
        raise ValueError("model returned empty title or summary")
    return {"title": title, "summary": summary, "chapters": chapters, "model": model, "cost": cost}
