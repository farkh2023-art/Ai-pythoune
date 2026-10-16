"""Génération des visuels : une image par segment de narration (Pillow).

Deux styles (``CONFIG["visual_style"]``) :

* ``slides`` : le texte narré s'affiche en grand au centre de l'écran ;
* ``card``   : carte fixe (titre du livre / de l'épisode) et sous-titre
  incrusté en bas de l'écran.

Une barre de progression en bas indique l'avancement dans l'épisode.
Le module produit aussi la miniature YouTube (1280×720).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from . import languages
from .scripter import Script
from .tts import Timing

_FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        "/Library/Fonts/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        # Termux (`pkg install ttf-dejavu`) puis polices système d'Android
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSerif.ttf",
        "/system/fonts/NotoSerif-Regular.ttf",
        "/system/fonts/Roboto-Regular.ttf",
        "DejaVuSerif.ttf",
    ],
    # Sans empattement, pour les paragraphes lus sur petit écran (présentation).
    "sans": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/NotoSans-Regular.ttf",
        "DejaVuSans.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/system/fonts/NotoSerif-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/Roboto-Regular.ttf",
        "DejaVuSans-Bold.ttf",
    ],
}



@dataclass
class Slide:
    image: Path
    duration: float


# Écritures de droite à gauche (arabe) : polices qui contiennent l'alphabet arabe
# (DejaVu Sans couvre aussi le latin ; sous Android, Noto Naskh Arabic est fournie).
_RTL_FONTS = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/system/fonts/NotoNaskhArabic-Bold.ttf",
        "/system/fonts/NotoSansArabic-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/system/fonts/NotoNaskhArabic-Regular.ttf",
        "/system/fonts/NotoSansArabic-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans.ttf",
    ],
}
_RTL_FONTS["sans"] = _RTL_FONTS["regular"]

_text_lang = "fr"  # langue des textes dessinés (fixée par SlideRenderer)


def set_language(lang: str | None) -> None:
    global _text_lang
    _text_lang = lang or "fr"


_font_warning_shown = False


def _font(kind: str, size: int, custom: str | None = None) -> ImageFont.FreeTypeFont:
    return _load_font(kind, size, custom, languages.get(_text_lang).rtl)


@lru_cache(maxsize=64)
def _load_font(kind: str, size: int, custom: str | None, rtl: bool) -> ImageFont.FreeTypeFont:
    candidates = _RTL_FONTS[kind] if rtl else _FONT_CANDIDATES[kind]
    for candidate in ([custom] if custom else []) + candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    global _font_warning_shown
    if not _font_warning_shown:
        _font_warning_shown = True
        print("Avertissement : aucune police TrueType trouvée, police par défaut utilisée "
              "(indiquez-en une avec `font_path` dans config.py).", file=sys.stderr)
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


# --- Écriture de droite à gauche -----------------------------------------------------
#
# Avec la bibliothèque raqm (incluse dans la plupart des Pillow), Pillow lie les
# lettres arabes et ordonne la ligne lui-même. Sinon, on lie les lettres avec
# arabic-reshaper (`pip install arabic-reshaper`) et on inverse l'ordre de la ligne,
# en gardant les mots latins et les nombres dans leur sens.

_LTR_RUN = re.compile(r"[A-Za-z0-9À-ÿ](?:[A-Za-z0-9À-ÿ .,:'’/\-]*[A-Za-z0-9À-ÿ])?")
_MIRROR = str.maketrans("()[]{}«»<>", ")(][}{»«><")
_reshape_warning_shown = False


@lru_cache(maxsize=1)
def _has_raqm() -> bool:
    return features.check("raqm")


def visual_order(text: str) -> str:
    """Ligne arabe prête à dessiner sans raqm : lettres liées, ordre de lecture inversé."""
    global _reshape_warning_shown
    try:
        import arabic_reshaper
        text = arabic_reshaper.reshape(text)
    except ImportError:
        if not _reshape_warning_shown:
            _reshape_warning_shown = True
            print("Avertissement : texte arabe sans liaison des lettres. Installez "
                  "arabic-reshaper (`pip install arabic-reshaper`) ou un Pillow avec raqm.",
                  file=sys.stderr)
    flipped = text[::-1].translate(_MIRROR)
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1].translate(_MIRROR), flipped)


class _BidiDraw:
    """ImageDraw qui écrit correctement les lignes en arabe (le reste est inchangé)."""

    def __init__(self, img: Image.Image):
        self._draw = ImageDraw.Draw(img)

    def __getattr__(self, name):
        return getattr(self._draw, name)

    @staticmethod
    def _prepare(text: str, kwargs: dict) -> tuple[str, dict]:
        if not languages.is_rtl_text(text):
            return text, kwargs
        if _has_raqm():
            return text, {**kwargs, "direction": "rtl", "language": "ar"}
        return visual_order(text), kwargs

    def text(self, xy, text, *args, **kwargs):
        text, kwargs = self._prepare(text, kwargs)
        return self._draw.text(xy, text, *args, **kwargs)

    def textlength(self, text, *args, **kwargs):
        text, kwargs = self._prepare(text, kwargs)
        return self._draw.textlength(text, *args, **kwargs)

    def textbbox(self, xy, text, *args, **kwargs):
        text, kwargs = self._prepare(text, kwargs)
        return self._draw.textbbox(xy, text, *args, **kwargs)


def _canvas(img: Image.Image):
    """ImageDraw adapté à la langue des textes (arabe : droite à gauche)."""
    return _BidiDraw(img) if languages.get(_text_lang).rtl else ImageDraw.Draw(img)


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
    def __init__(self, config: dict, book_title: str, episode_number: int, episode_title: str,
                 *, author: str | None = None, channel=None, presentation: str = ""):
        self.config = config
        self.author = author
        self.channel = channel
        self.presentation = presentation
        self.logo = None
        if channel is not None and channel.logo:
            try:
                self.logo = Image.open(channel.logo).convert("RGBA")
            except OSError:
                print(f"Avertissement : logo illisible ({channel.logo}).", file=sys.stderr)
        self.size = tuple(config.get("resolution", (1920, 1080)))
        self.book_title = book_title
        self.number = episode_number
        self.title = episode_title
        self.labels = languages.get(config.get("language", "fr")).labels
        set_language(config.get("language", "fr"))
        self.text_color = tuple(config.get("text_color", (245, 245, 245)))
        self.accent = tuple(config.get("accent_color", (255, 196, 87)))
        self.custom_font = config.get("font_path")
        self.base = _background(config, self.size)
        # Facteur d'échelle selon la résolution (petit côté : marche aussi en vertical).
        self.unit = min(self.size) / 1080
        # Short (YouTube Shorts, vertical 9:16) : la question reste affichée en haut,
        # les sous-titres sont plus grands et placés dans la zone visible de l'application.
        self.short = config.get("video_format") == "short"
        self.single = config.get("split_mode") == "single"  # une seule vidéo : pas de n°
        from .curiosity import question_of
        self.question = question_of(episode_title)

    def _px(self, value: float) -> int:
        return max(1, round(value * self.unit))

    def _logo(self, max_w: int, max_h: int) -> Image.Image | None:
        if self.logo is None:
            return None
        scale = min(max_w / self.logo.width, max_h / self.logo.height)
        return self.logo.resize((max(1, round(self.logo.width * scale)),
                                 max(1, round(self.logo.height * scale))), Image.LANCZOS)

    def _header_x(self, img: Image.Image) -> int:
        """Petit logo en haut à gauche ; renvoie l'abscisse du texte d'en-tête."""
        x = self._px(60)
        logo = self._logo(self._px(120), self._px(46))
        if logo is not None:
            y = self._px(66) - logo.height // 2
            img.paste(logo, (x, y), logo)
            x += logo.width + self._px(14)
        return x

    def _short_header(self, img: Image.Image) -> Image.Image:
        """Short : la question en grand, en haut, sur un panneau sombre (sous l'interface)."""
        w, h = self.size
        draw = _canvas(img)
        font, lines, lh = _fit_text(draw, self.question, "bold", w - self._px(160),
                                    self._px(330), self._px(68), self._px(40), self.custom_font)
        top = round(h * 0.09)
        widest = max(draw.textlength(line, font=font) for line in lines)
        pad_x, pad_y = self._px(36), self._px(26)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            (w / 2 - widest / 2 - pad_x, top - pad_y, w / 2 + widest / 2 + pad_x,
             top + len(lines) * lh + pad_y), radius=self._px(26), fill=(0, 0, 0, 165))
        mode = img.mode
        img = Image.alpha_composite(img.convert("RGBA"), layer)
        img = img if mode == "RGBA" else img.convert(mode)
        _draw_block(_canvas(img), lines, font, lh, top + len(lines) * lh / 2, w, self.accent)
        return img

    def _frame(self, progress: float | None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = self.base.copy()
        w, h = self.size
        if self.short:
            img = self._short_header(img)
        draw = _canvas(img)
        if not self.short:
            header = (self.book_title if self.single
                  else f"{self.book_title}  ·  {self.labels['episode']} {self.number}")
            draw.text((self._header_x(img), self._px(50)), header,
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

    def intro_card(self, show_presentation: bool = True) -> Image.Image:
        """Générique : logo, chaîne, livre, présentation, puis numéro et titre de l'épisode."""
        img, draw = self._frame(None)
        w, h = self.size
        muted = tuple(round(c * 0.78) for c in self.text_color)
        blocks = []  # (type, contenu, hauteur, espace après)

        logo = self._logo(self._px(520), self._px(150))
        if logo is not None:
            blocks.append(("image", logo, logo.height, self._px(18)))
        name = self.channel.name if self.channel else ""
        slogan = self.channel.slogan if self.channel else ""
        if name:
            font = _font("bold", self._px(46), self.custom_font)
            blocks.append(("line", (name, font, self.accent), self._px(56), self._px(6)))
        if slogan:
            font = _font("regular", self._px(30), self.custom_font)
            blocks.append(("line", (slogan, font, muted), self._px(40), 0))
        if name or slogan or logo is not None:
            blocks[-1] = (*blocks[-1][:3], self._px(20))
            blocks.append(("rule", None, self._px(4), self._px(30)))

        font, lines, lh = _fit_text(draw, self.book_title, "bold", w - self._px(300),
                                    self._px(170), self._px(64), self._px(34), self.custom_font)
        blocks.append(("text", (lines, font, lh, self.text_color), len(lines) * lh, self._px(4)))
        if self.author:
            font = _font("regular", self._px(34), self.custom_font)
            blocks.append(("line", (self.author, font, muted), self._px(46), 0))
        if show_presentation and self.presentation:
            # Lisible sur téléphone : grande police sans empattement, texte presque
            # blanc sur un panneau sombre (le fond peut être une image claire).
            box_h = self._px(330)
            font, lines, lh = _fit_text(draw, self.presentation, "sans", w - self._px(400),
                                        box_h, self._px(40), self._px(28), self.custom_font)
            max_lines = max(1, box_h // lh)
            if len(lines) > max_lines:  # trop long même en petit : on coupe proprement
                lines = lines[:max_lines]
                lines[-1] = lines[-1].rstrip(" .,;") + "…"
            bright = tuple(round(c * 0.96) for c in self.text_color)
            blocks[-1] = (*blocks[-1][:3], self._px(40))
            blocks.append(("panel", (lines, font, lh, bright), len(lines) * lh, 0))
        if not self.single:
            blocks[-1] = (*blocks[-1][:3], self._px(64 if blocks[-1][0] == "panel" else 40))
            font = _font("bold", self._px(40), self.custom_font)
            blocks.append(("line", (f"{self.labels['episode']} {self.number}", font,
                                    self.accent), self._px(52), self._px(6)))
            font, lines, lh = _fit_text(draw, self.title, "bold", w - self._px(300),
                                        self._px(150), self._px(58), self._px(30),
                                        self.custom_font)
            blocks.append(("text", (lines, font, lh, self.text_color), len(lines) * lh, 0))

        total = sum(b[2] + b[3] for b in blocks)
        y = max(self._px(100), (h - total) / 2)
        for kind, content, height, gap in blocks:
            if kind == "panel":
                lines, font, lh, color = content
                widest = max(draw.textlength(line, font=font) for line in lines)
                pad_x, pad_y = self._px(44), self._px(22)
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(layer).rounded_rectangle(
                    (w / 2 - widest / 2 - pad_x, y - pad_y, w / 2 + widest / 2 + pad_x,
                     y + height + pad_y), radius=self._px(22), fill=(0, 0, 0, 150))
                mode = img.mode
                img = Image.alpha_composite(img.convert("RGBA"), layer)
                img = img if mode == "RGBA" else img.convert(mode)
                draw = _canvas(img)
                _draw_block(draw, lines, font, lh, y + height / 2, w, color)
            elif kind == "image":
                img.paste(content, (round((w - content.width) / 2), round(y)), content)
            elif kind == "rule":
                draw.rectangle((w / 2 - self._px(80), y, w / 2 + self._px(80), y + height),
                               fill=self.accent)
            elif kind == "line":
                text, font, color = content
                draw.text((w / 2, y + height / 2), text, font=font, fill=color, anchor="mm")
            else:
                lines, font, lh, color = content
                _draw_block(draw, lines, font, lh, y + height / 2, w, color)
            y += height + gap
        return img

    def text_slide(self, text: str, progress: float) -> Image.Image:
        img, draw = self._frame(progress)
        w, h = self.size
        font, lines, lh = _fit_text(draw, text, "regular", w - self._px(320),
                                    h - self._px(320), self._px(64), self._px(30), self.custom_font)
        _draw_block(draw, lines, font, lh, h / 2, w, self.text_color)
        return img

    def _caption_box(self, draw, text: str) -> tuple:
        """Police, lignes, interligne, hauteur et haut du bandeau de sous-titre."""
        w, h = self.size
        if self.short:  # plus grand, au-dessus des boutons de l'application Shorts
            font, lines, lh = _fit_text(draw, text, "bold", w - self._px(140), self._px(420),
                                        self._px(60), self._px(38), self.custom_font)
            box_h = len(lines) * lh + self._px(60)
            return font, lines, lh, box_h, round(h * 0.64 - box_h / 2)
        font, lines, lh = _fit_text(draw, text, "bold", w - self._px(260), self._px(230),
                                    self._px(44), self._px(26), self.custom_font)
        box_h = len(lines) * lh + self._px(50)
        return font, lines, lh, box_h, h - self._px(60) - box_h

    def card_slide(self, text: str, progress: float) -> Image.Image:
        img, draw = self._frame(progress)
        w, h = self.size
        if not self.short:  # en Short, la question est déjà affichée en haut
            font, lines, lh = _fit_text(draw, self.title, "bold", w - self._px(300),
                                        self._px(300), self._px(80), self._px(36),
                                        self.custom_font)
            _draw_block(draw, lines, font, lh, h * 0.38, w, self.text_color)
        # Bandeau de sous-titre semi-transparent.
        sub_font, sub_lines, sub_lh, box_h, box_top = self._caption_box(draw, text)
        overlay = Image.new("RGBA", self.size, (0, 0, 0, 0))
        margin = self._px(50 if self.short else 100)
        ImageDraw.Draw(overlay).rounded_rectangle(
            (margin, box_top, w - margin, box_top + box_h),
            radius=self._px(18), fill=(0, 0, 0, 165))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        _draw_block(_canvas(img), sub_lines, sub_font, sub_lh,
                    box_top + box_h / 2, w, (255, 255, 255))
        return img

    def end_slide(self, last: bool) -> Image.Image:
        img, draw = self._frame(1.0)
        w, h = self.size
        if self.short:  # grand « ? » et invitation à s'abonner
            draw.text((w / 2, h * 0.46), "?", font=_font("bold", self._px(320),
                                                         self.custom_font),
                      fill=self.accent, anchor="mm")
            font, lines, lh = _fit_text(draw, self.labels["subscribe"], "bold",
                                        w - self._px(160), self._px(260), self._px(70),
                                        self._px(40), self.custom_font)
            _draw_block(draw, lines, font, lh, h * 0.64, w, self.text_color)
            return img
        draw.text((w / 2, h / 2), self.labels["end" if last else "next"],
                  font=_font("bold", self._px(90), self.custom_font),
                  fill=self.accent, anchor="mm")
        return img


class OverlayRenderer(SlideRenderer):
    """Calques transparents (RGBA) posés sur les illustrations du style « illustre »."""

    VEIL = (0, 0, 0, 150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base = Image.new("RGBA", self.size, (0, 0, 0, 0))
        self._veil = False  # voile sombre plein écran (titre, carte de fin)

    def _frame(self, progress):
        veil = {True: self.VEIL, "fort": (0, 0, 0, 200)}.get(self._veil, (0, 0, 0, 0))
        img = Image.new("RGBA", self.size, veil)
        w, h = self.size
        if self.short:
            img = self._short_header(img)
            draw = _canvas(img)
            if progress is not None:
                bar_y, bar_h = h - self._px(14), self._px(6)
                draw.rectangle((0, bar_y, w, bar_y + bar_h), fill=(0, 0, 0, 120))
                draw.rectangle((0, bar_y, round(w * progress), bar_y + bar_h), fill=self.accent)
            return img, draw
        draw = _canvas(img)
        header = (self.book_title if self.single
                  else f"{self.book_title}  ·  {self.labels['episode']} {self.number}")
        font = _font("bold", self._px(30), self.custom_font)
        x, y = self._px(60), self._px(50)
        logo = self._logo(self._px(120), self._px(46))
        text_x = x + (logo.width + self._px(14) if logo is not None else 0)
        left, top, right, bottom = draw.textbbox((text_x, y), header, font=font)
        pad = self._px(14)
        draw.rounded_rectangle((x - pad, min(top, self._px(43)) - pad, right + pad,
                                max(bottom, self._px(89)) + pad),
                               radius=self._px(12), fill=(0, 0, 0, 140))
        if logo is not None:
            img.alpha_composite(logo, (x, self._px(66) - logo.height // 2))
        draw.text((text_x, y), header, font=font, fill=self.accent)
        if progress is not None:
            bar_y, bar_h = h - self._px(14), self._px(6)
            draw.rectangle((0, bar_y, w, bar_y + bar_h), fill=(0, 0, 0, 120))
            draw.rectangle((0, bar_y, round(w * progress), bar_y + bar_h), fill=self.accent)
        return img, draw

    def intro_card(self, show_presentation=True):
        self._veil = "fort"
        try:
            return super().intro_card(show_presentation)
        finally:
            self._veil = False

    def title_slide(self, subtitle=None):
        self._veil = True
        try:
            return super().title_slide(subtitle)
        finally:
            self._veil = False

    def end_slide(self, last):
        self._veil = True
        try:
            return super().end_slide(last)
        finally:
            self._veil = False

    def caption_slide(self, text: str, progress: float) -> Image.Image:
        img, draw = self._frame(progress)
        w, h = self.size
        font, lines, lh, box_h, box_top = self._caption_box(draw, text)
        margin = self._px(50 if self.short else 100)
        draw.rounded_rectangle((margin, box_top, w - margin, box_top + box_h),
                               radius=self._px(18), fill=(0, 0, 0, 170))
        _draw_block(draw, lines, font, lh, box_top + box_h / 2, w, (255, 255, 255))
        return img


def generate_overlays(script: Script, timings: list[Timing], out_dir: str | Path, config: dict,
                      *, book_title: str = "", last_episode: bool = False,
                      branding: dict | None = None) -> list[Slide]:
    """Un calque transparent par segment (titre, bandeau de texte, carte de fin)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = script.episode
    renderer = OverlayRenderer(config, book_title, episode.number, episode.title,
                               **(branding or {}))
    total = timings[-1].end if timings else 1.0
    slides = []
    intro_path = None
    for i, timing in enumerate(timings):
        if timing.kind == "intro":
            # Une seule image de générique, réutilisée pour toutes ses phrases.
            if intro_path is None:
                intro_path = out_dir / f"overlay_{i:04d}.png"
                renderer.intro_card().save(intro_path, compress_level=1)
            slides.append(Slide(intro_path, timing.end - timing.start))
            continue
        if timing.kind == "outro":
            img = renderer.end_slide(last_episode)
        else:
            img = renderer.caption_slide(timing.text, timing.start / total)
        path = out_dir / f"overlay_{i:04d}.png"
        img.save(path, compress_level=1)
        slides.append(Slide(path, timing.end - timing.start))
    return slides


def generate(script: Script, timings: list[Timing], out_dir: str | Path, config: dict, *,
             book_title: str = "", last_episode: bool = False,
             branding: dict | None = None) -> list[Slide]:
    """Produit une image par segment ; retourne les diapositives et leur durée."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    episode = script.episode
    renderer = SlideRenderer(config, book_title, episode.number, episode.title,
                             **(branding or {}))
    style = config.get("visual_style", "slides")
    total = timings[-1].end if timings else 1.0

    slides = []
    intro_path = None
    for i, timing in enumerate(timings):
        progress = timing.start / total
        if timing.kind == "intro":
            if intro_path is None:
                intro_path = out_dir / f"slide_{i:04d}.png"
                renderer.intro_card().save(intro_path, optimize=False, compress_level=1)
            slides.append(Slide(intro_path, timing.end - timing.start))
            continue
        if timing.kind == "outro" and style == "slides":
            img = renderer.end_slide(last_episode)
        elif style == "card":
            img = renderer.card_slide(timing.text, progress)
        else:
            img = renderer.text_slide(timing.text, progress)
        path = out_dir / f"slide_{i:04d}.png"
        img.save(path, optimize=False, compress_level=1)
        slides.append(Slide(path, timing.end - timing.start))
    return slides


def composite_slides(overlays: list[Slide], timings: list[Timing], scenes: list,
                     fallback: str | Path, out_dir: str | Path, config: dict) -> list[Slide]:
    """Style illustré sans zoom : chaque calque est fusionné avec l'image de sa scène.

    Le montage devient alors un simple diaporama, bien plus rapide à encoder.
    """
    out_dir = Path(out_dir)
    size = tuple(config.get("resolution", (1920, 1080)))
    covers: dict = {}

    def cover(path) -> Image.Image:
        if path not in covers:
            img = Image.open(path).convert("RGB")
            scale = max(size[0] / img.width, size[1] / img.height)
            img = img.resize((round(img.width * scale), round(img.height * scale)))
            left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
            covers[path] = img.crop((left, top, left + size[0], top + size[1])).convert("RGBA")
        return covers[path]

    slides = []
    for i, (overlay, timing) in enumerate(zip(overlays, timings)):
        scene = next((s for s in scenes if s.start <= timing.start < s.end), scenes[-1])
        image = scene.illustration.path if scene.illustration else fallback
        frame = Image.alpha_composite(cover(image), Image.open(overlay.image).convert("RGBA"))
        path = out_dir / f"slide_{i:04d}.png"
        frame.convert("RGB").save(path, compress_level=1)
        slides.append(Slide(path, overlay.duration))
    return slides


def plain_background(path: str | Path, config: dict) -> Path:
    """Fond dégradé du thème, utilisé quand aucune illustration n'est trouvée."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {**config, "background_image": None}
    _background(cfg, tuple(config.get("resolution", (1920, 1080)))).save(path, quality=92)
    return path


def thumbnail(path: str | Path, config: dict, *, book_title: str, episode_number: int,
              episode_title: str, background: str | Path | None = None,
              branding: dict | None = None) -> Path:
    """Miniature YouTube 1280×720 : logo, livre et épisode (sur l'illustration si fournie)."""
    cfg = {**config, "resolution": (1280, 720), "video_format": "standard"}
    if background:
        cfg["background_image"] = background
    renderer = SlideRenderer(cfg, book_title, episode_number, episode_title, **(branding or {}))
    img = renderer.intro_card(show_presentation=False)
    path = Path(path)
    img.save(path, quality=90)
    return path
