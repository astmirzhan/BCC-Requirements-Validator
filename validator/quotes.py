"""Check that quotes returned by the model really exist in the document and find their line."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .loader import Document

_MARKUP = re.compile(r"[*`_]|\\")
_SPACES = re.compile(r"\s+")
_LINE_PREFIX = re.compile(r"^\s*L\d+\|\s?")


def normalize(text: str) -> str:
    text = _LINE_PREFIX.sub("", text or "")
    text = _MARKUP.sub("", text).replace("ё", "е").replace("Ё", "Е")
    text = text.replace("«", '"').replace("»", '"').replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return _SPACES.sub(" ", text).strip().lower()


@dataclass
class QuoteMatch:
    status: str  # exact | approx | missing
    line: int | None


class QuoteIndex:
    def __init__(self, document: Document):
        self.document = document
        self.normalized = [normalize(line) for line in document.lines]

    def locate(self, quote: str, hint_line: int | None = None) -> QuoteMatch:
        needle = normalize(quote)
        if len(needle) < 3:
            return QuoteMatch("missing", None)

        hits = [i for i, line in enumerate(self.normalized, start=1) if needle in line]
        if hits:
            return QuoteMatch("exact", _closest(hits, hint_line))

        # Quote spanning several lines: search in a window of joined lines.
        for start in range(len(self.normalized)):
            window = " ".join(self.normalized[start : start + 4])
            if needle in window:
                return QuoteMatch("exact", start + 1)

        # Model paraphrased slightly: accept a close match on one line.
        best_ratio, best_line = 0.0, None
        for i, line in enumerate(self.normalized, start=1):
            if not line:
                continue
            matcher = difflib.SequenceMatcher(None, needle, line, autojunk=False)
            block = matcher.find_longest_match(0, len(needle), 0, len(line))
            ratio = block.size / len(needle)
            if ratio > best_ratio or (ratio == best_ratio and hint_line and best_line and abs(i - hint_line) < abs(best_line - hint_line)):
                best_ratio, best_line = ratio, i
        if best_ratio >= 0.6:
            return QuoteMatch("approx", best_line)
        return QuoteMatch("missing", None)


def _closest(hits: list[int], hint: int | None) -> int:
    if not hint:
        return hits[0]
    return min(hits, key=lambda line: abs(line - hint))
