"""Erzeugt build/assets/ragos.ico (mehrere Auflösungen) — schlichtes RAG-OS-Mark
im SIMA-Akzentblau. Läuft einmalig im Build (build.ps1) bzw. manuell:
    python build/make-icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (37, 99, 235, 255)   # SIMA-Akzentblau
WHITE = (255, 255, 255, 255)
OUT = Path(__file__).resolve().parent / "assets" / "ragos.ico"


def _render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(2, size // 5)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=ACCENT)
    # "R" mittig
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.62))
    except Exception:
        font = ImageFont.load_default()
    text = "R"
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1]), text, font=font, fill=WHITE)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    base = _render(256)
    base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"geschrieben: {OUT}")


if __name__ == "__main__":
    main()
