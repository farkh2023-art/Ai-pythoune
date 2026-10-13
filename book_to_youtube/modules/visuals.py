"""Génération des visuels : une image par segment de narration (Pillow).

Deux styles (``CONFIG["visual_style"]``) :

* ``slides`` : le texte narré s'affiche en grand au centre de l'écran ;
* ``card``   : carte fixe (titre du livre / de l'épisode) et sous-titre
  incrusté en bas de l'écran.

Une barre de progression en bas indique l'avancement dans l'épisode.
Le module produit aussi la miniature YouTube (1280×720).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .scripter import Script
from .tts import Timing

_FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        "/Library/Fonts/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "DejaVuSerif.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    ],
}

LABELS = {
    "fr": {"episode": "Épisode", "next": "À suivre…", "end": "Fin"},
    "en": {"episode": "Episode", "next": "To be continued…", "end": "The End"},
}


@dataclass
class Slide:
    image: Path
    duration: float


@lru_cache(maxsize=64)
def _font(kind: str, size: int, custom: str | None = None) -> ImageFont.FreeTypeFont:
    for candidate in ([custom] if custom else []) + _FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    print("Avertissement : aucune police TrueType trouvée, police par défaut utilisée.",
          file=sys.stderr)
    return ImageFont.load_default(size)


def _background(config: dict, size: tuple[int, int]) -> Image.Image:
    w, h = size
    custom = config.get("background_image")
    if custom and Path(custom).is_file():
        img = Image.open(custom).convert("RGB")
        scale = max(w / img.width, h / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)))
        left, top = (img.width - w) // 2, (img.height - h) // 2
        img = img.crop((left, top, left + w, top + h)).filter(ImageFilter.GaussianBlur(6))
        # Voile sombre pour garantir la lisibilité du texte.
        return Image.blend(img, Image.new("RGB", size, (0, 0, 0)), 0.55)

    top, bottom = config.get("background", ((22, 27, 45), (48, 36, 74)))
    gradient = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return gradient.resize(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _fit_text(draw, text, kind, max_width, max_height, start, minimum, custom=None):
    """Plus grande taille de police pour laquelle le texte tient dans la zone."""
    size = start
    while True:
        font = _font(kind, size, custom)
        lines = _wrap(draw, text, font, max_width)
        line_height = round(size * 1.35)
        if len(lines) * line_height <= max_height or size <= minimum:
            return font, lines, line_height
        size -= 2


def _draw_block(draw, lines, font, line_height, center_y, width, color):
    y = center_y - len(lines) * line_height / 2
    for line in lines:
        draw.text((width / 2, y + line_height / 2), line, font=font, fill=color, anchor="mm")
        y += line_height


class SlideRenderer:
    def __init__(self, config: dict, book_title: str, episode_number: int, episode_title: str):
        self.config = config
        self.size = tuple(config.get("resolution", (1920, 1080)))
        self.book_title = book_title
        self.number = episode_number
        self.title = episode_title
        self.labels = LABELS.get(config.get("language", "fr"), LABELS["fr"])
        self.text_color = tuple(config.get("text_color", (245, 245, 245)))
        self.accent = tuple(config.get("accent_color", (255, 196, 87)))
        self.custom_font = config.get("font_path")
        self.base = _background(config, self.size)
        self.unit = self.size[1] / 1080  # facteur d'échelle selon la résolution

    def _px(self, value: float) -> int:
        return max(1, round(value * self.unit))

    def _frame(self, progress: float | None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = self.base.copy()
        draw = ImageDraw.Draw(img)
        w, h = self.size
        header = f"{self.book_title}  ·  {self.labels['episode']} {self.number}"
        draw.text((self._px(60), self._px(50)), header,
                  font=_font("bold", self._px(30), self.custom_font), fill=self.accent)
        if progress is not None:
            bar_y, bar_h = h - self._px(14), self._px(6)
            draw.rectangle((0, bar_y, w, bar_y + bar_h), fill=(70, 70, 90))
            draw.rectangle((0, bar_y, round(w * progress), bar_y + bar_h), fill=self.accent)
        return img, draw

    def title_slide(self, subtitle: str | None = None) -> Image.Image:
        img, draw = self._frame(None)
        w, h = self.size
        label_font = _font("bold", self._px(48), self.custom_font)
        draw.text((w / 2, h * 0.30), f"{self.labels['episode']} {self.number}",
                  font=label_font, fill=self.accent, anchor="mm")
        font, lines, lh = _fit_text(draw, self.title, "bold", w - self._px(300),
                                    self._px(380), self._px(96), self._px(40), self.custom_font)
        _draw_block(draw, lines, font, lh, h * 0.52, w, self.text_color)
        if subtitle:
            draw.text((w / 2, h * 0.78), subtitle,
                      font=_font("regular", self._px(40), self.custom_font),
                      fill=self.text_color, anchor="mm")
        return img

    def text_slide(self, text: str, progress: float) -> Image.Image:
        img, draw = self._frame(progress)
        w, h = self.size
        font, lines, lh = _fit_text(draw, text, "regular", w - self._px(320),
                                    h - self._px(320), self._px(64), self._px(30), self.custom_font)
        _draw_block(draw, lines, font, lh, h / 2, w, self.text_color)
        return img

    def card_slide(self, text: str, progress: float) -> Image.Image:
        img, draw = self._frame(progress)
        w, h = self.size
        font, lines, lh = _fit_text(draw, self.title, "bold", w - self._px(300),
                                    self._px(300), self._px(80), self._px(36), self.custom_font)
        _draw_block(draw, lines, font, lh, h * 0.38, w, self.text_color)
        # Bandeau de sous-titre semi-transparent.
        sub_font, sub_lines, sub_lh = _fit_text(draw, text, "bold", w - self._px(260),
                                                self._px(230), self._px(44), self._px(26),
                                                self.custom_font)
        box_h = len(sub_lines) * sub_lh + self._px(50)
        box_top = h - self._px(60) - box_h
        overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            (self._px(100), box_top, w - self._px(100), box_top + box_h),
            radius=self._px(18), fill=(0, 0, 0, 165))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        _draw_block(ImageDraw.Draw(img), sub_lines, sub_font, sub_lh,
                    box_top + box_h / 2, w, (255, 255, 255))
        return img

    def end_slide(self, last: bool) -> Image.Image:
        img, draw = self._frame(1.0)
        w, h = self.size
        draw.text((w / 2, h / 2), self.labels["end" if last else "next"],
                  font=_font("bold", self._px(90), self.custom_font),
                  fill=self.accent, anchor="mm")
        return img


def generate(script: Script, timings: list[Timing], out_dir: str | Path, config: dict, *,
             book_title: str = "", last_episode: bool = False) -> list[Slide]:
    """Produit une image par segment ; retourne les diapositives et leur durée."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = script.episode
    renderer = SlideRenderer(config, book_title, episode.number, episode.title)
    style = config.get("visual_style", "slides")
    total = timings[-1].end if timings else 1.0

    slides = []
    for i, timing in enumerate(timings):
        progress = timing.start / total
        if timing.kind == "intro":
            img = renderer.title_slide(book_title)
        elif timing.kind == "outro" and style == "slides":
            img = renderer.end_slide(last_episode)
        elif style == "card":
            img = renderer.card_slide(timing.text, progress)
        else:
            img = renderer.text_slide(timing.text, progress)
        path = out_dir / f"slide_{i:04d}.png"
        img.save(path, optimize=False, compress_level=1)
        slides.append(Slide(path, timing.end - timing.start))
    return slides


def thumbnail(path: str | Path, config: dict, *, book_title: str, episode_number: int,
              episode_title: str) -> Path:
    """Miniature YouTube 1280×720."""
    cfg = {**config, "resolution": (1280, 720)}
    img = SlideRenderer(cfg, book_title, episode_number, episode_title).title_slide(book_title)
    path = Path(path)
    img.save(path, quality=90)
    return path
