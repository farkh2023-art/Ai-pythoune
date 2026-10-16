"""Découpage du texte propre en épisodes.

Trois modes (``CONFIG["split_mode"]``) :

* ``chapter``  : un épisode par chapitre détecté ; les chapitres trop longs
  sont redécoupés, et si aucun chapitre n'est trouvé on bascule sur ``duration`` ;
* ``duration`` : épisodes d'environ ``target_minutes`` minutes de narration ;
* ``words``    : épisodes d'environ ``target_words`` mots.

On ne coupe jamais au milieu d'une phrase, et autant que possible entre deux
paragraphes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .cleaner import is_heading

# Fin de phrase (. ! ? … et le point d'interrogation arabe ؟) suivie d'une majuscule,
# ou de n'importe quelle lettre arabe (l'arabe n'a pas de majuscules).
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?…؟])[»\"”)]*\s+(?=[«\"“(\-—–A-ZÀ-ÖØ-Þ0-9¿¡\u0600-\u06FF])")


@dataclass
class Episode:
    number: int
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return count_words(self.text)

    def estimated_minutes(self, words_per_minute: int = 150) -> float:
        return self.word_count / max(words_per_minute, 1)


def count_words(text: str) -> int:
    return len(re.findall(r"\w+(?:['’-]\w+)*", text))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def split(text: str, config: dict) -> list[Episode]:
    """Découpe le texte en épisodes selon la configuration."""
    mode = config.get("split_mode", "chapter")
    wpm = config.get("words_per_minute", 150)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    if mode == "single":
        # Tout le livre en une seule vidéo (ex. les 4 questions d'un mot-clé) ; les
        # titres de chapitres ne sont pas lus (chaque question s'annonce elle-même).
        body = [p for p in paragraphs if not is_heading(p)]
        title = config.get("single_title") or _default_title("\n\n".join(body), 1, config)
        return [Episode(number=1, title=title, text="\n\n".join(body))]
    if mode == "words":
        target = int(config.get("target_words", 1500))
    elif mode in ("duration", "chapter"):
        target = int(config.get("target_minutes", 10) * wpm)
    else:
        raise ValueError(f"Mode de découpage inconnu : {mode}")

    sections: list[tuple[str | None, list[str]]] = [(None, paragraphs)]
    strict = False
    if mode == "chapter":
        chapters = _chapters(paragraphs)
        if any(title is not None for title, _ in chapters):
            sections = chapters
            # Seuls les chapitres plus longs que max_minutes sont redécoupés.
            target = int(config.get("max_minutes", 20) * wpm)
            strict = True
        # sinon : aucun chapitre détecté, découpage par durée cible.

    episodes: list[tuple[str | None, str]] = []
    for title, paras in sections:
        parts = _pack(paras, target, strict)
        if title is not None and len(parts) > 1:
            episodes.extend(
                (f"{title} ({i}/{len(parts)})", "\n\n".join(p)) for i, p in enumerate(parts, 1)
            )
        else:
            episodes.extend((title, "\n\n".join(p)) for p in parts)

    episodes = _merge_small(episodes, int(config.get("min_words", 0)))
    return [
        Episode(number=i, title=title or _default_title(text_, i, config), text=text_)
        for i, (title, text_) in enumerate(episodes, 1)
    ]


def _chapters(paragraphs: list[str]) -> list[tuple[str | None, list[str]]]:
    """Regroupe les paragraphes par chapitre (titre détecté par cleaner.is_heading)."""
    sections: list[tuple[str | None, list[str]]] = []
    title: str | None = None
    body: list[str] = []
    for para in paragraphs:
        if is_heading(para):
            if body:
                sections.append((title, body))
            elif title is not None and _is_bare_label(title):
                # « Chapitre 1 » puis son titre « Le départ » : on les réunit.
                para = f"{title} — {para}"
            # Sinon, un titre sans texte (sommaire sans points de suite…) est ignoré.
            title, body = para, []
        elif (not body and title is not None and _is_bare_label(title)
              and len(para) <= 80 and not re.search(r"[.!?…:;,]$", para)):
            # Sous-titre sur sa propre ligne : « Chapitre premier » / « Sur la plage ».
            title = f"{title} — {para}"
        else:
            body.append(para)
    if body:
        sections.append((title, body))
    # Texte avant le premier chapitre (page de titre, dédicace…) : on le garde
    # seulement s'il est conséquent, sinon on le rattache au premier chapitre.
    if len(sections) > 1 and sections[0][0] is None and count_words(" ".join(sections[0][1])) < 300:
        front = sections.pop(0)[1]
        first_title, first_body = sections[0]
        sections[0] = (first_title, front + first_body)
    elif sections and sections[0][0] is None and len(sections) > 1:
        sections[0] = ("Introduction", sections[0][1])
    return sections


def _is_bare_label(title: str) -> bool:
    """« Chapitre 3 », « Chapitre premier », « IV » : numéro sans titre propre."""
    return len(title.split()) <= 2 and "—" not in title


def _pack(paragraphs: list[str], target: int, strict: bool = False) -> list[list[str]]:
    """Répartit les paragraphes en blocs équilibrés d'environ `target` mots.

    Avec ``strict``, `target` est un maximum (blocs plus nombreux si besoin).
    """
    total = count_words(" ".join(paragraphs))
    target = max(target, 1)
    n_blocks = -(-total // target) if strict else round(total / target)
    if n_blocks <= 1:
        return [paragraphs]
    # On répartit équitablement plutôt que de laisser un dernier épisode minuscule.
    size_goal = total / n_blocks
    units: list[str] = []
    for para in paragraphs:
        if count_words(para) > size_goal:
            units.extend(_split_long_paragraph(para, int(size_goal)))
        else:
            units.append(para)

    blocks: list[list[str]] = []
    current: list[str] = []
    done = 0  # mots déjà placés dans les blocs fermés
    size = 0
    for unit in units:
        n = count_words(unit)
        boundary = size_goal * (len(blocks) + 1)
        # On ferme le bloc si l'ajout nous éloigne davantage de la frontière idéale.
        if current and abs(done + size + n - boundary) > abs(done + size - boundary):
            blocks.append(current)
            done += size
            current, size = [], 0
        current.append(unit)
        size += n
    if current:
        blocks.append(current)
    return blocks


def _split_long_paragraph(paragraph: str, target: int) -> list[str]:
    chunks, current, size = [], [], 0
    for sentence in split_sentences(paragraph):
        n = count_words(sentence)
        if current and size + n > target:
            chunks.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += n
    if current:
        chunks.append(" ".join(current))
    return chunks


def _merge_small(episodes: list[tuple[str | None, str]], min_words: int) -> list[tuple[str | None, str]]:
    merged: list[tuple[str | None, str]] = []
    for title, text in episodes:
        if merged and count_words(text) < min_words:
            prev_title, prev_text = merged[-1]
            merged[-1] = (prev_title, f"{prev_text}\n\n{text}")
        else:
            merged.append((title, text))
    if len(merged) > 1 and count_words(merged[0][1]) < min_words:
        (t0, x0), (t1, x1) = merged[0], merged[1]
        merged[0:2] = [(t0 or t1, f"{x0}\n\n{x1}")]
    return merged


def _default_title(text: str, number: int, config: dict) -> str:
    from . import languages
    label = languages.get(config.get("language")).labels["part"]
    return f"{label} {number}"


def save(episodes: list[Episode], directory: str | Path) -> list[Path]:
    """Enregistre chaque épisode en .txt (+ un index JSON) pour relecture."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for ep in episodes:
        path = directory / f"episode_{ep.number:03d}.txt"
        path.write_text(f"{ep.title}\n\n{ep.text}\n", encoding="utf-8")
        paths.append(path)
    index = [
        {"number": ep.number, "title": ep.title, "words": ep.word_count, "file": p.name}
        for ep, p in zip(episodes, paths)
    ]
    (directory / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths
