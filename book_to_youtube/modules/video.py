"""Montage vidéo : assemble les diapositives et la piste audio avec ffmpeg.

ffmpeg est piloté depuis Python (``subprocess``) via le démultiplexeur
« concat », qui affiche chaque image pendant une durée précise. La vidéo
produite (H.264 + AAC, MP4 « faststart ») est directement acceptée par YouTube.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .visuals import Slide


class FFmpegMissingError(RuntimeError):
    pass


def check_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FFmpegMissingError(
            "ffmpeg est introuvable. Installez-le (https://ffmpeg.org/download.html, "
            "`apt install ffmpeg`, `brew install ffmpeg` ou `winget install ffmpeg`)."
        )
    return exe


def assemble(audio_path: str | Path, slides: list[Slide], out_path: str | Path,
             config: dict | None = None) -> Path:
    """Crée la vidéo `out_path` à partir de l'audio et des diapositives."""
    config = config or {}
    ffmpeg = check_ffmpeg()
    if not slides:
        raise ValueError("Aucune diapositive à assembler.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    concat_file = out_path.with_suffix(".concat.txt")
    lines = ["ffconcat version 1.0"]
    for slide in slides:
        lines += [f"file '{_escape(slide.image.resolve())}'", f"duration {max(slide.duration, 0.04):.3f}"]
    # Le démultiplexeur ignore la durée de la dernière entrée : on la répète.
    lines.append(f"file '{_escape(slides[-1].image.resolve())}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fps = str(config.get("fps", 25))
    codec = config.get("video_codec", "libx264")
    tune = ["-tune", "stillimage"] if codec == "libx264" else []
    tmp_out = out_path.with_suffix(".part.mp4")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-nostats",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
        "-vf", "format=yuv420p", "-r", fps, "-fps_mode", "cfr",
        "-c:v", codec, "-preset", config.get("preset", "medium"),
        "-crf", str(config.get("crf", 23)), *tune,
        "-c:a", "aac", "-b:a", config.get("audio_bitrate", "160k"), "-ar", "48000",
        "-t", f"{sum(s.duration for s in slides):.3f}", "-shortest",
        "-movflags", "+faststart",
        str(tmp_out),
    ]
    try:
        subprocess.run(cmd, check=True)
    finally:
        concat_file.unlink(missing_ok=True)
    tmp_out.replace(out_path)
    return out_path


def probe_duration(path: str | Path) -> float:
    """Durée d'un fichier média en secondes (ffprobe)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "'\\''")
