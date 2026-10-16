"""Tableau de bord local pour piloter l'application (import → publication).

    python dashboard.py            # puis ouvrir l'adresse affichée dans le navigateur

Le serveur (bibliothèque standard uniquement, compatible Termux) sert
``dashboard-AI-pythoune.html`` et une petite API JSON qui :

* liste les livres de ``data/input`` (et permet d'en importer depuis le navigateur) ;
* affiche chaque série : découpage, génération épisode par épisode, validation,
  publication ;
* lance les commandes ``build`` et ``upload`` de ``main.py`` en arrière-plan,
  avec leur journal en direct ;
* valide / rejette les épisodes et modifie leurs titres et descriptions.

Sécurité : le serveur n'écoute que sur l'appareil lui-même (127.0.0.1) et
chaque requête doit porter le jeton secret affiché au démarrage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import CONFIG
from modules import branding, illustrations, prompts, uploader
from modules.reader import SUPPORTED_EXTENSIONS

BASE_DIR = Path(__file__).resolve().parent
PAGE = BASE_DIR / "dashboard-AI-pythoune.html"
LOG_DIR = Path(CONFIG["data_dir"]) / "logs"
MEDIA_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".srt": "text/plain; charset=utf-8"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".txt": "text/plain; charset=utf-8",
               ".md": "text/markdown; charset=utf-8"}

# Options du formulaire « Générer » -> options de `main.py build`.
BUILD_FLAGS = {
    "split": "--split", "minutes": "--minutes", "max_minutes": "--max-minutes",
    "words": "--words", "lang": "--lang", "narration": "--narration",
    "summary_minutes": "--summary-minutes", "tts": "--tts", "voice": "--voice",
    "rate": "--rate", "style": "--style", "scene_seconds": "--scene-seconds",
    "theme": "--theme", "image_hint": "--image-hint", "resolution": "--resolution",
    "fps": "--fps", "episodes": "--episodes", "max_episodes": "--max-episodes",
    "images": "--images", "image_style": "--image-style", "scene_cut": "--scene-cut",
}
BUILD_SWITCHES = {"no_zoom": "--no-zoom", "force": "--force"}


# --- Tâches en arrière-plan ---------------------------------------------------------

@dataclass
class Job:
    id: str
    kind: str  # "build" | "upload"
    label: str
    cmd: list[str]
    book: str | None = None
    slug: str | None = None
    status: str = "en_cours"  # en_cours | termine | erreur | annule
    started: float = field(default_factory=time.time)
    ended: float | None = None
    returncode: int | None = None
    error: str | None = None  # dernière ligne utile du journal en cas d'échec

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"{self.id}.log"


def _error_summary(log_path: Path) -> str:
    """Message d'erreur lisible : dernière ligne non vide du journal (fin de la trace)."""
    try:
        lines = [ln.strip() for ln in log_path.read_text(encoding="utf-8", errors="replace")
                 .splitlines() if ln.strip()]
    except OSError:
        return "Journal illisible."
    return lines[-1][:600] if lines else "La tâche s'est arrêtée sans message."


class JobRunner:
    """Une seule tâche à la fois : un téléphone n'a pas les ressources pour plus."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: list[Job] = []
        self.process: subprocess.Popen | None = None

    @property
    def current(self) -> Job | None:
        return next((j for j in self.jobs if j.status == "en_cours"), None)

    def start(self, kind: str, label: str, args: list[str], **extra) -> Job:
        with self.lock:
            if self.current:
                raise RuntimeError("Une tâche est déjà en cours : attendez sa fin ou annulez-la.")
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            job = Job(id=time.strftime("%Y%m%d-%H%M%S") + f"-{kind}", kind=kind, label=label,
                      cmd=[sys.executable, "-u", "main.py", *args], **extra)
            log = open(job.log_path, "w", encoding="utf-8")
            log.write("$ python main.py " + " ".join(args) + "\n\n")
            log.flush()
            self.process = subprocess.Popen(
                job.cmd, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=(os.name == "posix"),  # pour arrêter aussi ffmpeg
            )
            self.jobs.append(job)
            self.jobs[:] = self.jobs[-20:]
            threading.Thread(target=self._wait, args=(job, self.process, log), daemon=True).start()
            return job

    def _wait(self, job: Job, process: subprocess.Popen, log) -> None:
        job.returncode = process.wait()
        log.close()
        job.ended = time.time()
        if job.status == "en_cours":
            job.status = "termine" if job.returncode == 0 else "erreur"
        if job.status == "erreur":
            job.error = _error_summary(job.log_path)


    def cancel(self) -> bool:
        job, process = self.current, self.process
        if not job or not process:
            return False
        job.status = "annule"
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        return True


RUNNER = JobRunner()


# --- État des livres et des séries ----------------------------------------------------

def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def list_books() -> list[dict]:
    folder = Path(CONFIG["input_dir"])
    books = []
    for path in sorted(folder.glob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            stat = path.stat()
            books.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
    return books


def list_series(job: Job | None) -> list[dict]:
    out_root = Path(CONFIG["output_dir"])
    series = []
    for folder in sorted(p for p in out_root.glob("*") if p.is_dir()):
        manifest = _read_json(folder / "manifest.json")
        progress = _read_json(folder / "progress.json")
        if not manifest and not progress:
            continue
        slug = folder.name
        planned = _read_json(Path(CONFIG["episodes_dir"]) / slug / "index.json", [])
        manifest = manifest or {"book": {"title": (progress or {}).get("book") or slug},
                                "episodes": []}
        episodes = manifest.get("episodes", [])
        for ep in episodes:
            for key in ("video", "thumbnail", "subtitles"):
                if ep.get(key):
                    ep[f"{key}_url"] = f"/media/{urllib.parse.quote(slug)}/{urllib.parse.quote(ep[key])}"
        # La progression ne compte que si elle appartient à la tâche en cours.
        active = bool(job and job.kind in ("build", "prompts") and progress
                      and progress.get("updated", 0) >= job.started - 1)
        counts = {s: sum(1 for e in episodes if e.get("status") == s) for s in uploader.STATUSES}
        images = {}
        for number in [p_["number"] for p_ in planned] or [e["number"] for e in episodes]:
            scenes = prompts.scene_status(CONFIG, slug, number)
            if scenes:
                images[number] = {"scenes": len(scenes),
                                  "mine": sum(1 for sc in scenes if sc["fichier"])}
        series.append({
            "slug": slug,
            "title": manifest["book"].get("title", slug),
            "author": manifest["book"].get("author"),
            "source": Path(manifest["book"].get("source") or "").name,
            "planned": planned,
            "total": len(planned) or (progress or {}).get("total") or len(episodes),
            "episodes": episodes,
            "counts": counts,
            "playlist_id": manifest.get("playlist_id"),
            "build_options": manifest.get("build_options", []),
            "progress": progress if active else None,
            "presentation": branding.load_presentation(CONFIG, slug),
            "images": images,
            "prompts_index": (f"/illus/{slug}/PROMPTS.md" if (
                illustrations.illustrations_root(CONFIG) / slug / "PROMPTS.md").exists() else None),
            "manifest": str(folder / "manifest.json"),
        })
    return series


def environment() -> dict:
    def has(module: str) -> bool:
        import importlib.util
        return importlib.util.find_spec(module) is not None

    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "pdf": has("pymupdf") or has("fitz") or has("pypdf"),
        "edge_tts": has("edge_tts"),
        "gtts": has("gtts"),
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "tesseract": bool(shutil.which("tesseract")),
        "youtube_api": has("googleapiclient") and has("google_auth_oauthlib"),
        "client_secrets": Path(CONFIG["client_secrets"]).exists(),
        "youtube_token": Path(CONFIG["token_file"]).exists(),
    }


def channel_state() -> dict:
    channel = branding.load_channel(CONFIG)
    return {"name": channel.name, "spoken_name": channel.spoken_name, "slogan": channel.slogan,
            "narrated_name": channel.narrated_name,
            "logo_url": (f"/channel/{channel.logo.name}?v={int(channel.logo.stat().st_mtime)}"
                         if channel.logo else None)}


def state() -> dict:
    job = RUNNER.current or (RUNNER.jobs[-1] if RUNNER.jobs else None)
    return {
        "channel": channel_state(),
        "books": list_books(),
        "series": list_series(RUNNER.current),
        "job": asdict(job) if job else None,
        "jobs": [asdict(j) for j in reversed(RUNNER.jobs[-10:])],
        "env": environment(),
    }


# --- Actions --------------------------------------------------------------------------

def _book_path(name: str) -> Path:
    path = Path(CONFIG["input_dir"]) / Path(name).name
    if not path.is_file():
        raise FileNotFoundError(f"Livre introuvable : {name}")
    return path


def _series_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise ValueError("Série invalide.")
    folder = Path(CONFIG["output_dir"]) / slug
    if not (folder / "manifest.json").exists():
        raise FileNotFoundError(f"Série introuvable : {slug}")
    return folder


def build_args(book: str, options: dict) -> list[str]:
    args = ["build", str(_book_path(book))]
    for key, flag in BUILD_FLAGS.items():
        value = options.get(key)
        if value not in (None, ""):
            args += [flag, str(value)]
    for key, flag in BUILD_SWITCHES.items():
        if options.get(key):
            args.append(flag)
    return args


def action_build(body: dict) -> Job:
    args = build_args(body["book"], body.get("options", {}))
    return RUNNER.start("build", f"Génération — {body['book']}", args, book=body["book"])


def action_prompts(body: dict) -> Job:
    """Prépare les prompts d'images et les dossiers de scènes (sans voix ni vidéo)."""
    args = build_args(body["book"], body.get("options", {})) + ["--prompts-only"]
    return RUNNER.start("prompts", f"Préparation des images — {body['book']}", args,
                        book=body["book"])


def _series_slug(slug: str) -> str:
    """Série existante (dossier de sortie créé) ; le manifeste n'est pas nécessaire."""
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise ValueError("Série invalide.")
    if not (Path(CONFIG["output_dir"]) / slug).is_dir():
        raise FileNotFoundError(f"Série introuvable : {slug}")
    return slug


def action_channel(body: dict) -> dict:
    branding.save_channel(CONFIG, str(body.get("name", ""))[:100],
                          str(body.get("slogan", ""))[:150], str(body.get("spoken_name", ""))[:100])
    return channel_state()


def action_presentation(body: dict) -> dict:
    text = str(body.get("text", ""))[:3000]
    branding.save_presentation(CONFIG, _series_slug(body["slug"]), text)
    return {"text": text}


def _book_title(slug: str) -> str:
    manifest = _read_json(Path(CONFIG["output_dir"]) / slug / "manifest.json", {})
    return manifest.get("book", {}).get("title") or slug


def action_presentation_draft(body: dict) -> dict:
    """Présentation proposée par Claude (non enregistrée : le créateur la relit d'abord)."""
    from modules import claude_client, cleaner, reader

    if not claude_client.available():
        raise ValueError("Clé API Anthropic absente : lancez « export ANTHROPIC_API_KEY=… » "
                         "avant « python dashboard.py ».")
    folder = _series_dir(body["slug"])
    manifest = uploader.load_manifest(folder / "manifest.json")
    source = Path(manifest["book"].get("source", ""))
    if not source.is_file():
        raise FileNotFoundError(f"Livre source introuvable : {source}")
    book = reader.load_book(source)
    text = branding.generate_presentation(
        CONFIG, book_title=book.title, author=book.author, text=cleaner.clean(book.text),
        channel_name=branding.load_channel(CONFIG).narrated_name)
    return {"text": text}


def scenes_for(slug: str, number: int) -> list[dict]:
    _series_slug(slug)
    scenes = prompts.scene_status(CONFIG, slug, number)
    for sc in scenes:
        if sc["fichier"]:
            stamp = int((illustrations.episode_dir(CONFIG, slug, number) / sc["fichier"])
                        .stat().st_mtime)
            sc["url"] = f"/illus/{slug}/episode_{number:03d}/{sc['fichier']}?v={stamp}"
    return scenes


def action_delete_scene_image(body: dict) -> dict:
    slug, number, scene = _series_slug(body["slug"]), int(body["episode"]), int(body["scene"])
    prompts.delete_scene_image(CONFIG, slug, number, scene)
    prompts.write_book_index(CONFIG, slug, _book_title(slug))
    return {"scenes": scenes_for(slug, number)}


def action_regenerate(body: dict) -> Job:
    folder = _series_dir(body["slug"])
    manifest = uploader.load_manifest(folder / "manifest.json")
    source = Path(manifest["book"].get("source", ""))
    if not source.is_file():
        raise FileNotFoundError(f"Livre source introuvable : {source}")
    number = int(body["episode"])
    args = ["build", str(source), *manifest.get("build_options", []),
            "--episodes", str(number), "--force"]
    return RUNNER.start("build", f"Régénération — épisode {number}", args,
                        book=source.name, slug=body["slug"])


def action_review(body: dict) -> list[int]:
    folder = _series_dir(body["slug"])
    status = {"approve": uploader.APPROVED, "reject": uploader.REJECTED,
              "pending": uploader.PENDING}[body["action"]]
    numbers = [int(n) for n in body.get("episodes", [])]
    if not numbers:
        raise ValueError("Aucun épisode sélectionné.")
    return uploader.set_status(folder / "manifest.json", numbers, status, body.get("note") or None)


def action_edit(body: dict) -> dict:
    path = _series_dir(body["slug"]) / "manifest.json"
    manifest = uploader.load_manifest(path)
    ep = next((e for e in manifest["episodes"] if e["number"] == int(body["episode"])), None)
    if ep is None:
        raise FileNotFoundError("Épisode introuvable.")
    if ep["status"] == uploader.PUBLISHED:
        raise PermissionError("Épisode déjà publié : modifiez-le directement sur YouTube.")
    for key, limit in (("youtube_title", 100), ("description", 5000)):
        if key in body:
            ep[key] = str(body[key])[:limit]
    uploader.save_manifest(manifest, path)
    return ep


def action_manual_export(body: dict) -> dict:
    """Envoi manuel : prépare les fichiers d'un épisode validé dans Téléchargements."""
    folder = _series_dir(body["slug"])
    root = Path(CONFIG.get("export_dir") or uploader.default_export_root(CONFIG))
    return uploader.export_for_manual_upload(folder / "manifest.json", int(body["episode"]), root)


def action_mark_published(body: dict) -> dict:
    folder = _series_dir(body["slug"])
    return uploader.mark_published(folder / "manifest.json", int(body["episode"]),
                                   str(body.get("link", "")))


def action_publish(body: dict) -> Job:
    folder = _series_dir(body["slug"])
    args = ["upload", str(folder / "manifest.json")]
    if body.get("episodes"):
        args += ["--episodes", ",".join(str(int(n)) for n in body["episodes"])]
    if body.get("privacy") in ("private", "unlisted", "public"):
        args += ["--privacy", body["privacy"]]
    return RUNNER.start("upload", f"Publication YouTube — {body['slug']}", args, slug=body["slug"])


def read_log(job_id: str, offset: int) -> dict:
    job = next((j for j in RUNNER.jobs if j.id == job_id), None)
    if job is None:
        raise FileNotFoundError("Tâche inconnue.")
    try:
        with open(job.log_path, "rb") as log:
            log.seek(offset)
            chunk = log.read(256_000)
    except OSError:
        chunk = b""
    text = chunk.decode("utf-8", errors="replace")
    return {"text": text, "offset": offset + len(chunk), "status": job.status}


# --- Serveur HTTP -----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "AI-pythoune"
    token = ""

    def log_message(self, fmt, *args):  # journal discret
        pass

    # Utilitaires
    def _authorized(self, query: dict) -> bool:
        supplied = self.headers.get("X-Token") or query.get("token", [""])[0]
        return secrets.compare_digest(supplied, self.token)

    def _json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, message: str, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # Routes
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path in ("/", "/dashboard-AI-pythoune.html"):
            return self._send_file(PAGE, "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if not self._authorized(query):
            return self._error("Jeton manquant ou invalide : utilisez l'adresse affichée "
                               "au démarrage de dashboard.py.", HTTPStatus.UNAUTHORIZED)
        try:
            if url.path == "/api/state":
                return self._json(state())
            if url.path == "/api/log":
                return self._json(read_log(query["id"][0], int(query.get("offset", ["0"])[0])))
            if url.path == "/api/scenes":
                return self._json({"scenes": scenes_for(query["slug"][0],
                                                        int(query["episode"][0]))})
            if url.path.startswith("/channel/"):
                path = branding.channel_dir(CONFIG) / Path(urllib.parse.unquote(url.path)).name
                if path.name not in branding.LOGO_NAMES or not path.is_file():
                    return self._error("Logo introuvable.", HTTPStatus.NOT_FOUND)
                return self._send_file(path, IMAGE_TYPES[path.suffix.lower()])
            if url.path.startswith("/illus/"):
                parts = [urllib.parse.unquote(p) for p in url.path.split("/")[2:]]
                root = illustrations.illustrations_root(CONFIG).resolve()
                path = root.joinpath(_series_slug(parts[0]), *parts[1:]).resolve()
                if (not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_TYPES
                        or not path.is_file()):
                    return self._error("Fichier introuvable.", HTTPStatus.NOT_FOUND)
                return self._send_file(path, IMAGE_TYPES[path.suffix.lower()])
            if url.path.startswith("/media/"):
                _, _, slug, name = url.path.split("/", 3)
                folder = _series_dir(urllib.parse.unquote(slug))
                path = folder / Path(urllib.parse.unquote(name)).name
                if path.suffix not in MEDIA_TYPES or not path.is_file():
                    return self._error("Fichier introuvable.", HTTPStatus.NOT_FOUND)
                return self._send_file(path, MEDIA_TYPES[path.suffix])
        except (FileNotFoundError, KeyError, ValueError) as err:
            return self._error(str(err), HTTPStatus.NOT_FOUND)
        self._error("Adresse inconnue.", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self._authorized(query):
            return self._error("Jeton manquant ou invalide.", HTTPStatus.UNAUTHORIZED)
        try:
            if url.path == "/api/books":
                return self._json(self._receive_book(query))
            if url.path == "/api/logo":
                suffix = Path(query.get("name", [""])[0]).suffix
                branding.save_logo(CONFIG, self._raw_body(5_000_000), suffix)
                return self._json(channel_state())
            if url.path == "/api/scene-image":
                slug = _series_slug(query["slug"][0])
                number, scene = int(query["episode"][0]), int(query["scene"][0])
                suffix = Path(query.get("name", [""])[0]).suffix
                prompts.save_scene_image(CONFIG, slug, number, scene,
                                         self._raw_body(25_000_000), suffix)
                prompts.write_book_index(CONFIG, slug, _book_title(slug))
                return self._json({"scenes": scenes_for(slug, number)})
            body = self._body()
            routes = {
                "/api/prompts": lambda: asdict(action_prompts(body)),
                "/api/channel": lambda: action_channel(body),
                "/api/presentation": lambda: action_presentation(body),
                "/api/presentation-draft": lambda: action_presentation_draft(body),
                "/api/scene-image-delete": lambda: action_delete_scene_image(body),
                "/api/build": lambda: asdict(action_build(body)),
                "/api/regenerate": lambda: asdict(action_regenerate(body)),
                "/api/publish": lambda: asdict(action_publish(body)),
                "/api/review": lambda: {"changed": action_review(body)},
                "/api/edit": lambda: action_edit(body),
                "/api/manual-export": lambda: action_manual_export(body),
                "/api/mark-published": lambda: action_mark_published(body),
                "/api/cancel": lambda: {"cancelled": RUNNER.cancel()},
            }
            if url.path not in routes:
                return self._error("Adresse inconnue.", HTTPStatus.NOT_FOUND)
            return self._json(routes[url.path]())
        except RuntimeError as err:
            return self._error(str(err), HTTPStatus.CONFLICT)
        except (FileNotFoundError, KeyError, ValueError, PermissionError) as err:
            return self._error(str(err) or err.__class__.__name__)
        except Exception as err:  # erreur de l'API Claude, réseau…
            return self._error(f"{err.__class__.__name__} : {err}", HTTPStatus.BAD_GATEWAY)

    def _raw_body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError(f"Fichier trop volumineux (maximum {limit // 1_000_000} Mo).")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("Envoi interrompu.")
        return data

    def _receive_book(self, query: dict) -> dict:
        """Import d'un livre depuis le navigateur (corps brut de la requête)."""
        name = Path(query.get("name", [""])[0]).name
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Format non pris en charge (acceptés : "
                             f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}).")
        name = re.sub(r"[^\w.\-]+", "_", name)
        length = int(self.headers.get("Content-Length") or 0)
        target = Path(CONFIG["input_dir"]) / name
        tmp = target.with_suffix(target.suffix + ".part")
        with open(tmp, "wb") as out:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
        if remaining:
            tmp.unlink(missing_ok=True)
            raise ValueError("Envoi interrompu.")
        tmp.replace(target)
        return {"name": name, "size": length}

    def _send_file(self, path: Path, content_type: str):
        """Envoi d'un fichier, avec prise en charge des plages (lecture vidéo et avance rapide)."""
        size = path.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:  # « bytes=-500 » : les 500 derniers octets
                start = max(0, size - int(match.group(2) or 0))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with open(path, "rb") as source:
                source.seek(start)
                remaining = end - start + 1 if size else 0
                while remaining > 0:
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # le lecteur vidéo a interrompu la lecture : normal


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    Handler.token = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer((host, port), Handler)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown_host}:{port}/#token={Handler.token}"
    print("Tableau de bord AI-pythoune")
    print(f"  Ouvrez : {url}")
    if host not in ("127.0.0.1", "localhost"):
        print("  Attention : le tableau de bord est accessible depuis le réseau local. "
              "Ne partagez pas cette adresse (elle contient le jeton).")
    print("  Ctrl+C pour arrêter.")
    if open_browser:
        opener = shutil.which("termux-open-url")
        if opener:
            subprocess.Popen([opener, url])
        else:
            import webbrowser
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du tableau de bord.")
        RUNNER.cancel()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tableau de bord local d'AI-pythoune.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="adresse d'écoute (défaut : 127.0.0.1, cet appareil uniquement)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="ouvrir le navigateur au démarrage")
    args = parser.parse_args()
    serve(args.host, args.port, args.open)
