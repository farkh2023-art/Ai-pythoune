"""Synthèse vocale : convertit le script de narration en piste audio WAV.

Chaque segment est synthétisé séparément puis les morceaux sont concaténés
(avec une courte pause entre eux). On connaît ainsi le début et la fin exacts
de chaque segment, ce qui sert au minutage des diapositives et des sous-titres.

Les segments déjà synthétisés sont mis en cache (``data/audio/cache``) : une
génération interrompue reprend là où elle s'était arrêtée.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from .scripter import Script
from .splitter import count_words

SAMPLE_RATE = 24000
DEFAULT_VOICES = {
    "edge": {"fr": "fr-FR-DeniseNeural", "en": "en-US-AriaNeural"},
    "gtts": {"fr": "fr", "en": "en"},
}


@dataclass
class Timing:
    start: float
    end: float
    text: str
    kind: str


@dataclass
class AudioTrack:
    path: Path
    duration: float
    timings: list[Timing]


def synthesize(script: Script, out_path: str | Path, config: dict) -> AudioTrack:
    """Synthétise le script et écrit un WAV mono 24 kHz à `out_path`."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = config.get("tts_engine", "edge")
    lang = config.get("language", "fr")
    voice = config.get("voice") or DEFAULT_VOICES.get(engine, {}).get(lang)
    cache_dir = Path(config.get("audio_dir", out_path.parent)) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    texts = [seg.text for seg in script.segments]
    keys = [_cache_key(engine, voice, config.get("tts_rate"), t) for t in texts]
    wavs = [cache_dir / f"{k}.wav" for k in keys]
    missing = [(t, w) for t, w in zip(texts, wavs) if not w.exists()]

    if missing:
        if engine == "edge":
            asyncio.run(_edge_batch(missing, voice, config))
        elif engine == "gtts":
            for text, wav in missing:
                _gtts(text, wav, voice or lang)
        elif engine == "pyttsx3":
            _pyttsx3_batch(missing, config.get("voice"))
        elif engine == "silent":
            wpm = config.get("words_per_minute", 150)
            for text, wav in missing:
                _write_silence(wav, max(0.8, count_words(text) / wpm * 60))
        else:
            raise ValueError(f"Moteur TTS inconnu : {engine}")

    return _concatenate(script, wavs, out_path, float(config.get("pause_between_segments", 0.25)))


def _cache_key(engine: str, voice: str | None, rate: str | None, text: str) -> str:
    payload = "\x1f".join([engine, voice or "", rate or "", text])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


# --- Moteurs -----------------------------------------------------------------

async def _edge_batch(items: list[tuple[str, Path]], voice: str, config: dict) -> None:
    import edge_tts

    semaphore = asyncio.Semaphore(int(config.get("tts_concurrency", 4)))
    rate = config.get("tts_rate") or "+0%"

    async def one(text: str, wav: Path) -> None:
        async with semaphore:
            for attempt in range(4):
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        mp3 = Path(tmp) / "seg.mp3"
                        await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3))
                        await asyncio.to_thread(_to_wav, mp3, wav)
                    return
                except Exception:  # erreurs réseau transitoires
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 ** attempt)

    await asyncio.gather(*(one(t, w) for t, w in items))


def _gtts(text: str, wav: Path, lang: str) -> None:
    from gtts import gTTS

    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "seg.mp3"
        gTTS(text=text, lang=lang).save(str(mp3))
        _to_wav(mp3, wav)


def _pyttsx3_batch(items: list[tuple[str, Path]], voice: str | None) -> None:
    import pyttsx3

    engine = pyttsx3.init()
    if voice:
        engine.setProperty("voice", voice)
    with tempfile.TemporaryDirectory() as tmp:
        raws = []
        for i, (text, _wav) in enumerate(items):
            raw = Path(tmp) / f"seg_{i}.aiff"
            engine.save_to_file(text, str(raw))
            raws.append(raw)
        engine.runAndWait()
        for raw, (_text, wav) in zip(raws, items):
            _to_wav(raw, wav)


# --- Outils audio ------------------------------------------------------------

def _to_wav(src: Path, dst: Path) -> None:
    """Convertit n'importe quel fichier audio en WAV PCM mono 24 kHz (ffmpeg)."""
    tmp = dst.with_suffix(".part.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(tmp)],
        check=True,
    )
    tmp.replace(dst)  # écriture atomique : pas de fichier de cache tronqué


def _write_silence(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(b"\x00\x00" * int(seconds * SAMPLE_RATE))


def _concatenate(script: Script, wavs: list[Path], out_path: Path, pause: float) -> AudioTrack:
    silence = b"\x00\x00" * int(pause * SAMPLE_RATE)
    timings: list[Timing] = []
    cursor = 0.0
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for i, (seg, wav) in enumerate(zip(script.segments, wavs)):
            with wave.open(str(wav), "rb") as src:
                frames = src.readframes(src.getnframes())
                duration = src.getnframes() / src.getframerate()
            out.writeframes(frames)
            # Le segment « occupe » l'écran jusqu'au début du suivant.
            gap = len(silence) / 2 / SAMPLE_RATE if i < len(wavs) - 1 else 0.0
            if gap:
                out.writeframes(silence)
            timings.append(Timing(cursor, cursor + duration + gap, seg.text, seg.kind))
            cursor += duration + gap
    return AudioTrack(path=out_path, duration=cursor, timings=timings)
