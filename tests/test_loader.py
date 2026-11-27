import pytest

from validator.loader import DocumentError, load_document

from .conftest import SAMPLE_PDF


def test_markdown_lines_are_numbered(make_doc):
    doc = make_doc("# Title\n\nFirst line\nSecond line\n")
    assert doc.title == "Title"
    assert doc.lines[2] == "First line"
    assert doc.numbered().splitlines()[3] == "L4| Second line"
    assert doc.location(3) == "строка 3"


def test_pdf_keeps_pages_and_fixes_lookalike_glyphs():
    doc = load_document(SAMPLE_PDF)
    assert doc.fmt == "pdf"
    assert len(doc.lines) > 100
    assert set(doc.pages.values()) >= {1, 2}
    assert doc.location(1).startswith("стр. 1, строка 1")
    assert "ĸ" not in doc.text
    assert "максимум один такой push" in doc.text


def test_missing_file_and_unknown_format(tmp_path):
    with pytest.raises(DocumentError, match="не найден"):
        load_document(tmp_path / "nope.pdf")
    odd = tmp_path / "doc.xyz"
    odd.write_text("text")
    with pytest.raises(DocumentError, match="не поддерживается"):
        load_document(odd)


def test_empty_document_is_rejected(make_doc):
    with pytest.raises(DocumentError, match="нет текста"):
        make_doc("\n\n   \n")
