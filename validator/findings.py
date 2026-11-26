"""Shared data model for defects found by rules and by the model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Reference:
    quote: str
    line: int | None
    match: str = "exact"  # exact | approx | missing


@dataclass
class Finding:
    scope: str  # rule | requirement | set
    criterion: str
    severity: str  # critical | major | minor
    problem: str
    recommendation: str
    refs: list[Reference] = field(default_factory=list)
    related: list[str] = field(default_factory=list)  # requirement ids R-xx
    rule: str | None = None
    id: str = ""

    @property
    def verified(self) -> bool:
        return any(ref.match != "missing" for ref in self.refs)

    @property
    def first_line(self) -> int:
        lines = [ref.line for ref in self.refs if ref.line]
        return min(lines) if lines else 10**9
