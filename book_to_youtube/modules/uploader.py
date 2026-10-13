"""Publication YouTube, soumise à la validation du créateur de la chaîne.

Le pipeline ne publie jamais directement : chaque série produit un
``manifest.json`` où chaque épisode a un statut :

* ``a_valider`` : vidéo générée, en attente de relecture par le créateur ;
* ``valide``    : approuvée, peut être envoyée sur YouTube ;
* ``rejete``    : refusée (ne sera pas envoyée) ;
* ``publie``    : envoyée sur YouTube (``youtube_id`` renseigné).

``upload()`` n'envoie que les épisodes ``valide`` (YouTube Data API v3,
authentification OAuth du propriétaire de la chaîne).
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

PENDING, APPROVED, REJECTED, PUBLISHED = "a_valider", "valide", "rejete", "publie"
STATUSES = (PENDING, APPROVED, REJECTED, PUBLISHED)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Manifeste -----------------------------------------------------------------

def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_manifest(manifest: dict, path: str | Path) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def upsert_episode(manifest: dict, entry: dict) -> None:
    """Ajoute ou remplace un épisode ; une vidéo régénérée repasse « à valider »."""
    episodes = manifest.setdefault("episodes", [])
    for i, existing in enumerate(episodes):
        if existing["number"] == entry["number"]:
            if existing.get("status") == PUBLISHED:
                # Déjà en ligne : on garde l'historique de publication.
                entry = {**entry, "status": PUBLISHED, "youtube_id": existing.get("youtube_id"),
                         "published_at": existing.get("published_at")}
            episodes[i] = entry
            break
    else:
        episodes.append(entry)
    episodes.sort(key=lambda e: e["number"])


def set_status(manifest_path: str | Path, numbers: list[int] | None, status: str,
               note: str | None = None) -> list[int]:
    """Change le statut d'épisodes (tous les épisodes à valider si `numbers` est None)."""
    if status not in (APPROVED, REJECTED, PENDING):
        raise ValueError(f"Statut non modifiable manuellement : {status}")
    manifest = load_manifest(manifest_path)
    changed = []
    for ep in manifest["episodes"]:
        if ep["status"] == PUBLISHED:
            continue
        if (numbers is None and ep["status"] == PENDING) or (numbers and ep["number"] in numbers):
            ep["status"] = status
            ep["reviewed_at"] = _now()
            if note:
                ep["review_note"] = note
            changed.append(ep["number"])
    save_manifest(manifest, manifest_path)
    return changed


def summary(manifest_path: str | Path) -> str:
    manifest = load_manifest(manifest_path)
    rows = [f"Série : {manifest['book']['title']}  ({manifest_path})"]
    for ep in manifest["episodes"]:
        minutes = ep.get("duration", 0) / 60
        extra = f"  https://youtu.be/{ep['youtube_id']}" if ep.get("youtube_id") else ""
        rows.append(f"  #{ep['number']:>3}  [{ep['status']:<9}]  {minutes:5.1f} min  "
                    f"{ep['youtube_title']}{extra}")
    return "\n".join(rows)


def review(manifest_path: str | Path, input_fn=input, print_fn=print) -> None:
    """Relecture interactive des épisodes « à valider » par le créateur."""
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    pending = [ep for ep in manifest["episodes"] if ep["status"] == PENDING]
    if not pending:
        print_fn("Aucun épisode en attente de validation.")
        return
    for ep in pending:
        print_fn("\n" + "=" * 70)
        print_fn(f"Épisode {ep['number']} — {ep['youtube_title']}")
        print_fn(f"Durée      : {ep.get('duration', 0) / 60:.1f} min")
        print_fn(f"Vidéo      : {base / ep['video']}")
        print_fn(f"Miniature  : {base / ep['thumbnail']}")
        print_fn(f"Description:\n{ep['description']}")
        while True:
            answer = input_fn("[v]alider / [r]ejeter / [p]asser / [q]uitter ? ").strip().lower()
            if answer in ("v", "r", "p", "q"):
                break
        if answer == "q":
            break
        if answer == "p":
            continue
        ep["status"] = APPROVED if answer == "v" else REJECTED
        ep["reviewed_at"] = _now()
        if answer == "r":
            note = input_fn("Motif du rejet (facultatif) : ").strip()
            if note:
                ep["review_note"] = note
        save_manifest(manifest, manifest_path)
    print_fn("\n" + summary(manifest_path))


# --- YouTube Data API v3 ---------------------------------------------------------

def get_service(config: dict):
    """Client YouTube authentifié (OAuth « application de bureau »)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = Path(config["token_file"])
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets = Path(config["client_secrets"])
            if not secrets.exists():
                raise FileNotFoundError(
                    f"{secrets} introuvable : créez un identifiant OAuth « Application de "
                    "bureau » dans Google Cloud Console (API YouTube Data v3 activée) et "
                    "téléchargez-le sous ce nom."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload(manifest_path: str | Path, config: dict, numbers: list[int] | None = None,
           service=None, log=print) -> list[str]:
    """Envoie sur YouTube les épisodes validés par le créateur ; retourne leurs IDs."""
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    selected = [ep for ep in manifest["episodes"] if numbers is None or ep["number"] in numbers]

    blocked = [ep["number"] for ep in selected if ep["status"] in (PENDING, REJECTED)]
    if numbers is not None and blocked:
        raise PermissionError(
            f"Épisode(s) {blocked} non validé(s) par le créateur : lancez d'abord "
            f"`python main.py review {manifest_path}`."
        )
    to_send = [ep for ep in selected if ep["status"] == APPROVED]
    if not to_send:
        log("Aucun épisode validé à publier.")
        return []

    youtube = service or get_service(config)
    privacy = config.get("privacy_status", "private")
    if config.get("create_playlist", True) and not manifest.get("playlist_id"):
        manifest["playlist_id"] = _create_playlist(youtube, manifest, privacy)
        save_manifest(manifest, manifest_path)

    ids = []
    for ep in to_send:
        log(f"Envoi de l'épisode {ep['number']} : {ep['youtube_title']}")
        video_id = _upload_video(youtube, base / ep["video"], ep, config, privacy, log)
        ep.update(status=PUBLISHED, youtube_id=video_id, published_at=_now())
        save_manifest(manifest, manifest_path)  # sauvegarde après chaque envoi
        ids.append(video_id)

        _try(log, "miniature", lambda: youtube.thumbnails().set(
            videoId=video_id, media_body=_media(base / ep["thumbnail"], "image/jpeg")).execute())
        if config.get("upload_captions", True) and ep.get("subtitles"):
            _try(log, "sous-titres", lambda: youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": manifest.get("language", "fr"),
                                  "name": "Narration", "isDraft": False}},
                media_body=_media(base / ep["subtitles"], "application/octet-stream"),
            ).execute())
        if manifest.get("playlist_id"):
            _try(log, "playlist", lambda: youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": manifest["playlist_id"],
                                  "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
            ).execute())
        log(f"  -> https://youtu.be/{video_id} ({privacy})")
    return ids


def _media(path: Path, mimetype: str, resumable: bool = False):
    from googleapiclient.http import MediaFileUpload

    kwargs = {"chunksize": 8 * 1024 * 1024} if resumable else {}
    return MediaFileUpload(str(path), mimetype=mimetype, resumable=resumable, **kwargs)


def _create_playlist(youtube, manifest: dict, privacy: str) -> str:
    book = manifest["book"]
    response = youtube.playlists().insert(
        part="snippet,status",
        body={"snippet": {"title": book["title"][:150],
                          "description": f"{book['title']}" + (f" — {book['author']}" if book.get("author") else "")},
              "status": {"privacyStatus": privacy}},
    ).execute()
    return response["id"]


def _upload_video(youtube, path: Path, ep: dict, config: dict, privacy: str, log) -> str:
    from googleapiclient.errors import HttpError

    body = {
        "snippet": {
            "title": ep["youtube_title"][:100],
            "description": ep["description"][:5000],
            "tags": ep.get("tags", []),
            "categoryId": str(config.get("category_id", "27")),
            "defaultLanguage": ep.get("language", "fr"),
            "defaultAudioLanguage": ep.get("language", "fr"),
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    request = youtube.videos().insert(part="snippet,status", body=body,
                                      media_body=_media(path, "video/mp4", resumable=True))
    response, retries = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log(f"  {int(status.progress() * 100)} %")
        except HttpError as err:
            if err.resp.status not in (500, 502, 503, 504) or retries >= 5:
                raise
            retries += 1
            time.sleep(min(64, 2 ** retries) + random.random())
    return response["id"]


def _try(log, label: str, action) -> None:
    try:
        action()
    except Exception as err:  # non bloquant : la vidéo est déjà en ligne
        log(f"  Avertissement ({label}) : {err}")
