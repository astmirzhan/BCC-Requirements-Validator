from pathlib import Path

import pytest

from validator.loader import load_document

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_MD = ROOT / "examples" / "requirements.md"
SAMPLE_PDF = ROOT / "examples" / "requirements.pdf"


@pytest.fixture
def make_doc(tmp_path):
    """Write text to a temporary Markdown file and load it as a Document."""

    def _make(text: str, name: str = "doc.md"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return load_document(path)

    return _make


@pytest.fixture(scope="session")
def sample():
    return load_document(SAMPLE_MD)
