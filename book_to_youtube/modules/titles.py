"""Titres d'épisodes proposés par Claude.

Quand le livre n'a pas de chapitres détectés (découpage par durée ou par
nombre de mots), les épisodes s'appellent « Partie 1 », « Partie 2 »… Avec une
clé API, Claude leur donne un vrai titre, court et évocateur, utilisé dans la
vidéo (générique) et sur YouTube.

Les titres sont mis en cache dans ``data/episodes/<livre>/titres.json`` (clé :
empreinte du texte de l'épisode) : une régénération garde les mêmes titres, et
vous pouvez les modifier à la main dans ce fichier.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from . import claude_client, languages
from .splitter import Episode

DEFAULT_TITLE = languages.part_titles()
CACHE_NAME = "titres.json"

_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles"],
    "additionalProperties": False,
}

PROMPTS = {
    "fr": (
        "Tu donnes des titres aux épisodes d'une série YouTube qui raconte un livre.",
        "Livre : « {book} ». Voici ses {n} épisode(s), dans l'ordre.\n\n"
        "Donne à chaque épisode un titre en {langue} :\n"
        "- court : 2 à 6 mots, sans numéro ni guillemets ;\n"
        "- évocateur : le lieu, le personnage ou l'événement central de l'épisode ;\n"
        "- sans reprendre le titre du livre ni révéler la chute de l'épisode ;\n"
        "- tous différents.\n"
        "Réponds avec exactement {n} titre(s), dans l'ordre des épisodes.\n\n{episodes}",
    ),
    "en": (
        "You title the episodes of a YouTube series that narrates a book.",
        "Book: \"{book}\". Here are its {n} episode(s), in order.\n\n"
        "Give each episode a title in English:\n"
        "- short: 2 to 6 words, no number or quotes;\n"
        "- evocative: the episode's central place, character or event;\n"
        "- do not repeat the book title or reveal the episode's ending;\n"
        "- all different.\n"
        "Reply with exactly {n} title(s), in episode order.\n\n{episodes}",
    ),
}


def needs_title(episode: Episode) -> bool:
    return bool(DEFAULT_TITLE.match(episode.title))


def _key(episode: Episode) -> str:
    return hashlib.sha1(episode.text.encode()).hexdigest()[:12]


def _clean(title: str) -> str:
    title = re.sub(r"[*_#`]", "", title)
    title = re.sub(r"^(?:épisode|episode|partie|part)\s*\d+\s*[:—–-]\s*", "", title.strip(),
                   flags=re.IGNORECASE)
    return title.strip().strip("«»\"“”'").strip().rstrip(".")


def name_episodes(episodes: list[Episode], config: dict, *, book_title: str,
                  cache_dir: str | Path, client=None, log=print) -> list[Episode]:
    """Remplace les titres « Partie N » par des titres proposés par Claude (si possible)."""
    if not any(needs_title(ep) for ep in episodes):
        return episodes
    path = Path(cache_dir) / CACHE_NAME
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    missing = [ep for ep in episodes if needs_title(ep) and _key(ep) not in cache]
    enabled = config.get("episode_titles", "auto")
    if missing and enabled != "off" and (client is not None or claude_client.available()):
        lang = config.get("language", "fr")
        system, template = PROMPTS["en" if lang == "en" else "fr"]
        budget = max(2000, 300_000 // len(missing))
        blocks = "\n\n".join(f"<episode numero=\"{i}\">\n{ep.text[:budget]}\n</episode>"
                             for i, ep in enumerate(missing, 1))
        prompt = template.format(book=book_title, n=len(missing), episodes=blocks,
                                 langue=languages.get(lang).name)
        try:
            data = claude_client.ask_json(system, prompt, config, _SCHEMA, client,
                                          what="les titres des épisodes")
            titles = [_clean(str(t)) for t in data.get("titles", [])]
            if len(titles) != len(missing) or not all(titles):
                raise claude_client.ClaudeError(
                    f"{len(titles)} titre(s) reçu(s) pour {len(missing)} épisode(s)")
        except Exception as err:
            log(f"    titres : proposition par Claude impossible ({err})")
        else:
            cache.update({_key(ep): title for ep, title in zip(missing, titles)})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"Titres des épisodes proposés par Claude (modifiables) : {path}")

    return [replace(ep, title=cache[_key(ep)]) if needs_title(ep) and cache.get(_key(ep))
            else ep for ep in episodes]
