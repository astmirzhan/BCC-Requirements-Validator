"""Read a requirements document (PDF, DOCX, Markdown, plain text) into numbered lines."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"}


class DocumentError(Exception):
    """Raised when the input document cannot be read."""


@dataclass
class Document:
    path: Path
    title: str
    lines: list[str]
    sha256: str
    fmt: str
    pages: dict[int, int] = field(default_factory=dict)  # line number -> page (PDF only)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def numbered(self) -> str:
        """Text with stable line markers that the model and the report refer to."""
        width = len(str(len(self.lines)))
        return "\n".join(f"L{i:0{width}d}| {line}" for i, line in enumerate(self.lines, start=1))

    def location(self, line_no: int | None) -> str:
        if not line_no:
            return "—"
        page = self.pages.get(line_no)
        return f"стр. {page}, строка {line_no}" if page else f"строка {line_no}"


def _read_pdf(path: Path) -> tuple[list[str], dict[int, int]]:
    from pypdf import PdfReader

    lines: list[str] = []
    pages: dict[int, int] = {}
    for page_no, page in enumerate(PdfReader(str(path)).pages, start=1):
        for raw in (page.extract_text() or "").splitlines():
            lines.append(raw.rstrip())
            pages[len(lines)] = page_no
    return lines, pages


def _read_docx(path: Path) -> list[str]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    lines: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(block, document)
            text = paragraph.text.strip()
            style = (paragraph.style.name or "").lower() if paragraph.style else ""
            if text and style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                text = "#" * int(level) + " " + text
            lines.append(text)
        elif tag == "tbl":
            for row in Table(block, document).rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return lines


def load_document(path: str | Path) -> Document:
    path = Path(path)
    if not path.is_file():
        raise DocumentError(f"Файл не найден: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError(
            f"Формат {suffix or '(без расширения)'} не поддерживается. "
            f"Поддерживаются: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    raw_bytes = path.read_bytes()
    pages: dict[int, int] = {}
    try:
        if suffix == ".pdf":
            lines, pages = _read_pdf(path)
        elif suffix == ".docx":
            lines = _read_docx(path)
        else:
            lines = raw_bytes.decode("utf-8-sig").splitlines()
    except Exception as exc:  # parser libraries raise many unrelated types
        raise DocumentError(f"Не удалось прочитать {path.name}: {exc}") from exc

    lines = [line.rstrip() for line in lines]
    if not any(line.strip() for line in lines):
        raise DocumentError(f"В документе {path.name} нет текста (возможно, это скан без текстового слоя)")

    title = next(
        (line.lstrip("# ").strip() for line in lines if line.startswith("# ")),
        next((line.strip() for line in lines if line.strip()), path.stem),
    )
    return Document(
        path=path,
        title=title,
        lines=lines,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        fmt=suffix.lstrip("."),
        pages=pages,
    )
