"""Script de narration : prépare le texte lu pour un épisode.

Le script est découpé en *segments* (une ou deux phrases) : chaque segment
est synthétisé séparément, ce qui donne un minutage exact pour synchroniser
diapositives et sous-titres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .splitter import Episode, split_sentences

TEMPLATES = {
    "fr": {
        "intro": "{book}. Épisode {number} : {title}.",
        "intro_author": "{book}, de {author}. Épisode {number} : {title}.",
        "outro": "Fin de l'épisode {number}. Merci de votre écoute, à bientôt pour la suite.",
        "outro_last": "Fin de l'épisode {number}, et fin du livre. Merci de nous avoir suivis.",
    },
    "en": {
        "intro": "{book}. Episode {number}: {title}.",
        "intro_author": "{book}, by {author}. Episode {number}: {title}.",
        "outro": "End of episode {number}. Thanks for listening, see you in the next one.",
        "outro_last": "End of episode {number}, and the end of the book. Thanks for listening.",
    },
}

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
    kind: str = "body"  # "intro", "title", "body", "outro"


@dataclass
class Script:
    episode: Episode
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)


def make_script(episode: Episode, config: dict | None = None, *, book_title: str = "",
                author: str | None = None, total_episodes: int | None = None) -> Script:
    """Construit le script de narration d'un épisode."""
    config = config or {}
    lang = config.get("language", "fr")
    tpl = TEMPLATES.get(lang, TEMPLATES["fr"])
    max_chars = int(config.get("segment_max_chars", 220))
    script = Script(episode=episode)

    if config.get("add_intro", True):
        key = "intro_author" if author else "intro"
        intro = tpl[key].format(book=book_title or episode.title, author=author,
                                number=episode.number, title=episode.title)
        script.segments.append(Segment(intro, "intro"))

    for paragraph in episode.text.split("\n\n"):
        paragraph = prepare_for_speech(paragraph, lang)
        if paragraph:
            script.segments.extend(Segment(c) for c in chunk_sentences(paragraph, max_chars))

    if config.get("add_outro", True):
        last = total_episodes is not None and episode.number >= total_episodes
        script.segments.append(
            Segment(tpl["outro_last" if last else "outro"].format(number=episode.number), "outro")
        )
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
    for part in re.split(r"(?<=[,;:])\s+", sentence):
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
