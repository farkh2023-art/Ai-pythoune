"""Traduction du livre par Claude, quand la vidéo n'est pas dans la langue du livre.

Exemple : un livre en français raconté en arabe (``--lang ar``). Chaque épisode
est traduit fidèlement (pas résumé) avant la narration ; le titre du livre et
les titres des chapitres sont traduits aussi. En mode résumé, la traduction
n'est pas nécessaire : Claude résume directement dans la langue de la vidéo.

Tout est mis en cache dans ``data/episodes/<série>/`` : une régénération ne
retraduit rien, et les fichiers ``*.trad.<langue>.*.txt`` peuvent être relus.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from . import claude_client, languages
from .splitter import Episode, count_words

CHUNK_WORDS = 2500  # un long chapitre est traduit en plusieurs morceaux

_SYSTEM = ("Tu es traducteur littéraire. Tes traductions sont lues telles quelles par une "
           "voix de synthèse dans une série de vidéos YouTube.")

_TITLES_SCHEMA = {
    "type": "object",
    "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
    "required": ["titles"],
    "additionalProperties": False,
}


def _names(config: dict, source: str) -> tuple[str, str]:
    return languages.get(source).name, languages.get(config.get("language")).name


def _from(name: str) -> str:
    return f"de l'{name}" if name[0] in "aeiouéè" else f"du {name}"


def _to(name: str) -> str:
    return f"vers l'{name}" if name[0] in "aeiouéè" else f"vers le {name}"


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:10]


def _chunks(text: str, limit: int = CHUNK_WORDS) -> list[str]:
    """Paragraphes regroupés en morceaux d'environ `limit` mots."""
    chunks, current, words = [], [], 0
    for para in [p for p in text.split("\n\n") if p.strip()]:
        n = count_words(para)
        if current and words + n > limit:
            chunks.append("\n\n".join(current))
            current, words = [], 0
        current.append(para)
        words += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


def translate_text(text: str, config: dict, *, source: str, book_title: str,
                   client=None, what: str = "ce passage") -> str:
    src, dst = _names(config, source)
    parts = []
    for k, chunk in enumerate(_chunks(text), 1):
        prompt = (
            f"Traduis {_from(src)} {_to(dst)} ce passage du livre « {book_title} »."
        ) + (
            "\n\nConsignes :\n"
            "- Traduction complète et fidèle : ne résume pas, ne coupe rien, n'ajoute rien.\n"
            "- Langue naturelle et fluide, agréable à écouter ; garde le ton et le style.\n"
            "- Garde les paragraphes, séparés par une ligne vide.\n"
            "- Noms propres : graphie usuelle dans la langue d'arrivée.\n"
            "- Pas de note, de commentaire ni de markdown. Réponds uniquement avec la "
            f"traduction.\n\n<texte>\n{chunk}\n</texte>")
        parts.append(claude_client.ask(_SYSTEM, prompt, config, client,
                                       what=f"la traduction de {what} ({k})",
                                       max_tokens=32000).strip())
    return "\n\n".join(parts)


def translate_episode(episode: Episode, config: dict, *, source: str, book_title: str,
                      cache_dir: str | Path, client=None) -> Episode:
    """Épisode traduit dans la langue de la vidéo (texte seulement ; titre inchangé)."""
    target = config.get("language", "fr")
    cache = (Path(cache_dir) / f"episode_{episode.number:03d}.trad.{target}."
             f"{_key(source, target, episode.text)}.txt")
    if cache.exists():
        return replace(episode, text=cache.read_text(encoding="utf-8").strip())
    text = translate_text(episode.text, config, source=source, book_title=book_title,
                          client=client, what=f"l'épisode {episode.number}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text + "\n", encoding="utf-8")
    return replace(episode, text=text)


def translate_titles(titles: list[str], config: dict, *, source: str, book_title: str,
                     cache_dir: str | Path, client=None, log=print) -> list[str]:
    """Titre du livre et des chapitres traduits (même liste, même ordre), mis en cache.

    En cas d'échec, les titres d'origine sont gardés.
    """
    target = config.get("language", "fr")
    path = Path(cache_dir) / f"titres.trad.{target}.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    missing = [t for t in dict.fromkeys(titles) if t and t not in cache]
    if missing:
        src, dst = _names(config, source)
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(missing, 1))
        prompt = (f"Traduis {_from(src)} en {dst} ces {len(missing)} titre(s) du livre "
                  f"« {book_title} » (titre du livre, de chapitres ou d'épisodes). Garde les "
                  "numéros de chapitre et les dates. Réponds avec exactement autant de titres, "
                  f"dans l'ordre.\n\n{listing}")
        try:
            data = claude_client.ask_json(_SYSTEM, prompt, config, _TITLES_SCHEMA, client,
                                          what="la traduction des titres")
            out = [str(t).strip() for t in data.get("titles", [])]
            if len(out) != len(missing) or not all(out):
                raise claude_client.ClaudeError(
                    f"{len(out)} titre(s) reçu(s) pour {len(missing)}")
        except Exception as err:
            log(f"    traduction des titres impossible, titres d'origine gardés ({err})")
            return titles
        cache.update(zip(missing, out))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return [cache.get(t, t) for t in titles]
