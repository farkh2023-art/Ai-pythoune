import sys

import pytest

from modules import reader


def test_txt(tmp_path):
    path = tmp_path / "mon_livre.txt"
    path.write_text("Bonjour.", encoding="cp1252")
    book = reader.load_book(path)
    assert book.text == "Bonjour." and book.title == "Mon livre"


def test_docx(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.core_properties.title = "Titre DOCX"
    document.add_heading("Chapitre 1", level=1)
    document.add_paragraph("Premier paragraphe.")
    document.save(tmp_path / "b.docx")
    book = reader.load_book(tmp_path / "b.docx")
    assert book.title == "Titre DOCX"
    assert "Chapitre 1" in book.text and "Premier paragraphe." in book.text


def test_epub(tmp_path):
    epub = pytest.importorskip("ebooklib.epub")
    book = epub.EpubBook()
    book.set_identifier("id1")
    book.set_title("Titre EPUB")
    book.add_author("Auteur")
    chapter = epub.EpubHtml(title="C1", file_name="c1.xhtml", lang="fr")
    chapter.content = "<h1>Chapitre 1</h1><p>Il &eacute;tait une fois.</p>"
    book.add_item(chapter)
    book.spine = [chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(tmp_path / "b.epub"), book)
    loaded = reader.load_book(tmp_path / "b.epub")
    assert loaded.title == "Titre EPUB" and loaded.author == "Auteur"
    assert "Chapitre 1\n\nIl était une fois." in loaded.text


def test_pdf_pages_are_separated(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in ("Page un.", "Page deux."):
        doc.new_page().insert_text((72, 72), text)
    doc.save(tmp_path / "b.pdf")
    assert reader.load(tmp_path / "b.pdf").count("\f") == 1


def test_pdf_falls_back_to_pypdf(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("pypdf")
    doc = pymupdf.open()
    doc.set_metadata({"title": "Titre PDF", "author": "Auteur"})
    for text in ("Page un.", "Page deux."):
        doc.new_page().insert_text((72, 72), text)
    doc.save(tmp_path / "b.pdf")

    # Simule un environnement sans PyMuPDF (Termux).
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)
    book = reader.load_book(tmp_path / "b.pdf")
    assert (book.title, book.author) == ("Titre PDF", "Auteur")
    assert book.text.split("\f") == ["Page un.", "Page deux."]


def test_unsupported_format(tmp_path):
    (tmp_path / "b.xyz").write_text("x")
    with pytest.raises(ValueError):
        reader.load(tmp_path / "b.xyz")
