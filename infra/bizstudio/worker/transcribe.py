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
CHUNK_SECONDS = int(os.environ.get("CHUNK_SECONDS", "90"))
SPEECH_FLOOR_DB = int(os.environ.get("SPEECH_FLOOR_DB", "-50"))
VOCABULARY = os.environ.get(
    "WORKER_VOCABULARY",
    "Bizstudio, Bizstudio Recorder, Loom, Vincenzo Petrone, Squilloo, Hermes, OpenRouter, Claude, Claude Code, n8n, LinkedIn",
)
SILENCE_DB = -55.0
MAX_CUE_WORDS = 14
MAX_CUE_SECONDS = 7.0
MAX_SECONDS_PER_WORD = 5.0
NOISE_TAG = re.compile(r"\[[^\]]*\]")

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


def speech_islands(audio: Path, duration: float) -> list[tuple[float, float]]:
    stats = run(["ffmpeg", "-v", "info", "-i", str(audio), "-af", f"silencedetect=noise={SPEECH_FLOOR_DB}dB:d=1", "-f", "null", "-"])
    silences, opened = [], None
    for kind, value in re.findall(r"silence_(start|end): (-?[\d.]+)", stats):
        if kind == "start":
            opened = max(float(value), 0.0)
        elif opened is not None:
            silences.append((opened, float(value)))
            opened = None
    if opened is not None:
        silences.append((opened, duration))
    islands, cursor = [], 0.0
    for silence_start, silence_end in silences:
        if silence_start > cursor:
            islands.append((cursor, silence_start))
        cursor = max(cursor, silence_end)
    if cursor < duration:
        islands.append((cursor, duration))
    merged: list[list[float]] = []
    for lo, hi in islands:
        lo, hi = max(lo - 0.4, 0.0), min(hi + 0.4, duration)
        if merged and lo - merged[-1][1] < 1.0:
            merged[-1][1] = hi
        elif hi - lo >= 0.6:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def plan_chunks(islands: list[tuple[float, float]]) -> list[tuple[float, float, list[tuple[float, float]]]]:
    pieces = []
    for lo, hi in islands:
        while hi - lo > CHUNK_SECONDS:
            pieces.append((lo, lo + CHUNK_SECONDS))
            lo += CHUNK_SECONDS
        pieces.append((lo, hi))
    chunks: list[tuple[float, float, list[tuple[float, float]]]] = []
    for lo, hi in pieces:
        if chunks and hi - chunks[-1][0] <= CHUNK_SECONDS:
            start, _, members = chunks[-1]
            chunks[-1] = (start, hi, [*members, (lo, hi)])
        else:
            chunks.append((lo, hi, [(lo, hi)]))
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


def _timestamps_plausible(segments: list[dict], length: float) -> bool:
    if not segments:
        return True
    starts = [seg["start"] for seg in segments]
    if any(b < a for a, b in zip(starts, starts[1:])):
        return False
    if any(seg["start"] > length + 1 or seg["end"] > length + 1 for seg in segments):
        return False
    return len(segments) < 3 or len(set(starts)) >= len(segments) * 0.6


def _spread_over_speech(segments: list[dict], speech: list[tuple[float, float]]) -> None:
    total = sum(hi - lo for lo, hi in speech) or 1.0
    words = [max(len(seg["text"].split()), 1) for seg in segments]
    word_total = sum(words)

    def at(fraction: float) -> float:
        remaining = fraction * total
        for lo, hi in speech:
            if remaining <= hi - lo:
                return lo + remaining
            remaining -= hi - lo
        return speech[-1][1]

    done = 0
    for seg, count in zip(segments, words):
        seg["start"] = at(done / word_total)
        done += count
        seg["end"] = at(done / word_total)


def _normalize(raw_segments, offset: float, length: float, speech: list[tuple[float, float]]) -> list[dict]:
    segments = []
    for raw in raw_segments if isinstance(raw_segments, list) else []:
        try:
            text = fix_vocabulary(str(raw.get("text", "")))
            start, end = _seconds(raw.get("start", 0)), _seconds(raw.get("end", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if re.search(r"\w", re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text)):
            segments.append({"start": start, "end": end, "text": text})
    relative_speech = [(lo - offset, hi - offset) for lo, hi in speech]
    if not _timestamps_plausible(segments, length):
        _spread_over_speech(segments, relative_speech)
    for i, seg in enumerate(segments):
        seg["start"] = min(max(seg["start"], 0.0), length)
        next_start = segments[i + 1]["start"] if i + 1 < len(segments) else length
        seg["end"] = min(max(seg["end"], seg["start"]), length)
        if seg["end"] <= seg["start"] + 0.2:
            seg["end"] = min(seg["start"] + max(1.0, len(seg["text"].split()) * 0.4), max(next_start, seg["start"] + 0.5), length)
        seg["start"] = round(seg["start"] + offset, 3)
        seg["end"] = round(seg["end"] + offset, 3)
    return segments


def transcribe_chunk(audio: Path, offset: float, length: float, speech: list[tuple[float, float]], workdir: Path) -> tuple[list[dict], float, str]:
    chunk = workdir / f"chunk_{int(offset * 1000):09d}.mp3"
    cache = chunk.with_suffix(".json")
    if cache.exists():
        cached = json.loads(cache.read_text())
        return cached["segments"], 0.0, cached["model"]
    prompt = (
        f"Trascrivi fedelmente questo audio di {length:.0f} secondi. Lingua principale: italiano; "
        "se si parla un'altra lingua, trascrivi in quella lingua senza tradurre.\n"
        'Rispondi SOLO con JSON: {"language": "it", "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]}.\n'
        "- start/end in secondi (numeri) dall'inizio di QUESTO file audio.\n"
        f"- Segmenti brevi: una frase o al massimo {MAX_CUE_WORDS} parole, mai oltre {MAX_CUE_SECONDS:.0f} secondi.\n"
        "- Non riassumere, non correggere lo stile, non inventare. Ometti silenzi, rumori e annotazioni tipo [musica]. "
        'Se non c\'è parlato rispondi {"language": null, "segments": []}.\n'
        '- Il brand si scrive sempre "Bizstudio" (una parola; mai "Biz Studio", "Bistudio", "Business Studio").\n'
        f"- Nomi propri da scrivere esattamente così: {VOCABULARY}."
    )
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(audio), "-c", "copy", str(chunk)])
    parsed, model, cost = chat_json(TRANSCRIBE_MODELS, audio_message(prompt, str(chunk)), max_tokens=32000)
    segments = _normalize(parsed.get("segments") if isinstance(parsed, dict) else None, offset, length, speech)
    cache.write_text(json.dumps({"segments": segments, "model": model, "language": parsed.get("language")}, ensure_ascii=False))
    return segments, cost, model


def _cue_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def drop_hallucinations(chunks: list[list[dict]]) -> list[dict]:
    """Audio LLMs fill silence with noise tags, one word stretched over a whole chunk, or the
    same line replayed chunk after chunk; none of that is speech."""
    kept, previous = [], set()
    for chunk in chunks:
        keys = set()
        for seg in chunk:
            text = re.sub(r"\s+", " ", NOISE_TAG.sub("", seg["text"])).strip()
            key = _cue_key(text)
            if not re.search(r"\w", re.sub(r"\([^)]*\)", "", text)):
                continue
            keys.add(key)
            if key in previous or seg["end"] - seg["start"] > MAX_SECONDS_PER_WORD * len(text.split()):
                continue
            kept.append({**seg, "text": text})
        previous = keys
    return kept


def transcribe(source: Path, workdir: Path, on_chunk=lambda: None) -> dict:
    audio, duration = extract_audio(source, workdir)
    islands = speech_islands(audio, duration)
    chunks, total_cost, models = [], 0.0, set()
    for start, end, speech in plan_chunks(islands):
        chunk_segments, cost, model = transcribe_chunk(audio, start, end - start, speech, workdir)
        chunks.append(chunk_segments)
        total_cost += cost
        models.add(model)
        on_chunk()
    segments = drop_hallucinations(chunks)
    if not segments:
        raise NoAudio("no speech detected")
    speech_seconds = round(sum(hi - lo for lo, hi in islands), 1)
    return {"segments": segments, "duration": duration, "speech_seconds": speech_seconds, "cost": total_cost, "models": sorted(models)}


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
