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


# --- Style « illustre » : illustrations en plein écran + zoom lent ----------------

# Mouvements « Ken Burns » alternés d'une scène à l'autre (N = nombre d'images).
_MOVES = [
    ("1+{z}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),            # zoom avant
    ("1+{z}-{z}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),        # zoom arrière
    ("1+{z}", "(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"),                # travelling →
    ("1+{z}", "(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"),            # travelling ←
]


def assemble_illustrated(audio_path: str | Path, scenes: list, overlays: list[Slide],
                         out_path: str | Path, config: dict, work_dir: str | Path,
                         fallback_image: str | Path) -> Path:
    """Fond animé (une illustration par scène) + calques de texte + narration."""
    ffmpeg = check_ffmpeg()
    out_path = Path(out_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    fps = int(config.get("fps", 25))
    w, h = config.get("resolution", (1920, 1080))
    zoom = 0.12
    # Agrandir avant zoompan limite les saccades dues aux arrondis de pixels.
    supersampling = float(config.get("zoom_supersampling", 1.5))

    # 1. Un clip par scène. Encodage intermédiaire rapide et quasi sans perte.
    clips = []
    for k, scene in enumerate(scenes):
        frames = max(1, round(scene.duration * fps))
        if k == len(scenes) - 1:
            frames += fps  # marge : les arrondis ne doivent pas couper la fin de la narration
        image = scene.illustration.path if scene.illustration else fallback_image
        z, x, y = (part.format(z=zoom, n=frames) for part in _MOVES[k % len(_MOVES)])
        sw, sh = (round(v * supersampling / 2) * 2 for v in (w, h))
        vf = (f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
              "format=yuv420p")
        clip = work_dir / f"scene_{k:03d}.mp4"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-nostats", "-i", str(image),
                        "-vf", vf, "-frames:v", str(frames), "-c:v", "libx264",
                        "-preset", "ultrafast", "-crf", "16", str(clip)], check=True)
        clips.append(clip)

    background = work_dir / "fond.mp4"
    clip_list = work_dir / "scenes.txt"
    clip_list.write_text("".join(f"file '{_escape(c.resolve())}'\n" for c in clips),
                         encoding="utf-8")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-nostats", "-f", "concat", "-safe", "0",
                    "-i", str(clip_list), "-c", "copy", str(background)], check=True)

    # 2. Calques de texte (PNG transparents minutés) + audio sur le fond.
    overlay_list = work_dir / "calques.txt"
    lines = ["ffconcat version 1.0"]
    for slide in overlays:
        lines += [f"file '{_escape(slide.image.resolve())}'",
                  f"duration {max(slide.duration, 0.04):.3f}"]
    lines.append(f"file '{_escape(overlays[-1].image.resolve())}'")
    overlay_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    codec = config.get("video_codec", "libx264")
    tmp_out = out_path.with_suffix(".part.mp4")
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", "-nostats",
        "-i", str(background),
        "-f", "concat", "-safe", "0", "-i", str(overlay_list),
        "-i", str(audio_path),
        "-filter_complex", "[1:v]format=rgba[txt];[0:v][txt]overlay=0:0,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a", "-r", str(fps),
        "-c:v", codec, "-preset", config.get("preset", "medium"),
        "-crf", str(config.get("crf", 23)),
        "-c:a", "aac", "-b:a", config.get("audio_bitrate", "160k"), "-ar", "48000",
        "-t", f"{sum(s.duration for s in overlays):.3f}", "-shortest",
        "-movflags", "+faststart", str(tmp_out),
    ], check=True)
    tmp_out.replace(out_path)
    for temp in (*clips, background, clip_list, overlay_list):
        temp.unlink(missing_ok=True)
    return out_path
