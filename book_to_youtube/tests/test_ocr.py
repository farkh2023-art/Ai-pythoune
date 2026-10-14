import io
import json
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_prompts import FakeClaude

import main
from modules import ocr, reader


def cfg(tmp_path, **extra):
    return {"data_dir": tmp_path, "language": "fr", "ocr_engine": "claude", **extra}


def scanned_pdf(path, lines_per_page):
    """PDF « scanné » : une image par page, aucun texte extractible."""
    pages = []
    for lines in lines_per_page:
        image = Image.new("RGB", (1200, 900), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=48)
        for i, line in enumerate(lines):
            draw.text((60, 60 + i * 80), line, fill="black", font=font)
        pages.append(image)
    pages[0].save(path, save_all=True, append_images=pages[1:])
    return path


def test_engine_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    assert ocr.engine({"ocr_engine": "auto"}) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert ocr.engine({"ocr_engine": "auto"}) == "claude"
    assert ocr.engine({"ocr_engine": "off"}) is None
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    assert ocr.engine({"ocr_engine": "auto"}) == "tesseract"


def test_page_images_from_a_scan(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", [["Page un"], ["Page deux"], ["Page trois"]])
    assert reader.load_book(pdf).text.strip("\f\n ") == ""
    images = ocr.page_images(pdf)
    sizes = [Image.open(io.BytesIO(b)).size for b in images]
    assert len(sizes) == 3 and all(max(size) <= ocr.RENDER_SIDE for size in sizes)


def test_claude_ocr_by_batches_with_cache(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", [[f"Page {i}"] for i in range(1, 6)])
    client = FakeClaude([json.dumps({"pages": ["Chapitre I", "Il était", "une fois."]}),
                         json.dumps({"pages": ["Chapitre II", "La suite."]})])
    config = cfg(tmp_path, ocr_batch_pages=3)
    stages = []
    text = ocr.recognize(pdf, config, client=client, log=lambda *_: None,
                         progress=lambda stage, title="": stages.append(title))
    assert text.split("\f") == ["Chapitre I", "Il était", "une fois.", "Chapitre II", "La suite."]
    assert stages == ["pages 1–3 / 5", "pages 4–5 / 5"]
    content = client.calls[0]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "image", "text"]
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert "pages 1 à 3" in content[-1]["text"] and "barre d'état" in content[-1]["text"]
    # Cache : une seconde lecture n'appelle plus Claude.
    assert ocr.recognize(pdf, config, client=FakeClaude([]), log=lambda *_: None) == text


def test_claude_wrong_page_count_is_an_error(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", [["A"], ["B"]])
    client = FakeClaude([json.dumps({"pages": ["seulement une"]})])
    with pytest.raises(ocr.OcrError, match="1 page"):
        ocr.recognize(pdf, cfg(tmp_path), client=client, log=lambda *_: None)


def test_ensure_text_runs_ocr_for_scanned_pdf(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", [["x"], ["y"]])
    book = reader.load_book(pdf)
    client = FakeClaude([json.dumps({"pages": ["Chapitre 1", "Il était une fois un homme."]})])
    book = main.ensure_text(book, cfg(tmp_path), client=client, log=lambda *_: None)
    assert "Il était une fois un homme." in book.text
    # Un livre normal n'est pas touché.
    txt = tmp_path / "livre.txt"
    txt.write_text("Bonjour.", encoding="utf-8")
    plain = reader.load_book(txt)
    assert main.ensure_text(plain, cfg(tmp_path), client=FakeClaude([])) is plain


def test_scanned_pdf_without_ocr_engine_gives_clear_message(tmp_path):
    book = reader.load_book(scanned_pdf(tmp_path / "scan.pdf", [["x"]]))
    with pytest.raises(SystemExit, match="scanné.*ANTHROPIC_API_KEY.*Tesseract"):
        main.check_has_text(book, {"ocr_engine": "off"})


def test_tidy_removes_misread_bullets():
    raw = "e Nom complet : Ateeq\ne_ Statut : disparu\n• Langues : rifain\n# CARTE\n\n\n\nTexte"
    assert ocr.tidy(raw) == ("Nom complet : Ateeq\nStatut : disparu\nLangues : rifain\nCARTE"
                             "\n\nTexte")


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract non installé")
def test_tesseract_reads_a_scan(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", [["Le cheikh monta", "au sommet."]])
    text = ocr.recognize(pdf, cfg(tmp_path, ocr_engine="tesseract"), log=lambda *_: None)
    assert "cheikh" in text and "sommet" in text


def test_page_images_without_pymupdf(tmp_path, monkeypatch):
    """Termux : pas de PyMuPDF, l'image de chaque page est extraite avec pypdf."""
    monkeypatch.setitem(__import__("sys").modules, "pymupdf", None)
    monkeypatch.setitem(__import__("sys").modules, "fitz", None)
    pdf = scanned_pdf(tmp_path / "scan.pdf", [["Un"], ["Deux"]])
    sizes = [Image.open(io.BytesIO(b)).size for b in ocr.page_images(pdf)]
    assert sizes == [(1200, 900), (1200, 900)]
