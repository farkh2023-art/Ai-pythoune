"""Livre -> série de vidéos YouTube.

Exemples :
    python main.py build data/input/mon_livre.pdf
    python main.py build livre.epub --split duration --minutes 8 --style card
    python main.py status data/output/mon-livre/manifest.json
    python main.py review data/output/mon-livre/manifest.json
    python main.py upload data/output/mon-livre/manifest.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from config import CONFIG
from modules import (branding, cleaner, curiosity, illustrations, languages, ocr, prompts,
                     reader, scripter, splitter, subtitles, summarizer, titles, translator, tts,
                     uploader, video, visuals)

def slugify(text: str, default: str = "livre") -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or default


def series_slug(title: str, source: str | Path, lang: str, book_lang: str,
                short: bool = False) -> str:
    """Dossier de la série : titre du livre, plus la langue de la vidéo si elle diffère.

    Un titre sans lettres latines (arabe…) prend le nom du fichier, sinon une empreinte.
    """
    base = (slugify(title, "") or slugify(Path(source).stem, "")
            or "livre-" + hashlib.sha1(title.encode()).hexdigest()[:6])
    base = base if lang == book_lang else f"{base}-{lang}"
    return f"{base}-short" if short else base


# Format « Short » (YouTube Shorts) : vertical, sans générique, scènes courtes.
SHORT_DEFAULTS = {
    "resolution": (1080, 1920),
    "add_intro": False,
    "scene_seconds": 8,
    "scene_min_seconds": 3,
    "scene_max_seconds": 8,
    "segment_max_chars": 120,
}


def effective_config(config: dict | None) -> dict:
    """CONFIG + réglages du format Short (si demandé) + réglages explicites."""
    user = dict(config or {})
    short = user.get("video_format", CONFIG.get("video_format")) == "short"
    merged = {**CONFIG, **(SHORT_DEFAULTS if short else {}), **user}
    if short:
        w, h = merged["resolution"]
        if w > h:  # une résolution paysage (ex. 1280x720) devient verticale (720x1280)
            merged["resolution"] = (h, w)
    return merged


def book_language(text: str, config: dict) -> tuple[str, bool]:
    """(langue du livre, traduction nécessaire ?) — détectée si `book_language` = auto."""
    from modules import claude_client

    declared = config.get("book_language") or "auto"
    source = languages.detect(text) if declared == "auto" else declared
    target = config.get("language", "fr")
    if source == target:
        return source, False
    if config.get("translate", True) is False:
        return source, False
    if not claude_client.available():
        print(f"Avertissement : livre en {languages.get(source).name}, vidéo en "
              f"{languages.get(target).name}, mais sans clé Claude il ne peut pas être "
              "traduit : il sera lu tel quel.", file=sys.stderr)
        return source, False
    return source, True


def _drop_progress(folder: Path) -> None:
    """Dossier provisoire (avant de connaître la langue) : retiré s'il ne contient rien d'autre."""
    (folder / "progress.json").unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


class Progress:
    """Écrit l'étape en cours dans `progress.json` (lu par le tableau de bord)."""

    def __init__(self, path: Path, total: int, book_title: str = ""):
        self.path, self.total, self.book_title = path, total, book_title
        self.done: list[int] = []

    def __call__(self, stage: str, episode: int | None = None, title: str = "") -> None:
        if stage == "termine" and episode is not None:
            self.done.append(episode)
        data = {"stage": stage, "episode": episode, "title": title, "total": self.total,
                "book": self.book_title,
                "done": self.done, "updated": time.time()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def main(book_path: str | Path, config: dict | None = None,
         only: list[int] | None = None, force: bool = False,
         build_options: list[str] | None = None, prompts_only: bool = False) -> Path:
    """Exécute tout le pipeline ; retourne le chemin du manifeste de la série.

    `build_options` (les options de ligne de commande utilisées) est enregistré
    dans le manifeste pour pouvoir régénérer un épisode avec les mêmes réglages.
    `prompts_only` : prépare seulement les prompts d'images et les dossiers de scènes.
    """
    config = effective_config(config)
    video.check_ffmpeg()

    book = reader.load_book(book_path)                          # 1. Lecture
    check_has_text(book, config)
    slug = series_slug(book.title, book.source, "", "")
    out_dir = Path(config["output_dir"]) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(out_dir / "progress.json", 0, book.title)
    book = ensure_text(book, config, progress=progress)         #    (OCR si scanné)
    texte = cleaner.clean(book.text)                            # 2. Nettoyage
    # Langue du livre / de la vidéo : si elles diffèrent, Claude traduit, et la
    # série est distincte (suffixe « -ar », « -en »…) de celle dans la langue du livre.
    source_lang, translated = book_language(texte, config)
    series = series_slug(book.title, book.source, config["language"],
                         source_lang if translated else config["language"],
                         short=config.get("video_format") == "short")
    if series != slug:
        _drop_progress(out_dir)
        slug, out_dir = series, Path(config["output_dir"]) / series
        out_dir.mkdir(parents=True, exist_ok=True)
        progress = Progress(out_dir / "progress.json", 0, book.title)
    cache_dir = Path(config["episodes_dir"]) / slug
    if translated:
        print(f"Livre en {languages.get(source_lang).name}, vidéo en "
              f"{languages.get(config['language']).name} : traduction par Claude.")
        if config.get("narration_mode") != "resume":
            config = {**config, "translated_from": source_lang}
        (title,) = translator.translate_titles([book.title], config, source=source_lang,
                                               book_title=book.title, cache_dir=cache_dir)
        book = dataclasses.replace(book, title=title)
    if book.metadata.get("curiosite"):  # une question = un épisode, même courte
        config = {**config, "min_words": 0, "split_mode": config["split_mode"]
                  if config["split_mode"] == "single" else "chapter"}
    episodes = splitter.split(texte, {**config, "single_title": book.title})  # 3. Découpage
    if not episodes:
        raise SystemExit("Aucun texte exploitable dans ce livre.")
    # Titres « Partie N » remplacés par des titres proposés par Claude. Le titre
    # d'origine reste utilisé pour le résumé (cache inchangé, mêmes scènes).
    split_titles = {ep.number: ep.title for ep in episodes}
    episodes = titles.name_episodes(episodes, config, book_title=book.title,
                                    cache_dir=cache_dir, log=print)
    if translated:  # titres de chapitres traduits (les titres proposés le sont déjà)
        chapters = [ep for ep in episodes if not titles.DEFAULT_TITLE.match(
            split_titles[ep.number]) and ep.title == split_titles[ep.number]]
        names = translator.translate_titles([ep.title for ep in chapters], config,
                                            source=source_lang, book_title=book.title,
                                            cache_dir=cache_dir)
        renamed = {ep.number: name for ep, name in zip(chapters, names)}
        episodes = [dataclasses.replace(ep, title=renamed.get(ep.number, ep.title))
                    for ep in episodes]

    splitter.save(episodes, Path(config["episodes_dir"]) / slug)
    manifest_path = out_dir / "manifest.json"
    manifest = (uploader.load_manifest(manifest_path) if manifest_path.exists() else
                {"book": {"title": book.title, "author": book.author, "source": str(book.source)},
                 "language": config["language"], "playlist_id": None, "episodes": []})

    manifest["book"]["source"] = str(book.source)
    manifest["book"]["title"] = book.title
    manifest["book"]["language"] = source_lang
    manifest["language"] = config["language"]
    if build_options is not None:
        manifest["build_options"] = build_options
    channel = branding.load_channel(config)
    presentation = branding.ensure_presentation(
        config, slug, book_title=book.title, author=book.author, text=texte,
        channel_name=channel.narrated_name, log=print)
    brand = {"author": book.author, "channel": channel, "presentation": presentation}
    total = len(episodes)
    progress.total = total
    progress("decoupage")
    print(f"« {book.title} » : {total} épisode(s), "
          f"{sum(e.word_count for e in episodes)} mots.")
    # Prompts d'images et dossiers de scènes (style illustré ou préparation seule).
    with_prompts = prompts_only or config["visual_style"] == "illustre"
    bible = ""
    if with_prompts:
        progress("bible")
        bible = prompts.visual_bible(config, slug, book.title, book.author, texte, log=print)
    limit = config.get("max_episodes")
    for ep in episodes[:limit] if limit else episodes:
        if only and ep.number not in only:
            continue
        name = f"episode_{ep.number:03d}"
        video_path = out_dir / f"{name}.mp4"
        if video_path.exists() and not force and not prompts_only:
            print(f"[{ep.number}/{total}] déjà généré, ignoré (--force pour régénérer).")
            continue
        started = time.monotonic()
        print(f"[{ep.number}/{total}] {ep.title} — {ep.word_count} mots")
        if translated and config["narration_mode"] != "resume":
            progress("traduction", ep.number, ep.title)
            ep = translator.translate_episode(ep, config, source=source_lang,
                                              book_title=book.title, cache_dir=cache_dir)
        if config["narration_mode"] == "resume":
            progress("resume", ep.number, ep.title)
            original = ep
            ep = summarizer.summarize_episode(
                dataclasses.replace(ep, title=split_titles[ep.number]), config,
                book_title=book.title, author=book.author, total_episodes=total,
                cache_dir=Path(config["episodes_dir"]) / slug)
            ep = dataclasses.replace(ep, title=original.title)
            print(f"    résumé : {ep.word_count} mots "
                  f"({summarizer.summary_ratio(original, ep):.0%} de l'original)")

        script = scripter.make_script(ep, config, book_title=book.title,   # 4. Script
                                      author=book.author, total_episodes=total,
                                      channel_name=channel.narrated_name,
                                      presentation=presentation)
        scene_ranges = illustrations.plan_scene_ranges(script.segments, config)
        scene_prompts = None
        user_dir = illustrations.episode_dir(config, slug, ep.number)
        if with_prompts:
            progress("prompts", ep.number, ep.title)
            # Découpage visuel : une scène par « ce qu'on voit », prompts compris.
            plan = prompts.visual_scene_plan(config, slug, ep, script.segments,
                                             book_title=book.title, author=book.author,
                                             bible=bible, log=print)
            if plan is not None:
                scene_ranges, scene_prompts = plan
            scenes_info = prompts.write_episode_prompts(
                config, slug, ep, illustrations.scene_texts(script.segments, scene_ranges),
                book_title=book.title, author=book.author, bible=bible, log=print,
                given=scene_prompts)
            mine = sum(1 for sc in scenes_info if illustrations.user_image(user_dir, sc["scene"]))
            print(f"    scènes : {len(scenes_info)} (vos images : {mine}) — {user_dir}")
            if config.get("visual_mode") == "anime":                    # option « anime »
                prompts.write_motion_prompts(config, slug, ep, scenes_info,
                                             book_title=book.title, bible=bible, log=print)
                clips = sum(1 for sc in scenes_info
                            if illustrations.user_clip(user_dir, sc["scene"]))
                print(f"    animations : {clips}/{len(scenes_info)} clip(s) déposé(s) — "
                      f"prompts dans {user_dir / 'prompts_animation.txt'}")
        if prompts_only:
            progress("termine", ep.number, ep.title)
            continue

        progress("voix", ep.number, ep.title)
        audio = tts.synthesize(script, Path(config["audio_dir"]) / slug / f"{name}.wav",
                               config)                                     # 5. Voix off
        srt = subtitles.write_srt(audio.timings, out_dir / f"{name}.srt")
        images_dir = Path(config["images_dir"]) / slug / name
        last = ep.number == total
        credits: list[str] = []
        cover = None
        if config["visual_style"] == "illustre":                           # 6. Visuels
            progress("illustrations", ep.number, ep.title)
            scenes = illustrations.plan_scenes(audio.timings, scene_ranges)
            n_clips = (illustrations.attach_clips(scenes, user_dir)
                       if config.get("visual_mode") == "anime" else 0)
            illustrations.illustrate(scenes, config, Path(config["images_dir"]) / "commons",
                                     log=print, user_dir=user_dir)
            n_images = len({s.illustration.path for s in scenes if s.illustration})
            n_mine = sum(1 for s in scenes if illustrations.user_image(user_dir, s.number))
            print(f"    illustrations : {n_images} image(s) pour {len(scenes)} scène(s), "
                  f"{n_mine} scène(s) avec votre image"
                  + ("" if n_images or n_clips else " — aucune trouvée, fond uni utilisé"))
            if config.get("visual_mode") == "anime":
                print(f"    animations : {n_clips} scène(s) animée(s) sur {len(scenes)}")
            progress("visuels", ep.number, ep.title)
            overlays = visuals.generate_overlays(script, audio.timings, images_dir, config,
                                                 book_title=book.title, last_episode=last,
                                                 branding=brand)
            fallback = visuals.plain_background(images_dir / "fond.jpg", config)
            progress("montage", ep.number, ep.title)
            if config["ken_burns"] or n_clips:                              # 7. Montage
                video.assemble_illustrated(audio.path, scenes, overlays, video_path,
                                           config, images_dir, fallback)
            else:
                slides = visuals.composite_slides(overlays, audio.timings, scenes, fallback,
                                                  images_dir, config)
                video.assemble(audio.path, slides, video_path, config)
            credits = illustrations.credits(scenes)
            cover = next((s.illustration.path for s in scenes if s.illustration), None)
            if cover is None and n_clips:  # tout animé : miniature tirée du premier clip
                cover = video.clip_frame(next(s.clip for s in scenes if s.clip),
                                         images_dir / "miniature_clip.jpg")
        else:
            progress("visuels", ep.number, ep.title)
            slides = visuals.generate(script, audio.timings, images_dir, config,
                                      book_title=book.title, last_episode=last, branding=brand)
            progress("montage", ep.number, ep.title)
            video.assemble(audio.path, slides, video_path, config)
        thumb = visuals.thumbnail(out_dir / f"{name}.jpg", config, book_title=book.title,
                                  episode_number=ep.number, episode_title=ep.title,
                                  background=cover, branding=brand)

        uploader.upsert_episode(manifest, {                                # 8. À valider
            "number": ep.number,
            "title": ep.title,
            "youtube_title": youtube_title(book.title, ep, config, total, book.metadata),
            "description": youtube_description(book, ep, total, config, credits),
            "image_credits": credits,
            "tags": list(dict.fromkeys([*config.get("tags", []), book.title]
                                       + ([book.author] if book.author else []))),
            "language": config["language"],
            "video": video_path.name,
            "subtitles": srt.name,
            "thumbnail": thumb.name,
            "duration": round(audio.duration, 2),
            "words": ep.word_count,
            "narration": config["narration_mode"],
            "status": uploader.PENDING,
        })
        uploader.save_manifest(manifest, manifest_path)
        progress("termine", ep.number, ep.title)
        print(f"    -> {video_path} ({audio.duration / 60:.1f} min, "
              f"généré en {time.monotonic() - started:.0f} s)")

    if with_prompts:
        index = prompts.write_book_index(config, slug, book.title)
        print(f"Prompts d'images : {index}")
    if prompts_only:
        uploader.save_manifest(manifest, manifest_path)
    progress("fini")
    print(f"\nManifeste : {manifest_path}")
    print("Étape suivante — validation par le créateur de la chaîne :")
    print(f"    python main.py review {manifest_path}")
    return manifest_path


def is_scanned(book: reader.Book) -> bool:
    """PDF scanné (pages en images) : presque pas de texte extractible."""
    pages = book.text.count("\f") + 1
    return (book.source.suffix.lower() == ".pdf"
            and splitter.count_words(book.text) < 20 * pages)


def check_has_text(book: reader.Book, config: dict | None = None) -> None:
    """Message clair pour un livre sans texte, avant de lancer quoi que ce soit."""
    config = {**CONFIG, **(config or {})}
    if is_scanned(book):
        if ocr.engine(config) is None:
            pages = book.text.count("\f") + 1
            raise SystemExit(
                f"Ce PDF ne contient presque pas de texte ({splitter.count_words(book.text)} "
                f"mots pour {pages} pages) : c'est un livre scanné, dont les pages sont des "
                "images. Pour la reconnaissance automatique du texte (OCR), configurez la clé "
                "Claude (export ANTHROPIC_API_KEY=…) ou installez Tesseract "
                "(voir le README, « Livres scannés »).")
    elif splitter.count_words(book.text) == 0:
        raise SystemExit("Aucun texte exploitable dans ce livre.")


def ensure_text(book: reader.Book, config: dict | None = None, *, client=None,
                progress=None, log=print) -> reader.Book:
    """Livre avec son texte : OCR des pages si le PDF est scanné."""
    config = {**CONFIG, **(config or {})}
    if not is_scanned(book):
        return book
    try:
        text = ocr.recognize(book.source, config, client=client, log=log, progress=progress)
    except Exception as err:
        raise SystemExit(f"Reconnaissance du texte (OCR) impossible : {err}") from err
    if splitter.count_words(text) == 0:
        raise SystemExit("OCR : aucun texte reconnu dans ce PDF scanné.")
    return dataclasses.replace(book, text=text)


def youtube_title(book_title: str, ep: splitter.Episode, config: dict, total: int = 0,
                  meta: dict | None = None) -> str:
    label = languages.get(config["language"]).labels["episode"]
    meta = meta or {}
    proposed = meta.get("youtube_titles") or []
    if config.get("video_format") == "short":
        # Short : la question elle-même (ou le titre proposé par Claude), + #Shorts.
        base = (proposed[ep.number - 1] if ep.number <= len(proposed)
                and proposed[ep.number - 1] and not config.get("translated_from")
                else curiosity.question_of(ep.title))
        return f"{base[:90].rstrip()} #Shorts"
    if total == 1 and ep.title == book_title:  # une seule vidéo (ex. les 4 questions)
        return book_title[:100]
    title = f"{book_title} — {label} {ep.number} : {ep.title}"
    if len(title) > 100:  # limite YouTube
        title = f"{book_title[:60].rstrip()}… — {label} {ep.number}"
    return title


def youtube_description(book: reader.Book, ep: splitter.Episode, total: int, config: dict,
                        credits: list[str] | None = None) -> str:
    labels = languages.get(config["language"]).labels
    excerpt = ep.text.replace("\n\n", " ")
    if len(excerpt) > 300:
        excerpt = excerpt[:300].rsplit(" ", 1)[0] + "…"
    author = f" {labels['by']} {book.author}" if book.author else ""
    return (f"{book.title}{author} — {labels['episode']} {ep.number}/{total} : {ep.title}\n\n"
            f"{excerpt}\n\n{labels['voice']}"
            + (f" {labels['summary']}" if config.get("narration_mode") == "resume" else "")
            + (f" {labels['translation']}" if config.get("translated_from") else "")
            + _credits_block(credits or [], labels)
            + _hashtags(book.metadata, ep, total, config))


def _hashtags(meta: dict, ep: splitter.Episode, total: int, config: dict) -> str:
    """Hashtags des questions (mode curiosité) ; #Shorts pour un Short."""
    groups = (meta or {}).get("hashtags") or []
    if total == 1:
        tags = [t for group in groups for t in group]
    else:
        tags = list(groups[ep.number - 1]) if ep.number <= len(groups) else []
    if config.get("video_format") == "short":
        tags.append("Shorts")
    tags = list(dict.fromkeys(re.sub(r"\s+", "", t.lstrip("#")) for t in tags if t.strip()))
    return "\n\n" + " ".join(f"#{t}" for t in tags[:8]) if tags else ""


def _credits_block(credits: list[str], labels: dict, budget: int = 3500) -> str:
    """Crédits des illustrations, dans la limite de taille d'une description YouTube."""
    if not credits:
        return ""
    lines, size = [], 0
    for credit in credits:
        if size + len(credit) > budget:
            lines.append("…")
            break
        lines.append(f"- {credit}")
        size += len(credit) + 3
    return f"\n\n{labels['images']}\n" + "\n".join(lines)


def parse_numbers(value: str | None) -> list[int] | None:
    """« 1,3,5-7 » -> [1, 3, 5, 6, 7]."""
    if not value:
        return None
    numbers: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            numbers.extend(range(int(a), int(b) + 1))
        elif part:
            numbers.append(int(part))
    return numbers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transforme un livre en série de vidéos YouTube.")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="générer les vidéos d'un livre (PDF, EPUB, DOCX, TXT)")
    b.add_argument("book", type=Path)
    b.add_argument("--split", choices=["chapter", "duration", "words", "single"],
                   help="règle de découpage (single : tout en une seule vidéo)")
    b.add_argument("--format", choices=["standard", "short"],
                   help="standard (16:9) ou short (YouTube Shorts vertical 9:16, sans générique)")
    b.add_argument("--minutes", type=float, help="durée cible d'un épisode (mode duration)")
    b.add_argument("--max-minutes", type=float, help="durée max d'un chapitre avant redécoupage")
    b.add_argument("--words", type=int, help="nombre de mots par épisode (mode words)")
    b.add_argument("--lang", choices=list(languages.CODES),
                   help="langue de la vidéo (narration, générique, sous-titres) ; si le livre "
                        "est dans une autre langue, Claude le traduit")
    b.add_argument("--book-lang", choices=["auto", *languages.CODES],
                   help="langue du livre (auto = détectée)")
    b.add_argument("--narration", choices=["fidele", "resume"],
                   help="lecture intégrale ou résumé de chaque épisode (Claude)")
    b.add_argument("--summary-minutes", type=float, help="durée visée d'un épisode résumé")
    b.add_argument("--tts", choices=["edge", "gtts", "pyttsx3", "silent"], help="moteur TTS")
    b.add_argument("--voice", help="voix TTS (ex. fr-FR-HenriNeural)")
    b.add_argument("--rate", help="vitesse edge-tts (ex. +10%%)")
    b.add_argument("--style", choices=["slides", "card", "illustre"],
                   help="style visuel (illustre : images Wikimedia Commons + zoom lent)")
    b.add_argument("--scene-seconds", type=float, help="durée d'une scène illustrée (s)")
    b.add_argument("--visuels", choices=["images", "anime"],
                   help="option-images (images fixes) ou option-anime (vos clips animés "
                        "par scène, image sinon)")
    b.add_argument("--anime-fill", choices=["aller-retour", "boucle", "fige"],
                   help="clip plus court que sa scène : aller-retour, boucle ou image figée")
    b.add_argument("--scene-cut", choices=["auto", "claude", "duree"],
                   help="découpage des scènes : visuel par Claude (auto si clé) ou par durée")
    b.add_argument("--no-zoom", action="store_true",
                   help="illustrations fixes, sans zoom lent (montage bien plus rapide)")
    b.add_argument("--fps", type=int, help="images par seconde (défaut 25 ; 20 = plus rapide)")
    b.add_argument("--theme", help="recherche d'image de secours, ex. « veld Afrique du Sud »")
    b.add_argument("--image-hint", help="mot ajouté à chaque recherche, ex. « painting »")
    b.add_argument("--images", choices=["dossier+commons", "dossier", "commons"],
                   help="source des illustrations : vos images (dossier) et/ou Wikimedia Commons")
    b.add_argument("--image-style", help="style imposé dans les prompts d'images")
    b.add_argument("--background", type=Path, help="image de fond")
    b.add_argument("--resolution", help="ex. 1280x720")
    b.add_argument("--episodes", help="ne générer que ces épisodes (ex. 1,3,5-7)")
    b.add_argument("--max-episodes", type=int)
    b.add_argument("--force", action="store_true", help="régénérer les vidéos existantes")
    b.add_argument("--prompts-only", action="store_true",
                   help="préparer seulement les prompts d'images et les dossiers de scènes")

    cu = sub.add_parser("curiosite", help="« Savez-vous pourquoi… ??? » : 4 questions sur un "
                                          "mot-clé, rédigées par Claude")
    cu.add_argument("mot_cle")
    cu.add_argument("--domaine", default="", help="ex. « santé et corps humain »")
    cu.add_argument("--lang", choices=list(languages.CODES), help="langue des questions")
    cu.add_argument("--words", type=int, help="longueur d'un script (défaut 120 mots ≈ 45 s)")

    pr = sub.add_parser("presentation", help="faire rédiger la présentation du livre par Claude")
    pr.add_argument("book", type=Path)
    pr.add_argument("--force", action="store_true", help="remplacer la présentation existante")
    pr.add_argument("--lang", choices=list(languages.CODES), help="langue de la vidéo")

    for name, help_ in [("status", "état de la série"),
                        ("review", "relecture interactive par le créateur")]:
        sub.add_parser(name, help=help_).add_argument("manifest", type=Path)

    for name, help_ in [("approve", "valider des épisodes"), ("reject", "rejeter des épisodes")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("manifest", type=Path)
        p.add_argument("--episodes", help="ex. 1,3,5-7 (défaut : tous ceux à valider)")
        p.add_argument("--note", help="commentaire de relecture")

    u = sub.add_parser("upload", help="publier sur YouTube les épisodes validés")
    u.add_argument("manifest", type=Path)
    u.add_argument("--episodes", help="ex. 1,3,5-7 (défaut : tous les épisodes validés)")
    u.add_argument("--privacy", choices=["private", "unlisted", "public"])
    return parser


def write_presentation(book_path: str | Path, config: dict | None = None,
                       force: bool = False, client=None) -> str:
    """Rédige (ou relit) la présentation du livre et l'enregistre."""
    config = {**CONFIG, **(config or {})}
    book = reader.load_book(book_path)
    check_has_text(book, config)
    book = ensure_text(book, config, client=client)
    texte = cleaner.clean(book.text)
    source, translated = book_language(texte, config)
    slug = series_slug(book.title, book.source, config["language"],
                       source if translated else config["language"])
    if not force and (existing := branding.load_presentation(config, slug)):
        return existing
    channel = branding.load_channel(config)
    text = branding.generate_presentation(config, book_title=book.title, author=book.author,
                                          text=texte, channel_name=channel.narrated_name,
                                          client=client)
    branding.save_presentation(config, slug, text)
    return text


def write_curiosity(keyword: str, domain: str = "", lang: str | None = None,
                    words: int | None = None, client=None, log=print) -> Path:
    """Fait écrire les 4 questions d'un mot-clé et les enregistre dans data/input/."""
    config = {**CONFIG, **({"language": lang} if lang else {}),
              **({"curiosity_words": words} if words else {})}
    channel = branding.load_channel(config)
    data = curiosity.generate(keyword, config, domain=domain,
                              channel_name=channel.narrated_name, client=client)
    for n, q in enumerate(data["questions"], 1):
        log(f"{n}. [{q['angle']}] {q['question']}\n   à vérifier : {q['a_verifier']}")
    return curiosity.save(data, config["input_dir"])


def _reusable_options(argv: list[str], book: str) -> list[str]:
    """Options de `build` à réutiliser pour régénérer un épisode (sans livre ni sélection)."""
    args = list(argv[1:])  # retire « build »
    if book in args:
        args.remove(book)
    kept, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("--episodes", "--max-episodes"):
            skip = True
        elif arg not in ("--force", "--prompts-only") and not arg.startswith(
                ("--episodes=", "--max-episodes=")):
            kept.append(arg)
    return kept


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "build":
        overrides = {key: value for key, value in {
            "split_mode": args.split, "target_minutes": args.minutes,
            "max_minutes": args.max_minutes, "target_words": args.words,
            "language": args.lang, "book_language": args.book_lang,
            "narration_mode": args.narration,
            "summary_minutes": args.summary_minutes, "tts_engine": args.tts, "voice": args.voice,
            "tts_rate": args.rate, "visual_style": args.style,
            "scene_seconds": args.scene_seconds, "scene_cut": args.scene_cut,
            "visual_mode": args.visuels, "anime_fill": args.anime_fill,
            "video_format": args.format,
            "illustration_theme": args.theme,
            "illustration_hint": args.image_hint, "illustration_source": args.images,
            "image_style": args.image_style,
            "background_image": args.background, "max_episodes": args.max_episodes,
        }.items() if value is not None}
        if args.fps:
            overrides["fps"] = args.fps
        if args.no_zoom:
            overrides["ken_burns"] = False
        if args.resolution:
            w, h = (int(v) for v in args.resolution.lower().split("x"))
            overrides["resolution"] = (w, h)
        main(args.book, overrides, only=parse_numbers(args.episodes), force=args.force,
             prompts_only=args.prompts_only,
             build_options=_reusable_options(argv if argv is not None else sys.argv[1:],
                                             str(args.book)))
    elif args.command == "curiosite":
        path = write_curiosity(args.mot_cle, args.domaine, args.lang, args.words)
        print(f"\nQuestions enregistrées (modifiables) : {path}")
        print("Ensuite :")
        print(f"    python main.py build {path} --format short --style illustre   # 4 Shorts")
        print(f"    python main.py build {path} --split single --style illustre   # vidéo longue")
    elif args.command == "presentation":
        print(write_presentation(args.book, {"language": args.lang} if args.lang else None,
                                 force=args.force))
    elif args.command == "status":
        print(uploader.summary(args.manifest))
    elif args.command == "review":
        uploader.review(args.manifest)
    elif args.command in ("approve", "reject"):
        status = uploader.APPROVED if args.command == "approve" else uploader.REJECTED
        changed = uploader.set_status(args.manifest, parse_numbers(args.episodes), status, args.note)
        print(f"Épisode(s) {changed or 'aucun'} -> {status}")
        print(uploader.summary(args.manifest))
    elif args.command == "upload":
        config = dict(CONFIG)
        if args.privacy:
            config["privacy_status"] = args.privacy
        try:
            uploader.upload(args.manifest, config, parse_numbers(args.episodes))
        except PermissionError as err:
            print(err, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
