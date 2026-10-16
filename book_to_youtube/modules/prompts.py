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

Découpage visuel : avec une clé, Claude découpe aussi l'épisode en scènes. Au
lieu d'une image toutes les ~40 secondes, une nouvelle scène commence quand ce
qu'on doit voir change (lieu, personnage, objet, étape…), et chaque prompt
illustre exactement les phrases lues pendant son image. Le découpage est gardé
dans ``decoupage.json`` (dossier de l'épisode) : les scènes ne bougent plus
ensuite, et vos images ``scene_NN`` restent à leur place.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import claude_client
from .illustrations import (CLIP_EXTENSIONS, episode_dir, illustrations_root, user_clip,
                            user_image)
from .splitter import count_words, split_sentences

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


# --- Découpage visuel des scènes (Claude) -------------------------------------------

_CUT_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {"first": {"type": "integer"}, "prompt": {"type": "string"}},
        "required": ["first", "prompt"],
        "additionalProperties": False,
    }}},
    "required": ["scenes"],
    "additionalProperties": False,
}
CUT_FILE = "decoupage.json"


def visual_scene_plan(config: dict, slug: str, episode, segments: list, *, book_title: str,
                      author: str | None = None, bible: str = "", client=None,
                      log=print) -> tuple[list[tuple[int, int]], list[str]] | None:
    """Scènes découpées par Claude selon ce qu'on doit voir, avec leur prompt.

    Retourne (plages de segments, prompts), ou None pour garder le découpage par
    durée : mode « duree », pas de clé, réponse inutilisable, ou épisode dont
    les images ont déjà été déposées sur l'ancien découpage (mode « auto »).
    """
    mode = config.get("scene_cut", "auto")
    body = [i for i, seg in enumerate(segments) if seg.kind == "body"]
    if mode == "duree" or not body:
        return None
    wpm = config.get("words_per_minute", 150)
    pause = float(config.get("pause_between_segments", 0.25))
    seconds = [count_words(segments[i].text) / wpm * 60 + pause for i in body]
    low = float(config.get("scene_min_seconds", 8))
    high = float(config.get("scene_max_seconds", 30))
    style = config.get("image_style") or DEFAULT_STYLE
    signature = hashlib.sha1(json.dumps(
        [[segments[i].text for i in body], style, low, high], ensure_ascii=False).encode()
    ).hexdigest()[:12]
    folder = episode_dir(config, slug, episode.number)
    path = folder / CUT_FILE

    def absolute(starts: list[int]) -> list[tuple[int, int]]:
        ends = [s - 1 for s in starts[1:]] + [len(body) - 1]
        return [(body[a], body[b]) for a, b in zip(starts, ends)]

    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("signature") == signature:
            return absolute(cached["debuts"]), cached["prompts"]
    except (OSError, ValueError, KeyError):
        pass
    if not (mode == "claude" or (client is not None or use_claude(config))):
        return None
    if mode == "auto" and not path.exists() and any(
            user_image(folder, n) for n in range(1, 100)):
        log(f"    scènes : vos images de l'épisode {episode.number} suivent l'ancien découpage, "
            "conservé (--scene-cut claude pour redécouper)")
        return None

    lines = "\n".join(f"[{k}] ({sec:.0f} s) {segments[i].text}"
                      for k, (i, sec) in enumerate(zip(body, seconds), 1))
    system = ("Tu es directeur artistique et monteur. Tu découpes la narration d'un livre en "
              "scènes visuelles et tu écris le prompt de l'image de chaque scène.")
    prompt = (
        f"Livre : « {book_title} »{f', de {author}' if author else ''}. "
        f"Épisode {episode.number} : {episode.title}.\n"
        f"Style imposé pour toutes les images : {style}.\n\n"
        + (f"Bible visuelle (à respecter pour les personnages et les lieux) :\n{bible}\n\n"
           if bible else "")
        + "Voici la narration, phrase par phrase, avec la durée de lecture estimée de "
        "chacune. Une scène = une image fixe affichée pendant que ses phrases sont lues.\n\n"
        "Découpage :\n"
        "- Commence une nouvelle scène dès que ce que le spectateur devrait voir change : "
        "lieu, personnage, objet, action, écran ou étape différents.\n"
        "- Regroupe les phrases consécutives qui montrent la même chose.\n"
        f"- Durée d'une scène : idéalement entre {low:.0f} et {high:.0f} secondes. Ne descends "
        f"sous {low:.0f} s que pour un changement très marqué, ne dépasse pas {high:.0f} s.\n"
        "- Les scènes se suivent sans trou : la première commence à la phrase 1.\n\n"
        "Pour chaque scène, donne le numéro de sa première phrase (« first ») et un prompt "
        "en anglais de 40 à 80 mots (« prompt ») qui montre précisément ce que disent ses "
        "phrases : sujet principal, décor, éclairage, cadrage, en reprenant le style imposé "
        "et la bible. Une seule image fixe au format paysage 16:9. Aucun texte, lettrage ni "
        "logo dans l'image. Évite la violence explicite.\n\n"
        f"<narration>\n{lines}\n</narration>")
    try:
        data = claude_client.ask_json(system, prompt, config, _CUT_SCHEMA, client,
                                      what=f"le découpage de l'épisode {episode.number}",
                                      max_tokens=32000)
        by_start: dict[int, str] = {}
        for scene in data.get("scenes", []):
            first, text = int(scene["first"]), str(scene["prompt"]).strip()
            if 1 <= first <= len(body) and text and first not in by_start:
                by_start[first] = text
        if not by_start:
            raise claude_client.ClaudeError("aucune scène reçue")
    except Exception as err:
        log(f"    scènes : découpage par Claude impossible, découpage par durée ({err})")
        return None
    starts = sorted(by_start)
    prompts_ = [by_start[s] for s in starts]
    starts = [0] + [s - 1 for s in starts[1:]]  # la première scène commence à la phrase 1
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"signature": signature, "debuts": starts, "prompts": prompts_},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    durations = [sum(seconds[a:b + 1]) for a, b in
                 zip(starts, [s - 1 for s in starts[1:]] + [len(body) - 1])]
    log(f"    scènes : découpage visuel par Claude, {len(starts)} scène(s) de "
        f"{min(durations):.0f} à {max(durations):.0f} s")
    return absolute(starts), prompts_


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
                          client=None, log=print, given: list[str] | None = None) -> list[dict]:
    """Écrit prompts.txt et scenes.json (sauf s'ils correspondent déjà à ces scènes).

    `given` : prompts déjà écrits (découpage visuel par Claude), un par scène.
    """
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
    if given is not None and len(given) == len(texts):
        prompts, author_label = list(given), "claude"
    elif texts and use_claude(config):
        try:
            prompts = _claude_prompts(texts, config, book_title, author, episode, bible, client)
            author_label = "claude"
        except Exception as err:
            log(f"    prompts : Claude indisponible, prompts simples utilisés ({err})")
    if len(prompts) != len(texts):
        prompts = [p for p in prompts[:len(texts)]]
        prompts += [template_prompt(t, config) for t in texts[len(prompts):]]

    scenes = [{"scene": i, "image": f"scene_{i:02d}.jpg", "extrait": _excerpt(t, 220),
               "texte": t,
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
                  f"Texte lu : « {sc['texte']} »", f"Prompt : {sc['prompt']}", ""]
    (folder / "prompts.txt").write_text("\n".join(lines), encoding="utf-8")
    return scenes


# --- Option « anime » : prompts d'animation ------------------------------------------
#
# Les scènes et leurs prompts d'images ne changent pas : chaque scène reçoit en plus
# un prompt de mouvement pour un générateur image→vidéo (Kling, Runway, Veo, Hailuo,
# Luma…). On crée d'abord l'image de la scène, puis on l'anime avec ce prompt, et on
# dépose le clip sous le nom scene_NN.mp4 à côté de scene_NN.jpg.

ANIM_FILE = "animations.json"
DEFAULT_MOTION = ("Slow cinematic camera push-in, subtle natural motion (light flicker, "
                  "drifting dust, gentle breathing of the subject), smooth and steady, "
                  "5 seconds, no cuts, no text.")


def template_motion(image_prompt: str) -> str:
    return f"{DEFAULT_MOTION} Scene: {image_prompt}"


def write_motion_prompts(config: dict, slug: str, episode, scenes: list[dict], *,
                         book_title: str, bible: str = "", client=None,
                         log=print) -> list[str]:
    """Prompts d'animation (un par scène), écrits dans animations.json et
    prompts_animation.txt, sauf s'ils correspondent déjà à ces scènes."""
    folder = episode_dir(config, slug, episode.number)
    folder.mkdir(parents=True, exist_ok=True)
    seconds = float(config.get("anime_clip_seconds", 5))
    signature = hashlib.sha1(json.dumps(
        [[sc["prompt"] for sc in scenes], [sc.get("texte", sc["extrait"]) for sc in scenes],
         seconds], ensure_ascii=False).encode()).hexdigest()[:12]
    path = folder / ANIM_FILE
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("signature") == signature:
            return cached["prompts"]
    except (OSError, ValueError, KeyError):
        pass

    motions: list[str] = []
    if scenes and (client is not None or use_claude(config)):
        blocks = "\n\n".join(
            f"<scene numero=\"{sc['scene']}\">\nTexte lu : {sc.get('texte', sc['extrait'])}\n"
            f"Image : {sc['prompt']}\n</scene>" for sc in scenes)
        system = ("Tu es réalisateur. Tu écris des prompts pour un générateur image→vidéo qui "
                  "anime, une par une, les illustrations d'un livre raconté en vidéo.")
        prompt = (
            f"Livre : « {book_title} ». Épisode {episode.number} : {episode.title}.\n\n"
            + (f"Bible visuelle :\n{bible}\n\n" if bible else "")
            + f"Pour chacune des {len(scenes)} scènes, l'image fixe existe déjà (son prompt "
            "est donné). Écris, en anglais, le prompt qui l'anime en un plan continu "
            f"d'environ {seconds:.0f} secondes : mouvement de caméra (lent et fluide), "
            "mouvement du sujet et de l'ambiance, en accord avec le texte lu pendant la scène. "
            "20 à 50 mots. Garde le cadrage, le décor et l'apparence des personnages de "
            "l'image : pas de nouveau personnage, pas de changement de lieu, pas de coupe. "
            "Aucun texte à l'écran. Évite la violence explicite.\n\n" + blocks)
        try:
            data = claude_client.ask_json(system, prompt, config, _SCHEMA, client,
                                          what=f"les animations de l'épisode {episode.number}")
            motions = [str(m).strip() for m in data.get("prompts", [])][:len(scenes)]
        except Exception as err:
            log(f"    animations : Claude indisponible, prompts simples utilisés ({err})")
    motions += [template_motion(sc["prompt"]) for sc in scenes[len(motions):]]

    path.write_text(json.dumps({"signature": signature, "prompts": motions},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"{book_title} — Épisode {episode.number} : {episode.title} — animations",
             "1. Créez l'image de la scène (prompts.txt), 2. animez-la avec ce prompt dans "
             "votre générateur image→vidéo, 3. déposez le clip ici : scene_01.mp4, "
             "scene_02.mp4… (MP4, MOV ou WEBM, paysage 16:9).", ""]
    for sc, motion in zip(scenes, motions):
        lines += [f"Scène {sc['scene']:02d}  →  scene_{sc['scene']:02d}.mp4",
                  f"Texte lu : « {sc.get('texte', sc['extrait'])} »",
                  f"Animation : {motion}", ""]
    (folder / "prompts_animation.txt").write_text("\n".join(lines), encoding="utf-8")
    return motions


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
        motions = _motions(folder)
        for k, sc in enumerate(data["scenes"]):
            done = "✅" if user_image(folder, sc["scene"]) else "⬜"
            lines += [f"{done} **scene_{sc['scene']:02d}** — {sc['prompt']}", ""]
            if k < len(motions):
                done = "✅" if user_clip(folder, sc["scene"]) else "⬜"
                lines += [f"   {done} 🎬 *animation* — {motions[k]}", ""]
    path = root / "PROMPTS.md"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _motions(folder: Path) -> list[str]:
    try:
        return json.loads((folder / ANIM_FILE).read_text(encoding="utf-8"))["prompts"]
    except (OSError, ValueError, KeyError):
        return []


def scene_status(config: dict, slug: str, number: int) -> list[dict]:
    """Scènes d'un épisode avec leur prompt et l'image déposée (pour le tableau de bord)."""
    folder = episode_dir(config, slug, number)
    try:
        scenes = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    except (OSError, ValueError, KeyError):
        return []
    motions = _motions(folder)
    for k, sc in enumerate(scenes):
        image = user_image(folder, sc["scene"])
        sc["fichier"] = image.name if image else None
        clip = user_clip(folder, sc["scene"])
        sc["clip"] = clip.name if clip else None
        sc["animation"] = motions[k] if k < len(motions) else None
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


def save_scene_clip(config: dict, slug: str, number: int, scene: int, suffix: str) -> Path:
    """Chemin où enregistrer le clip d'une scène (l'ancien, quel que soit son format, est retiré)."""
    suffix = suffix.lower()
    if suffix not in CLIP_EXTENSIONS:
        raise ValueError("Animation : formats acceptés MP4, MOV, WEBM ou MKV.")
    folder = episode_dir(config, slug, number)
    folder.mkdir(parents=True, exist_ok=True)
    delete_scene_clip(config, slug, number, scene)
    return folder / f"scene_{scene:02d}{suffix}"


def delete_scene_clip(config: dict, slug: str, number: int, scene: int) -> None:
    folder = episode_dir(config, slug, number)
    while (clip := user_clip(folder, scene)) is not None:
        clip.unlink()


def delete_scene_image(config: dict, slug: str, number: int, scene: int) -> None:
    folder = episode_dir(config, slug, number)
    while (image := user_image(folder, scene)) is not None:
        image.unlink()

