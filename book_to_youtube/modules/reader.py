"""Lecture du livre : extrait le texte brut d'un fichier PDF, EPUB, DOCX ou TXT.

Les sauts de page (PDF) sont marqués par le caractère ``\\f`` afin que le
nettoyage puisse détecter les en-têtes et pieds de page répétés.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".docx", ".txt", ".md", ".json"}


@dataclass
class Book:
    title: str
    author: str | None
    text: str
    source: Path
    metadata: dict = field(default_factory=dict)


def load(path: str | Path) -> str:
    """Retourne le texte brut du livre."""
    return load_book(path).text


def load_book(path: str | Path) -> Book:
    """Charge le livre et ses métadonnées (titre, auteur)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Livre introuvable : {path}")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Format non pris en charge : {ext} "
            f"(formats acceptés : {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )
    loader = {
        ".pdf": _load_pdf,
        ".epub": _load_epub,
        ".docx": _load_docx,
        ".txt": _load_txt,
        ".md": _load_txt,
        ".json": _load_curiosity,
    }[ext]
    text, meta = loader(path)
    title = (meta.get("title") or "").strip() or _title_from_filename(path)
    author = (meta.get("author") or "").strip() or None
    return Book(title=title, author=author, text=text, source=path, metadata=meta)


def _title_from_filename(path: Path) -> str:
    name = re.sub(r"[_\-]+", " ", path.stem).strip()
    return name[:1].upper() + name[1:] if name else "Livre"


def _load_curiosity(path: Path) -> tuple[str, dict]:
    """Questions « Savez-vous pourquoi… ??? » (fichier *.curiosite.json)."""
    from . import curiosity

    if not curiosity.is_curiosity(path):
        raise ValueError(f"{path.name} : seuls les fichiers « *{curiosity.SUFFIX} » sont lus.")
    return curiosity.to_book(curiosity.load(path))


def _load_txt(path: Path) -> tuple[str, dict]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), {}
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), {}


def _load_pdf(path: Path) -> tuple[str, dict]:
    try:
        import pymupdf  # PyMuPDF >= 1.24
    except ImportError:
        try:  # anciennes versions de PyMuPDF
            import fitz as pymupdf
        except ImportError:
            # PyMuPDF absent (ex. Android/Termux, sans paquet précompilé) : pypdf,
            # en pur Python, s'installe partout mais extrait le texte plus lentement.
            return _load_pdf_pypdf(path)

    with pymupdf.open(path) as doc:
        pages = [page.get_text("text") for page in doc]
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    return "\f".join(pages), meta


def _load_pdf_pypdf(path: Path) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "Lecture des PDF impossible : installez PyMuPDF (`pip install pymupdf`) "
            "ou, à défaut, pypdf (`pip install pypdf`)."
        ) from None

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    info = reader.metadata
    meta = {"title": info.title, "author": info.author} if info else {}
    return "\f".join(pages), {k: v for k, v in meta.items() if v}


def _load_docx(path: Path) -> tuple[str, dict]:
    import docx

    document = docx.Document(str(path))
    paragraphs = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style.startswith(("heading", "titre", "title")):
            # Les titres sont isolés pour faciliter la détection des chapitres.
            paragraphs.append(f"\n{text}\n")
        else:
            paragraphs.append(text)
    props = document.core_properties
    meta = {"title": props.title, "author": props.author}
    return "\n\n".join(paragraphs), meta


_BLOCK_TAGS = re.compile(
    r"</?(p|div|br|h[1-6]|li|blockquote|section|article|tr)\b[^>]*>", re.IGNORECASE
)
_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"<(script|style|head)\b.*?</\1>", "", markup, flags=re.I | re.S)
    # Les titres HTML deviennent des paragraphes isolés.
    markup = _HEADING.sub(lambda m: "\n\n" + m.group(1) + "\n\n", markup)
    markup = _BLOCK_TAGS.sub("\n\n", markup)
    markup = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(markup)
    text = re.sub(r"[ \t\r ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _load_epub(path: Path) -> tuple[str, dict]:
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    chunks = []
    # Respecte l'ordre de lecture (spine) plutôt que l'ordre des fichiers.
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        text = _html_to_text(item.get_content().decode("utf-8", errors="replace"))
        if text:
            chunks.append(text)

    def first(name: str) -> str | None:
        values = book.get_metadata("DC", name)
        return values[0][0] if values else None

    return "\n\n".join(chunks), {"title": first("title"), "author": first("creator")}
