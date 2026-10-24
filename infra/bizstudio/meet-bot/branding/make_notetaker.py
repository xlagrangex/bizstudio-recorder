"""Renders the Meet bot's camera tile: a still (bot_image) and a seamless loop (output_video).

Run once on a dev machine, the outputs are committed in ../bridge/assets:
    python3 make_notetaker.py
Needs rsvg-convert, ffmpeg and the Inter font installed (fontconfig), plus Pillow.
The mascot is Rec with the friendly face of rec-pronto.svg and the lit record
light of rec-registra.svg (both copied in this folder), animated by hand-picked
transforms whose periods all divide LOOP_SECONDS, so the last frame meets the first.
"""

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import ImageFont

WIDTH, HEIGHT = 1280, 720
FPS = 15
LOOP_SECONDS = 6
CAPTION = "Sto registrando questa riunione per Vincenzo"
CAPTION_SIZE = 28
OUT = Path(__file__).resolve().parent.parent / "bridge" / "assets"


def caption_width() -> float:
    for path in (Path.home() / "Library/Fonts/Inter-Medium.ttf", Path("/usr/share/fonts/truetype/inter/Inter-Medium.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), CAPTION_SIZE).getlength(CAPTION)
    sys.exit("Inter-Medium.ttf not found: install the Inter font first")


def wave(t: float, period: float, phase: float = 0) -> float:
    """0 at the start of each period, 1 halfway, smooth."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * (t / period + phase))


def blink(t: float) -> float:
    closed = 0.0
    for start in (1.9, 4.5, 4.85):
        x = (t - start) / 0.27
        if 0 <= x <= 1:
            closed = max(closed, 1 - abs(2 * x - 1))
    return closed


def frame(t: float, text_width: float) -> str:
    lift = wave(t, 3)
    squash = 2 * lift - 1
    sx, sy = 1 - 0.014 * squash, 1 + 0.02 * squash
    dy = -7 * lift
    arm = 5 * math.sin(2 * math.pi * t / 3)
    eyes = 1 - 0.92 * blink(t)
    look = 2.5 * math.sin(2 * math.pi * t / LOOP_SECONDS)
    pulse = wave(t, 1.5, 0.5)
    scale = 1.6
    ox, oy = 640 - 120 * scale, 322 - 128 * scale
    dot_r = 7
    gap = 14
    row = 2 * dot_r + gap + text_width
    left = 640 - row / 2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <defs>
    <linearGradient id="bg" gradientUnits="userSpaceOnUse" x1="140" y1="-140" x2="1140" y2="860">
      <stop offset="0" stop-color="#C3DCF8"/><stop offset=".55" stop-color="#DACBF9"/><stop offset="1" stop-color="#F6D9EC"/>
    </linearGradient>
    <radialGradient id="halo" cx="640" cy="330" r="300" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="body" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2f6bff"/><stop offset=".5" stop-color="#e21ecf"/><stop offset="1" stop-color="#ff7a1a"/>
    </linearGradient>
    <radialGradient id="lens" cx=".38" cy=".34" r=".75">
      <stop offset="0" stop-color="#3a3a46"/><stop offset=".7" stop-color="#131313"/><stop offset="1" stop-color="#050507"/>
    </radialGradient>
    <radialGradient id="glow" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#ff3b3b" stop-opacity=".7"/><stop offset="1" stop-color="#ff3b3b" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#halo)"/>
  <g transform="translate({ox:.2f} {oy:.2f}) scale({scale})">
    <ellipse cx="120" cy="222" rx="{62 * (1 - 0.12 * lift):.2f}" ry="{8 * (1 - 0.12 * lift):.2f}" fill="#131313" opacity="{0.12 * (1 - 0.3 * lift):.3f}"/>
    <g transform="translate(0 {dy:.2f}) translate(120 200) scale({sx:.4f} {sy:.4f}) translate(-120 -200)">
      <path transform="rotate({arm:.2f} 50 140)" d="M50 140c-16 6-22 22-16 34" stroke="#131313" stroke-width="10" stroke-linecap="round" fill="none"/>
      <path transform="rotate({-arm:.2f} 190 140)" d="M190 140c16 6 22 22 16 34" stroke="#131313" stroke-width="10" stroke-linecap="round" fill="none"/>
      <rect x="44" y="52" width="152" height="148" rx="58" fill="url(#body)"/>
      <rect x="44" y="52" width="152" height="148" rx="58" fill="#fff" opacity=".08"/>
      <path d="M74 70c18-10 46-14 70-8" stroke="#fff" stroke-opacity=".45" stroke-width="7" stroke-linecap="round" fill="none"/>
      <rect x="104" y="30" width="32" height="26" rx="10" fill="#131313"/>
      <circle cx="168" cy="76" r="{18 + 6 * pulse:.2f}" fill="url(#glow)" opacity="{0.35 + 0.65 * pulse:.3f}"/>
      <circle cx="168" cy="76" r="9" fill="#fff"/><circle cx="168" cy="76" r="6" fill="#ff3b3b"/>
      <circle cx="120" cy="128" r="50" fill="#fff"/>
      <circle cx="120" cy="128" r="42" fill="url(#lens)"/>
      <g transform="translate(0 122) scale(1 {eyes:.3f}) translate(0 -122)">
        <ellipse cx="104" cy="122" rx="9" ry="12" fill="#fff"/><ellipse cx="136" cy="122" rx="9" ry="12" fill="#fff"/>
        <circle cx="{104 + look:.2f}" cy="120" r="3.8" fill="#131313"/><circle cx="{136 + look:.2f}" cy="120" r="3.8" fill="#131313"/>
      </g>
      <path d="M108 146q12 10 24 0" stroke="#fff" stroke-width="4.5" stroke-linecap="round" fill="none"/>
    </g>
  </g>
  <circle cx="{left + dot_r:.2f}" cy="594" r="{dot_r}" fill="#ff3b3b" opacity="{0.45 + 0.55 * pulse:.3f}"/>
  <text x="{left + 2 * dot_r + gap:.2f}" y="604" font-family="Inter" font-weight="500" font-size="{CAPTION_SIZE}" fill="#131313" fill-opacity=".78">{CAPTION}</text>
</svg>"""


def render(svg: str, png: Path) -> None:
    source = png.with_suffix(".svg")
    source.write_text(svg)
    subprocess.run(["rsvg-convert", "-w", str(WIDTH), "-h", str(HEIGHT), "-o", str(png), str(source)], check=True)


def main() -> None:
    for tool in ("rsvg-convert", "ffmpeg"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found")
    OUT.mkdir(parents=True, exist_ok=True)
    text_width = caption_width()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        still = work / "still.png"
        render(frame(0, text_width), still)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(still), "-q:v", "2", str(OUT / "notetaker.jpg")], check=True)
        for index in range(FPS * LOOP_SECONDS):
            render(frame(index / FPS, text_width), work / f"f{index:04d}.png")
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(work / "f%04d.png"),
            "-c:v", "libx264", "-preset", "veryslow", "-tune", "animation", "-crf", "24",
            "-profile:v", "main", "-pix_fmt", "yuv420p", "-g", str(FPS * LOOP_SECONDS),
            "-movflags", "+faststart", "-an", str(OUT / "notetaker.mp4"),
        ], check=True)
    for name in ("notetaker.jpg", "notetaker.mp4"):
        print(f"{OUT / name}: {(OUT / name).stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
