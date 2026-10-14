"""Prompts d'images par scène, préparés dès l'import du livre.

Pour chaque épisode, le dossier ``data/illustrations/<livre>/episode_NNN/``
reçoit :

* ``prompts.txt`` : un prompt par scène, à copier dans votre générateur d'images
  (ChatGPT, Gemini, Midjourney, Leonardo…) ;
* ``scenes.json`` : la même chose pour le tableau de bord.

Déposez ensuite vos images dans ce dossier sous les noms ``scene_01.jpg``,
``scene_02.png``… : elles remplacent les images de Wikimedia Commons dans la
vidéo (style « illustre »).

Avec une clé API Anthropic, Claude rédige les prompts (en anglais, la langue que
les générateurs comprennent le mieux) à partir d'une « bible visuelle » du livre
(époque, lieux, apparence des personnages) pour garder des images cohérentes.
Sans clé, un prompt simple est construit à partir du texte de la scène.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import claude_client
from .illustrations import episode_dir, illustrations_root, user_image
from .splitter import split_sentences

DEFAULT_STYLE = ("Illustration narrative détaillée, peinture numérique, lumière cinématographique, "
                 "couleurs riches")

_SCHEMA = {
    "type": "object",
    "properties": {"prompts": {"type": "array", "items": {"type": "string"}}},
    "required": ["prompts"],
    "additionalProperties": False,
}


def use_claude(config: dict) -> bool:
    mode = config.get("image_prompts", "auto")
    return mode == "claude" or (mode == "auto" and claude_client.available())


# --- Bible visuelle (une par livre) ------------------------------------------------

def visual_bible(config: dict, slug: str, book_title: str, author: str | None, text: str,
                 client=None, log=print) -> str:
    """Description de référence (époque, lieux, personnages), mise en cache."""
    path = illustrations_root(config) / slug / "bible.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if not use_claude(config):
        return ""
    system = ("Tu es directeur artistique. Tu prépares l'illustration d'un livre adapté en "
              "série de vidéos.")
    prompt = (f"Livre : « {book_title} »{f', de {author}' if author else ''}.\n\n"
              "Rédige en anglais une bible visuelle concise (250 mots maximum) pour garder des "
              "illustrations cohérentes : époque et lieux, ambiance et palette de couleurs, puis "
              "pour chaque personnage principal une description physique stable (âge, visage, "
              "cheveux, vêtements). Pas de titres markdown, des phrases simples.\n\n"
              f"<livre>\n{text[:400_000]}\n</livre>")
    try:
        bible = claude_client.ask(system, prompt, config, client, what="la bible visuelle")
    except Exception as err:
        log(f"    prompts : bible visuelle impossible ({err})")
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bible + "\n", encoding="utf-8")
    return bible


# --- Prompts par épisode -----------------------------------------------------------

def _excerpt(text: str, limit: int = 350) -> str:
    out = ""
    for sentence in split_sentences(text):
        if out and len(out) + len(sentence) > limit:
            break
        out = f"{out} {sentence}".strip()
    return out[:limit].rstrip() + ("…" if len(out) > limit else "")


def template_prompt(text: str, config: dict) -> str:
    style = config.get("image_style") or DEFAULT_STYLE
    return (f"{style}. Scène : {_excerpt(text)} "
            "Format paysage 16:9, sans texte ni lettrage.")


def _claude_prompts(texts: list[str], config: dict, book_title: str, author: str | None,
                    episode, bible: str, client=None) -> list[str]:
    style = config.get("image_style") or DEFAULT_STYLE
    scenes = "\n\n".join(f"<scene numero=\"{i}\">\n{t}\n</scene>" for i, t in enumerate(texts, 1))
    system = ("Tu es directeur artistique. Tu écris des prompts pour un générateur d'images "
              "afin d'illustrer, scène par scène, la lecture d'un livre en vidéo.")
    prompt = (
        f"Livre : « {book_title} »{f', de {author}' if author else ''}. "
        f"Épisode {episode.number} : {episode.title}.\n"
        f"Style imposé pour toutes les images : {style}.\n\n"
        + (f"Bible visuelle (à respecter pour les personnages et les lieux) :\n{bible}\n\n"
           if bible else "")
        + f"Écris exactement {len(texts)} prompts, un par scène et dans l'ordre, en anglais. "
        "Chaque prompt (40 à 80 mots) décrit une seule image fixe au format paysage 16:9 : "
        "le sujet principal, le décor, l'éclairage et le cadrage, en reprenant le style imposé "
        "et les descriptions de la bible. Aucun texte, lettrage ni logo dans l'image. Évite la "
        "violence explicite.\n\n" + scenes)
    data = claude_client.ask_json(system, prompt, config, _SCHEMA, client,
                                  what=f"les prompts de l'épisode {episode.number}")
    return [str(p).strip() for p in data.get("prompts", [])]


def write_episode_prompts(config: dict, slug: str, episode, texts: list[str], *,
                          book_title: str, author: str | None = None, bible: str = "",
                          client=None, log=print) -> list[dict]:
    """Écrit prompts.txt et scenes.json (sauf s'ils correspondent déjà à ces scènes)."""
    folder = episode_dir(config, slug, episode.number)
    folder.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha1(json.dumps(
        [texts, config.get("image_style") or DEFAULT_STYLE], ensure_ascii=False).encode()
    ).hexdigest()[:12]
    index_path = folder / "scenes.json"
    try:
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        if existing.get("signature") == signature:
            return existing["scenes"]
    except (OSError, ValueError):
        pass

    prompts: list[str] = []
    author_label = "modele"
    if texts and use_claude(config):
        try:
            prompts = _claude_prompts(texts, config, book_title, author, episode, bible, client)
            author_label = "claude"
        except Exception as err:
            log(f"    prompts : Claude indisponible, prompts simples utilisés ({err})")
    if len(prompts) != len(texts):
        prompts = [p for p in prompts[:len(texts)]]
        prompts += [template_prompt(t, config) for t in texts[len(prompts):]]

    scenes = [{"scene": i, "image": f"scene_{i:02d}.jpg", "extrait": _excerpt(t, 220),
               "prompt": p} for i, (t, p) in enumerate(zip(texts, prompts), 1)]
    index_path.write_text(json.dumps({"signature": signature, "auteur": author_label,
                                      "episode": episode.number, "titre": episode.title,
                                      "scenes": scenes}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    lines = [f"{book_title} — Épisode {episode.number} : {episode.title}",
             "Déposez vos images dans ce dossier : scene_01.jpg, scene_02.jpg… "
             "(JPG, PNG ou WEBP, format paysage 16:9).", ""]
    for sc in scenes:
        lines += [f"Scène {sc['scene']:02d}  →  scene_{sc['scene']:02d}.jpg",
                  f"Texte : « {sc['extrait']} »", f"Prompt : {sc['prompt']}", ""]
    (folder / "prompts.txt").write_text("\n".join(lines), encoding="utf-8")
    return scenes


def write_book_index(config: dict, slug: str, book_title: str) -> Path:
    """PROMPTS.md : tous les prompts du livre, avec l'état des images déposées."""
    root = illustrations_root(config) / slug
    lines = [f"# Prompts d'images — {book_title}", "",
             "Une image par scène, à déposer dans le dossier de l'épisode sous le nom indiqué.",
             ""]
    for folder in sorted(root.glob("episode_*")):
        try:
            data = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lines += [f"## Épisode {data['episode']} — {data['titre']}  (`{folder.name}/`)", ""]
        for sc in data["scenes"]:
            done = "✅" if user_image(folder, sc["scene"]) else "⬜"
            lines += [f"{done} **scene_{sc['scene']:02d}** — {sc['prompt']}", ""]
    path = root / "PROMPTS.md"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def scene_status(config: dict, slug: str, number: int) -> list[dict]:
    """Scènes d'un épisode avec leur prompt et l'image déposée (pour le tableau de bord)."""
    folder = episode_dir(config, slug, number)
    try:
        scenes = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    except (OSError, ValueError, KeyError):
        return []
    for sc in scenes:
        image = user_image(folder, sc["scene"])
        sc["fichier"] = image.name if image else None
    return scenes


def save_scene_image(config: dict, slug: str, number: int, scene: int, data: bytes,
                     suffix: str) -> Path:
    suffix = suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError("Image : formats acceptés JPG, PNG ou WEBP.")
    folder = episode_dir(config, slug, number)
    folder.mkdir(parents=True, exist_ok=True)
    delete_scene_image(config, slug, number, scene)
    path = folder / f"scene_{scene:02d}{'.jpg' if suffix == '.jpeg' else suffix}"
    path.write_bytes(data)
    return path


def delete_scene_image(config: dict, slug: str, number: int, scene: int) -> None:
    folder = episode_dir(config, slug, number)
    while (image := user_image(folder, scene)) is not None:
        image.unlink()

