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
import re
import sys
import time
import unicodedata
from pathlib import Path

from config import CONFIG
from modules import (cleaner, illustrations, reader, scripter, splitter, subtitles, summarizer,
                     tts, uploader, video, visuals)

DESCRIPTION_LABELS = {
    "fr": {"episode": "Épisode", "voice": "Narration : voix de synthèse.",
           "summary": "Résumé du texte original, rédigé avec l'aide d'une IA.", "by": "de",
           "images": "Illustrations (Wikimedia Commons, licences libres) :"},
    "en": {"episode": "Episode", "voice": "Narration: synthetic voice.",
           "summary": "Summary of the original text, written with AI assistance.", "by": "by",
           "images": "Illustrations (Wikimedia Commons, free licenses):"},
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "livre"


def main(book_path: str | Path, config: dict | None = None,
         only: list[int] | None = None, force: bool = False) -> Path:
    """Exécute tout le pipeline ; retourne le chemin du manifeste de la série."""
    config = {**CONFIG, **(config or {})}
    video.check_ffmpeg()

    book = reader.load_book(book_path)                          # 1. Lecture
    texte = cleaner.clean(book.text)                            # 2. Nettoyage
    episodes = splitter.split(texte, config)                    # 3. Découpage
    if not episodes:
        raise SystemExit("Aucun texte exploitable dans ce livre.")

    slug = slugify(book.title)
    out_dir = Path(config["output_dir"]) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    splitter.save(episodes, Path(config["episodes_dir"]) / slug)
    manifest_path = out_dir / "manifest.json"
    manifest = (uploader.load_manifest(manifest_path) if manifest_path.exists() else
                {"book": {"title": book.title, "author": book.author, "source": str(book.source)},
                 "language": config["language"], "playlist_id": None, "episodes": []})

    total = len(episodes)
    print(f"« {book.title} » : {total} épisode(s), "
          f"{sum(e.word_count for e in episodes)} mots.")
    limit = config.get("max_episodes")
    for ep in episodes[:limit] if limit else episodes:
        if only and ep.number not in only:
            continue
        name = f"episode_{ep.number:03d}"
        video_path = out_dir / f"{name}.mp4"
        if video_path.exists() and not force:
            print(f"[{ep.number}/{total}] déjà généré, ignoré (--force pour régénérer).")
            continue
        started = time.monotonic()
        print(f"[{ep.number}/{total}] {ep.title} — {ep.word_count} mots")
        if config["narration_mode"] == "resume":
            original = ep
            ep = summarizer.summarize_episode(ep, config, book_title=book.title,
                                              author=book.author, total_episodes=total,
                                              cache_dir=Path(config["episodes_dir"]) / slug)
            print(f"    résumé : {ep.word_count} mots "
                  f"({summarizer.summary_ratio(original, ep):.0%} de l'original)")

        script = scripter.make_script(ep, config, book_title=book.title,   # 4. Script
                                      author=book.author, total_episodes=total)
        audio = tts.synthesize(script, Path(config["audio_dir"]) / slug / f"{name}.wav",
                               config)                                     # 5. Voix off
        srt = subtitles.write_srt(audio.timings, out_dir / f"{name}.srt")
        images_dir = Path(config["images_dir"]) / slug / name
        last = ep.number == total
        credits: list[str] = []
        cover = None
        if config["visual_style"] == "illustre":                           # 6. Visuels
            scenes = illustrations.plan_scenes(audio.timings, config["scene_seconds"])
            illustrations.illustrate(scenes, config, Path(config["images_dir"]) / "commons",
                                     log=print)
            n_images = len({s.illustration.path for s in scenes if s.illustration})
            print(f"    illustrations : {n_images} image(s) pour {len(scenes)} scène(s)"
                  + ("" if n_images else " — aucune trouvée, fond uni utilisé"))
            overlays = visuals.generate_overlays(script, audio.timings, images_dir, config,
                                                 book_title=book.title, last_episode=last)
            fallback = visuals.plain_background(images_dir / "fond.jpg", config)
            video.assemble_illustrated(audio.path, scenes, overlays, video_path,  # 7. Montage
                                       config, images_dir, fallback)
            credits = illustrations.credits(scenes)
            cover = next((s.illustration.path for s in scenes if s.illustration), None)
        else:
            slides = visuals.generate(script, audio.timings, images_dir, config,
                                      book_title=book.title, last_episode=last)
            video.assemble(audio.path, slides, video_path, config)
        thumb = visuals.thumbnail(out_dir / f"{name}.jpg", config, book_title=book.title,
                                  episode_number=ep.number, episode_title=ep.title,
                                  background=cover)

        uploader.upsert_episode(manifest, {                                # 8. À valider
            "number": ep.number,
            "title": ep.title,
            "youtube_title": youtube_title(book.title, ep, config),
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
        print(f"    -> {video_path} ({audio.duration / 60:.1f} min, "
              f"généré en {time.monotonic() - started:.0f} s)")

    print(f"\nManifeste : {manifest_path}")
    print("Étape suivante — validation par le créateur de la chaîne :")
    print(f"    python main.py review {manifest_path}")
    return manifest_path


def youtube_title(book_title: str, ep: splitter.Episode, config: dict) -> str:
    label = DESCRIPTION_LABELS.get(config["language"], DESCRIPTION_LABELS["fr"])["episode"]
    title = f"{book_title} — {label} {ep.number} : {ep.title}"
    if len(title) > 100:  # limite YouTube
        title = f"{book_title[:60].rstrip()}… — {label} {ep.number}"
    return title


def youtube_description(book: reader.Book, ep: splitter.Episode, total: int, config: dict,
                        credits: list[str] | None = None) -> str:
    labels = DESCRIPTION_LABELS.get(config["language"], DESCRIPTION_LABELS["fr"])
    excerpt = ep.text.replace("\n\n", " ")
    if len(excerpt) > 300:
        excerpt = excerpt[:300].rsplit(" ", 1)[0] + "…"
    author = f" {labels['by']} {book.author}" if book.author else ""
    return (f"{book.title}{author} — {labels['episode']} {ep.number}/{total} : {ep.title}\n\n"
            f"{excerpt}\n\n{labels['voice']}"
            + (f" {labels['summary']}" if config.get("narration_mode") == "resume" else "")
            + _credits_block(credits or [], labels))


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
    b.add_argument("--split", choices=["chapter", "duration", "words"], help="règle de découpage")
    b.add_argument("--minutes", type=float, help="durée cible d'un épisode (mode duration)")
    b.add_argument("--max-minutes", type=float, help="durée max d'un chapitre avant redécoupage")
    b.add_argument("--words", type=int, help="nombre de mots par épisode (mode words)")
    b.add_argument("--lang", choices=["fr", "en"], help="langue de la narration")
    b.add_argument("--narration", choices=["fidele", "resume"],
                   help="lecture intégrale ou résumé de chaque épisode (Claude)")
    b.add_argument("--summary-minutes", type=float, help="durée visée d'un épisode résumé")
    b.add_argument("--tts", choices=["edge", "gtts", "pyttsx3", "silent"], help="moteur TTS")
    b.add_argument("--voice", help="voix TTS (ex. fr-FR-HenriNeural)")
    b.add_argument("--rate", help="vitesse edge-tts (ex. +10%%)")
    b.add_argument("--style", choices=["slides", "card", "illustre"],
                   help="style visuel (illustre : images Wikimedia Commons + zoom lent)")
    b.add_argument("--scene-seconds", type=float, help="durée d'une scène illustrée (s)")
    b.add_argument("--no-zoom", action="store_true", help="illustrations fixes, sans zoom lent")
    b.add_argument("--theme", help="recherche d'image de secours, ex. « veld Afrique du Sud »")
    b.add_argument("--image-hint", help="mot ajouté à chaque recherche, ex. « painting »")
    b.add_argument("--background", type=Path, help="image de fond")
    b.add_argument("--resolution", help="ex. 1280x720")
    b.add_argument("--episodes", help="ne générer que ces épisodes (ex. 1,3,5-7)")
    b.add_argument("--max-episodes", type=int)
    b.add_argument("--force", action="store_true", help="régénérer les vidéos existantes")

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


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "build":
        overrides = {key: value for key, value in {
            "split_mode": args.split, "target_minutes": args.minutes,
            "max_minutes": args.max_minutes, "target_words": args.words,
            "language": args.lang, "narration_mode": args.narration,
            "summary_minutes": args.summary_minutes, "tts_engine": args.tts, "voice": args.voice,
            "tts_rate": args.rate, "visual_style": args.style,
            "scene_seconds": args.scene_seconds, "illustration_theme": args.theme,
            "illustration_hint": args.image_hint,
            "background_image": args.background, "max_episodes": args.max_episodes,
        }.items() if value is not None}
        if args.no_zoom:
            overrides["ken_burns"] = False
        if args.resolution:
            w, h = (int(v) for v in args.resolution.lower().split("x"))
            overrides["resolution"] = (w, h)
        main(args.book, overrides, only=parse_numbers(args.episodes), force=args.force)
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
