"""Nettoyage du texte : en-têtes, pieds de page, numéros de page, césures,
caractères parasites et retours à la ligne au milieu des phrases.

Le résultat est un texte composé de paragraphes séparés par une ligne vide.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

_PAGE_NUMBER = re.compile(
    r"^\s*(?:[-–—]\s*)?(?:page\s+|p\.\s*)?\d{1,4}"
    r"(?:\s*(?:/|sur|of)\s*\d{1,4})?(?:\s*[-–—])?\s*$",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f​-‏  ﻿­]")
_SEPARATOR = re.compile(r"^\s*([*#~=_.\-•·]\s*){3,}$")
# Ligne de table des matières : « Chapitre II Vers l'inconnu........ 21 ».
_TOC_LINE = re.compile(r"(?:\.\s?){4,}\s*\d{1,4}\s*$|…{2,}\s*\d{1,4}\s*$")
_SENTENCE_END = re.compile(r"[.!?…:»\"”)\]]$")

# Lignes qui doivent rester des paragraphes isolés (titres de chapitres…).
HEADING = re.compile(
    r"^(?:(?:chapitre|chapter|partie|part|livre|book|tome|section|"
    r"capítulo|capitulo|capitolo|kapitel|parte|teil|libro|buch)\s+"
    r"(?:\d+|[ivxlcdm]+|premier|première|[a-zéèêûôáíóúñçãõàìòü\-]+)\b.*"
    r"|(?:prologue|épilogue|epilogue|préface|preface|avant-propos|introduction|"
    r"conclusion|postface|interlude|prólogo|prologo|epílogo|epilogo|prolog|epilog|"
    r"vorwort|nachwort|einleitung|introducción|introduzione|introdução|prefacio|"
    r"prefazione|prefácio)\b.*"
    # Arabe : « الفصل الأول », « الباب 3 », « المقدمة »…
    r"|(?:الفصل|الباب|الجزء|القسم)\s+\S+.*"
    r"|(?:المقدمة|مقدمة|الخاتمة|خاتمة|تمهيد|التمهيد|الخلاصة)(?:\s.*)?"
    r"|(?-i:[IVXLC]{1,6})\.?)$",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    """Nettoie le texte brut et retourne des paragraphes propres."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)

    pages = text.split("\f")
    if len(pages) > 1:
        pages = _strip_running_headers(pages)
    lines: list[str] = []
    for page in pages:
        lines.extend(page.split("\n"))
        lines.append("")  # un saut de page n'unit pas deux paragraphes… sauf césure

    lines = [ln.strip() for ln in lines]
    lines = [ln for ln in lines if not _PAGE_NUMBER.match(ln) or not ln]
    lines = ["" if _TOC_LINE.search(ln) else ln for ln in lines]  # sommaire : non lu
    lines = ["" if _SEPARATOR.match(ln) else ln for ln in lines]
    paragraphs = _join_lines(lines)
    paragraphs = [_normalize_spaces(p) for p in paragraphs]
    return "\n\n".join(p for p in paragraphs if p)


def _signature(line: str) -> str:
    """Forme normalisée d'une ligne (les chiffres varient d'une page à l'autre)."""
    return re.sub(r"\d+", "#", line.strip().lower())


def _strip_running_headers(pages: list[str], edge: int = 2, ratio: float = 0.3) -> list[str]:
    """Supprime les lignes répétées en haut/bas d'une part importante des pages."""
    counter: Counter[str] = Counter()
    for page in pages:
        lines = [ln for ln in page.split("\n") if ln.strip()]
        candidates = {_signature(ln) for ln in lines[:edge] + lines[-edge:]}
        counter.update(candidates)
    threshold = max(3, int(len(pages) * ratio))
    repeated = {sig for sig, n in counter.items() if n >= threshold and len(sig) < 120}
    if not repeated:
        return pages

    cleaned = []
    for page in pages:
        lines = page.split("\n")
        non_empty = [i for i, ln in enumerate(lines) if ln.strip()]
        edges = set(non_empty[:edge] + non_empty[-edge:])
        cleaned.append(
            "\n".join(
                ln for i, ln in enumerate(lines)
                if not (i in edges and _signature(ln) in repeated)
            )
        )
    return cleaned


def _join_lines(lines: list[str]) -> list[str]:
    """Recompose les paragraphes coupés par les retours à la ligne du PDF."""
    paragraphs: list[str] = []
    current = ""
    pending_break = False  # une ligne vide a été rencontrée depuis `current`

    def flush() -> None:
        nonlocal current
        if current:
            paragraphs.append(current)
        current = ""

    for line in lines:
        if not line:
            pending_break = True
            continue
        if HEADING.match(line) and len(line) < 100:
            flush()
            paragraphs.append(line)
            pending_break = False
            continue
        if not current:
            current = line
        elif current.endswith("-") and line[:1].islower():
            # Césure en fin de ligne : « exem-\nple » -> « exemple ».
            current = current[:-1] + line
        elif pending_break and _SENTENCE_END.search(current):
            flush()
            current = line
        elif pending_break and line[:1].isupper() and len(current) < 60:
            # Ligne courte isolée (titre, dédicace…) : paragraphe à part.
            flush()
            current = line
        else:
            current = f"{current} {line}"
        pending_break = False
    flush()
    return paragraphs


def _normalize_spaces(paragraph: str) -> str:
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    # Espace parasite avant la ponctuation simple (« mot , » -> « mot, »).
    paragraph = re.sub(r"\s+([,.)\]])", r"\1", paragraph)
    return paragraph
