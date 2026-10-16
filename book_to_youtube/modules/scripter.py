"""Script de narration : prépare le texte lu pour un épisode.

Le script est découpé en *segments* (une ou deux phrases) : chaque segment
est synthétisé séparément, ce qui donne un minutage exact pour synchroniser
diapositives et sous-titres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import languages
from .splitter import Episode, split_sentences

_ABBREVIATIONS = {
    "fr": {
        r"\bM\.\s": "Monsieur ",
        r"\bMM\.\s": "Messieurs ",
        r"\bMme\b\.?": "Madame",
        r"\bMlle\b\.?": "Mademoiselle",
        r"\bDr\.\s": "Docteur ",
        r"\bSt\.?\s": "Saint ",
        r"\bp\. ex\.": "par exemple",
        r"\betc\.": "et cetera.",
    },
    "en": {
        r"\bMr\.\s": "Mister ",
        r"\bMrs\.\s": "Missus ",
        r"\bDr\.\s": "Doctor ",
        r"\be\.g\.": "for example",
        r"\bi\.e\.": "that is",
    },
}


@dataclass
class Segment:
    text: str
    kind: str = "body"  # "intro", "body", "outro"
    # Rôle dans le générique : "welcome" (chaîne + livre), "presentation", "episode".
    role: str = ""


@dataclass
class Script:
    episode: Episode
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)


def make_script(episode: Episode, config: dict | None = None, *, book_title: str = "",
                author: str | None = None, total_episodes: int | None = None,
                channel_name: str = "", presentation: str = "") -> Script:
    """Construit le script de narration d'un épisode.

    Le générique (identique pour chaque épisode, sauf le numéro et le titre) :
    « Bienvenue sur <chaîne>. <Livre>, de <auteur>. <présentation> Épisode n : titre. »
    """
    config = config or {}
    lang = config.get("language", "fr")
    tpl = languages.get(lang).script
    max_chars = int(config.get("segment_max_chars", 220))
    script = Script(episode=episode)

    if config.get("add_intro", True):
        welcome = tpl["welcome"].format(channel=channel_name) if channel_name else ""
        book = tpl["book_author" if author else "book"].format(
            book=book_title or episode.title, author=author)
        # Une seule vidéo (--split single) : pas de « Épisode 1 : … » à annoncer.
        announce = ("" if config.get("split_mode") == "single"
                    else tpl["episode"].format(number=episode.number, title=episode.title))
        presentation = prepare_for_speech(presentation, lang) if (
            presentation and config.get("read_presentation", True)) else ""
        if presentation:
            script.segments.append(Segment(f"{welcome} {book}".strip(), "intro", "welcome"))
            script.segments.extend(Segment(c, "intro", "presentation")
                                   for c in chunk_sentences(presentation, max_chars))
            script.segments.append(Segment(announce, "intro", "episode"))
        else:
            script.segments.append(Segment(f"{welcome} {book} {announce}".strip(), "intro",
                                           "episode"))

    for paragraph in episode.text.split("\n\n"):
        paragraph = prepare_for_speech(paragraph, lang)
        if paragraph:
            script.segments.extend(Segment(c) for c in chunk_sentences(paragraph, max_chars))

    if config.get("add_outro", True):
        last = total_episodes is not None and episode.number >= total_episodes
        key = ("outro_short" if config.get("video_format") == "short"
               else "outro_last" if last else "outro")
        script.segments.append(Segment(tpl[key].format(number=episode.number), "outro"))
    return script


def prepare_for_speech(text: str, lang: str = "fr") -> str:
    """Adapte le texte à la lecture à voix haute."""
    # Appels de note : « [3] », « (12) », « mot.4 », « mot4 ».
    text = re.sub(r"\[\d{1,3}\]|\(\d{1,3}\)", "", text)
    text = re.sub(r"(?<=[a-zà-ÿ])([.,;!?]?)\d{1,3}(?=\s|$)", r"\1", text)
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    text = re.sub(r"[*_#|<>{}~^`]", "", text)
    for pattern, replacement in _ABBREVIATIONS.get(lang, {}).items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+([,.])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def chunk_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Regroupe les phrases en segments de `max_chars` caractères maximum."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        for piece in _split_long_sentence(sentence, max_chars):
            if current and len(current) + 1 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    # Coupe sur la ponctuation faible, puis sur les espaces en dernier recours.
    pieces, current = [], ""
    for part in re.split(r"(?<=[,;:،؛])\s+", sentence):  # virgule et point-virgule arabes
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                pieces.append(current)
                current = ""
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if current and len(current) + 1 + len(part) > max_chars:
            pieces.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        pieces.append(current)
    return pieces
