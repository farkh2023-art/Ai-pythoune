"""Sous-titres : fichier SRT à partir du minutage des segments audio.

Un segment trop long pour un sous-titre est réparti en plusieurs cartons,
au prorata du nombre de caractères.
"""

from __future__ import annotations

from pathlib import Path

from .scripter import chunk_sentences
from .tts import Timing


def build_cues(timings: list[Timing], max_chars: int = 84) -> list[tuple[float, float, str]]:
    cues = []
    for t in timings:
        pieces = chunk_sentences(t.text, max_chars) or [t.text]
        total = sum(len(p) for p in pieces) or 1
        start = t.start
        for piece in pieces:
            end = start + (t.end - t.start) * len(piece) / total
            cues.append((start, end, piece))
            start = end
    return cues


def write_srt(timings: list[Timing], path: str | Path, max_chars: int = 84) -> Path:
    path = Path(path)
    lines = []
    for i, (start, end, text) in enumerate(build_cues(timings, max_chars), 1):
        lines += [str(i), f"{_fmt(start)} --> {_fmt(end)}", _wrap(text), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _fmt(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str, width: int = 42) -> str:
    """Au plus deux lignes équilibrées."""
    if len(text) <= width:
        return text
    middle = len(text) // 2
    left, right = text.rfind(" ", 0, middle + 1), text.find(" ", middle)
    candidates = [c for c in (left, right) if c > 0]
    if not candidates:
        return text
    cut = min(candidates, key=lambda c: abs(c - middle))
    return f"{text[:cut]}\n{text[cut + 1:]}"
