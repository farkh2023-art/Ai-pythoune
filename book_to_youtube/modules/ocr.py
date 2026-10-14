"""Reconnaissance du texte (OCR) des PDF scannés, dont les pages sont des images.

Deux moteurs :

* ``claude`` : Claude lit les images des pages (clé API Anthropic requise).
  Excellente qualité, y compris pour des captures d'écran ou des photos de pages ;
  les éléments d'interface (barre d'état, boutons…) sont ignorés.
* ``tesseract`` : moteur libre, hors ligne (``pkg install tesseract`` sous Termux,
  plus le fichier de langue ``fra.traineddata``).

Avec ``ocr_engine = "auto"`` (défaut), Claude est utilisé si une clé est
configurée, sinon Tesseract s'il est installé.

Images des pages : rendu par PyMuPDF s'il est installé, sinon extraction de
l'image de chaque page avec pypdf (cas des scans : une image par page), sinon
``pdftoppm`` (poppler). Le texte reconnu est mis en cache page par page dans
``data/ocr/`` : une reprise après interruption ne refait pas les pages déjà lues.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import claude_client

TESSERACT_LANGS = {"fr": "fra", "en": "eng"}
MAX_SIDE = 2000  # côté maximal envoyé à Claude (pixels)
RENDER_SIDE = 2400  # rendu des pages (PyMuPDF) : grand côté, en pixels

_SCHEMA = {
    "type": "object",
    "properties": {"pages": {"type": "array", "items": {"type": "string"}}},
    "required": ["pages"],
    "additionalProperties": False,
}

_SYSTEM = ("Tu transcris fidèlement des pages de livre photographiées, scannées ou capturées "
           "à l'écran, pour qu'elles soient ensuite lues à voix haute.")

_PROMPT = (
    "Voici {n} page(s) consécutive(s) d'un livre (pages {first} à {last}). Transcris le texte "
    "de chaque page, dans l'ordre : une chaîne par image dans le tableau « pages ».\n\n"
    "Consignes :\n"
    "- Recopie le texte exactement : ne résume pas, ne traduis pas, ne corrige pas le style, "
    "n'ajoute rien.\n"
    "- Sépare les paragraphes par une ligne vide. Recolle les mots coupés en fin de ligne.\n"
    "- Mets chaque titre (chapitre, partie, introduction…) seul sur sa ligne.\n"
    "- Ignore tout ce qui n'est pas le texte du livre : barre d'état du téléphone, boutons, "
    "menus, numéros de page, en-têtes et pieds de page répétés.\n"
    "- Texte brut uniquement, sans markdown (pas de #, *, ni puces décoratives).\n"
    "- Une page sans texte lisible donne une chaîne vide."
)


class OcrError(RuntimeError):
    pass


def tesseract_available() -> bool:
    return bool(shutil.which("tesseract"))


def engine(config: dict) -> str | None:
    """Moteur OCR utilisable : « claude », « tesseract » ou None."""
    choice = config.get("ocr_engine", "auto")
    if choice == "claude" or (choice == "auto" and claude_client.available()):
        return "claude"
    if choice in ("tesseract", "auto") and tesseract_available():
        return "tesseract"
    return None


# --- Images des pages ----------------------------------------------------------------

def page_images(pdf: Path) -> list[bytes]:
    """Une image (PNG ou JPEG, en octets) par page du PDF."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(pdf) as doc:
            return [page.get_pixmap(matrix=pymupdf.Matrix(*[RENDER_SIDE / max(page.rect.width,
                                                                              page.rect.height)] * 2)
                                    ).tobytes("png") for page in doc]
    try:
        return _pypdf_images(pdf)
    except Exception as err:
        if shutil.which("pdftoppm"):
            return _pdftoppm_images(pdf)
        raise OcrError(f"Impossible d'extraire les images des pages ({err}). "
                       "Installez PyMuPDF ou poppler (pdftoppm).") from err


def _pypdf_images(pdf: Path) -> list[bytes]:
    """Plus grande image de chaque page (un scan = une image pleine page)."""
    from pypdf import PdfReader

    images: list[bytes] = []
    for number, page in enumerate(PdfReader(str(pdf)).pages, 1):
        candidates = list(page.images)
        if not candidates:
            raise OcrError(f"page {number} sans image")
        best = max(candidates, key=lambda im: im.image.width * im.image.height)
        out = io.BytesIO()
        best.image.convert("RGB").save(out, "PNG")
        images.append(out.getvalue())
    return images


def _pdftoppm_images(pdf: Path) -> list[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-scale-to", str(RENDER_SIDE), "-png", str(pdf), f"{tmp}/page"],
                       check=True, capture_output=True)
        return [p.read_bytes() for p in sorted(Path(tmp).glob("page*.png"))]


def _for_claude(data: bytes) -> tuple[str, str]:
    """Image réduite si besoin, en JPEG : (type MIME, base64)."""
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    scale = MAX_SIDE / max(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return "image/jpeg", base64.b64encode(out.getvalue()).decode()


# --- Reconnaissance ------------------------------------------------------------------

def _claude_pages(images: list[bytes], first: int, config: dict, client=None) -> list[str]:
    content: list[dict] = []
    for data in images:
        media_type, b64 = _for_claude(data)
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64}})
    content.append({"type": "text", "text": _PROMPT.format(n=len(images), first=first,
                                                           last=first + len(images) - 1)})
    ocr_config = {**config, "summary_effort": config.get("ocr_effort", "low")}
    data = claude_client.ask_json(_SYSTEM, content, ocr_config, _SCHEMA, client,
                                  what=f"la transcription des pages {first}+")
    pages = [str(p).strip() for p in data.get("pages", [])]
    if len(pages) != len(images):
        raise OcrError(f"Claude a renvoyé {len(pages)} page(s) au lieu de {len(images)}.")
    return pages


def _tesseract_page(data: bytes, config: dict) -> str:
    lang = TESSERACT_LANGS.get(config.get("language", "fr"), "fra")
    result = subprocess.run(["tesseract", "stdin", "stdout", "-l", lang],
                            input=data, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip().splitlines()
        raise OcrError("Tesseract : " + (message[-1] if message else "échec")
                       + (f" (langue « {lang} » installée ?)" if lang != "eng" else ""))
    return tidy(result.stdout.decode(errors="replace"))


# Puces mal reconnues en début de ligne : « • », « e », « e_ », « # »…
_BULLET = re.compile(r"^[ \t]*(?:[•●◦▪■□·*#>_]+|[eo©®](?:_|\s))[ \t_]*", re.MULTILINE)


def tidy(text: str) -> str:
    """Nettoie la sortie de Tesseract (puces parasites, espaces)."""
    text = _BULLET.sub("", text.replace("\u200b", ""))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def recognize(pdf: Path, config: dict, client=None, log=print, progress=None) -> str:
    """Texte du PDF scanné, pages séparées par « \\f » (comme la lecture normale)."""
    name = engine(config) if client is None else "claude"
    if name is None:
        raise OcrError("aucun moteur OCR disponible")
    digest = hashlib.sha1(Path(pdf).read_bytes()).hexdigest()[:16]
    cache = Path(config.get("ocr_dir") or Path(config["data_dir"]) / "ocr") / f"{digest}-{name}"
    cache.mkdir(parents=True, exist_ok=True)

    images = page_images(Path(pdf))
    total = len(images)
    texts: list[str | None] = []
    for i in range(total):
        path = cache / f"page_{i + 1:04d}.txt"
        texts.append(path.read_text(encoding="utf-8") if path.exists() else None)
    todo = [i for i, t in enumerate(texts) if t is None]
    log(f"Reconnaissance du texte (OCR, {name}) : {total} page(s)"
        + (f", {total - len(todo)} déjà lue(s)" if len(todo) < total else ""))

    batch = max(1, int(config.get("ocr_batch_pages", 4))) if name == "claude" else 1
    pos = 0
    while pos < len(todo):
        # Lots de pages consécutives (meilleure continuité des paragraphes).
        group = [todo[pos]]
        while (len(group) < batch and pos + len(group) < len(todo)
               and todo[pos + len(group)] == group[-1] + 1):
            group.append(todo[pos + len(group)])
        pos += len(group)
        if progress:
            progress("ocr", title=f"pages {group[0] + 1}–{group[-1] + 1} / {total}"
                     if len(group) > 1 else f"page {group[0] + 1} / {total}")
        if name == "claude":
            results = _claude_pages([images[i] for i in group], group[0] + 1, config, client)
        else:
            results = [_tesseract_page(images[i], config) for i in group]
        for i, text in zip(group, results):
            texts[i] = text
            (cache / f"page_{i + 1:04d}.txt").write_text(text, encoding="utf-8")
        log(f"    OCR : {pos}/{len(todo)} page(s)")
    return "\f".join(t or "" for t in texts)
