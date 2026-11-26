"""Run the review: rules, slicing, per-requirement check, whole-set check, quote verification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import prompts
from .criteria import REQUIREMENT_CRITERIA, SET_CRITERIA, SEVERITIES, SEVERITY_ORDER
from .findings import Finding, Reference
from .llm import LLMClient, LLMError
from .loader import Document
from .quotes import QuoteIndex
from .rules import run_gap_rules, run_rules

VALID_STATUSES = {"pass", "partial", "fail", "na"}


@dataclass
class Requirement:
    id: str
    doc_id: str | None
    section: str
    category: str
    summary: str
    quote: str
    line: int | None
    match: str
    parts: list[str] = field(default_factory=list)
    criteria: dict[str, str] = field(default_factory=dict)


@dataclass
class SetCriterion:
    status: str
    summary: str


@dataclass
class Review:
    document: Document
    requirements: list[Requirement]
    findings: list[Finding]
    set_criteria: dict[str, SetCriterion]
    verdict: dict
    dropped: list[Finding]

    def by_scope(self, scope: str) -> list[Finding]:
        return [f for f in self.findings if f.scope == scope]

    def for_requirement(self, req_id: str) -> list[Finding]:
        return [f for f in self.findings if f.scope == "requirement" and req_id in f.related]


def run_review(document: Document, llm: LLMClient, log=print) -> Review:
    index = QuoteIndex(document)
    numbered = document.numbered()

    log("1/5 Проверки без модели (правила)")
    rule_findings = run_rules(document)

    log("2/5 Нарезка документа на требования")
    sliced = llm.complete_json(prompts.SYSTEM, prompts.slice_prompt(numbered), step="slice")
    requirements = _parse_requirements(sliced, index)
    if not requirements:
        raise LLMError("Модель не выделила ни одного требования")
    log(f"    выделено требований: {len(requirements)}")
    req_dicts = [vars(r) for r in requirements]
    known = [f"строка {f.refs[0].line}: {f.problem.split('.')[0]}" for f in rule_findings if f.refs]

    log("3/5 Проверка каждого требования")
    checked = llm.complete_json(
        prompts.SYSTEM, prompts.requirements_prompt(numbered, req_dicts, known), step="requirements"
    )
    req_findings = _parse_requirement_results(checked, requirements, index)

    log("4/5 Поиск противоречий между частями документа")
    conflicts = llm.complete_json(
        prompts.SYSTEM, prompts.conflicts_prompt(numbered, req_dicts, known), step="conflicts"
    )
    conflict_findings = _parse_conflicts(conflicts, requirements, index)

    log("5/5 Проверка набора требований целиком")
    set_result = llm.complete_json(prompts.SYSTEM, prompts.set_prompt(numbered, req_dicts, known), step="set")
    set_findings, set_criteria, verdict = _parse_set_result(set_result, requirements, index)
    set_findings = conflict_findings + set_findings

    rule_findings += run_gap_rules(document)
    _link_rules_to_requirements(rule_findings, requirements)
    all_findings = rule_findings + req_findings + set_findings
    kept = [f for f in all_findings if f.verified]
    dropped = [f for f in all_findings if not f.verified]
    kept = _dedupe(kept)
    kept.sort(key=lambda f: ({"rule": 0, "requirement": 1, "set": 2}[f.scope], f.first_line))
    for n, finding in enumerate(sorted(kept, key=lambda f: (SEVERITY_ORDER[f.severity], f.first_line)), start=1):
        finding.id = f"D-{n:02d}"
    return Review(document, requirements, kept, set_criteria, verdict, dropped)


# ---- parsing model output --------------------------------------------------


def _parse_requirements(data: dict, index: QuoteIndex) -> list[Requirement]:
    result = []
    for n, item in enumerate(data.get("requirements") or [], start=1):
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        match = index.locate(quote, _int(item.get("line")))
        result.append(
            Requirement(
                id=f"R-{n:02d}",
                doc_id=(str(item["doc_id"]).strip() or None) if item.get("doc_id") else None,
                section=str(item.get("section") or "—"),
                category=str(item.get("category") or "other"),
                summary=str(item.get("summary") or quote[:120]),
                quote=quote,
                line=match.line or _int(item.get("line")),
                match=match.status,
                parts=[str(p) for p in item.get("parts") or [] if p],
            )
        )
    return result


def _parse_requirement_results(data: dict, requirements: list[Requirement], index: QuoteIndex) -> list[Finding]:
    by_id = {r.id: r for r in requirements}
    findings = []
    for item in data.get("results") or []:
        req = by_id.get(str(item.get("id")))
        if not req:
            continue
        criteria = item.get("criteria") or {}
        req.criteria = {
            key: (str(criteria.get(key)).lower() if str(criteria.get(key)).lower() in VALID_STATUSES else "na")
            for key in REQUIREMENT_CRITERIA
        }
        for raw in item.get("findings") or []:
            finding = _finding_from(raw, "requirement", index, default_line=req.line)
            if finding:
                finding.related = [req.id]
                findings.append(finding)
                if finding.criterion in req.criteria and req.criteria[finding.criterion] == "pass":
                    req.criteria[finding.criterion] = "partial"
    return findings


def _parse_set_result(data: dict, requirements: list[Requirement], index: QuoteIndex):
    known = {r.id for r in requirements}
    findings = []
    for raw in data.get("findings") or []:
        finding = _finding_from(raw, "set", index)
        if finding:
            finding.related = [r for r in raw.get("related") or [] if r in known]
            findings.append(finding)
    criteria = {}
    raw_criteria = data.get("criteria") or {}
    for key in SET_CRITERIA:
        value = raw_criteria.get(key) or {}
        status = str(value.get("status", "na")).lower() if isinstance(value, dict) else str(value).lower()
        criteria[key] = SetCriterion(
            status=status if status in VALID_STATUSES else "na",
            summary=str(value.get("summary", "")) if isinstance(value, dict) else "",
        )
    verdict = data.get("verdict") or {}
    return findings, criteria, verdict


def _parse_conflicts(data: dict, requirements: list[Requirement], index: QuoteIndex) -> list[Finding]:
    known = {r.id for r in requirements}
    findings = []
    for raw in data.get("conflicts") or []:
        if not isinstance(raw, dict):
            continue
        raw = dict(raw, criterion="consistent")
        if raw.get("scenario"):
            raw["problem"] = f"{raw.get('problem', '').strip()} Сценарий: {str(raw['scenario']).strip()}"
        finding = _finding_from(raw, "set", index)
        if finding:
            finding.related = [r for r in raw.get("related") or [] if r in known]
            findings.append(finding)
    return findings


def _finding_from(raw: dict, scope: str, index: QuoteIndex, default_line: int | None = None) -> Finding | None:
    if not isinstance(raw, dict) or not raw.get("problem"):
        return None
    quotes = raw.get("quotes")
    if not quotes:
        quotes = [{"quote": raw.get("quote", ""), "line": raw.get("line", default_line)}]
    refs = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        text = str(q.get("quote") or "").strip()
        match = index.locate(text, _int(q.get("line")))
        refs.append(Reference(text, match.line, match.status))
    severity = str(raw.get("severity", "minor")).lower()
    criterion = str(raw.get("criterion", "")).lower()
    return Finding(
        scope=scope,
        criterion=criterion,
        severity=severity if severity in SEVERITIES else "minor",
        problem=str(raw["problem"]).strip(),
        recommendation=str(raw.get("recommendation", "")).strip(),
        refs=refs,
    )


def _link_rules_to_requirements(findings: list[Finding], requirements: list[Requirement]) -> None:
    by_line: dict[int, list[str]] = {}
    for req in requirements:
        if req.line:
            by_line.setdefault(req.line, []).append(req.id)
    for finding in findings:
        for ref in finding.refs:
            finding.related.extend(r for r in by_line.get(ref.line or -1, []) if r not in finding.related)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Merge findings that describe the same defect at the same place.

    Rule findings win (they are exact); among model findings the more severe one is kept.
    Two findings are duplicates when they point to a common line and their texts are similar,
    or when they point to the same two or more lines.
    """
    scope_rank = {"rule": 0, "set": 1, "requirement": 2}
    ordered = sorted(findings, key=lambda f: (scope_rank[f.scope], SEVERITY_ORDER[f.severity]))
    kept: list[Finding] = []
    for finding in ordered:
        duplicate_of = next((k for k in kept if _same_defect(k, finding)), None)
        if duplicate_of is None:
            kept.append(finding)
            continue
        duplicate_of.related.extend(r for r in finding.related if r not in duplicate_of.related)
        known = _ref_lines(duplicate_of)
        duplicate_of.refs.extend(r for r in finding.refs if r.line and r.line not in known and r.match != "missing")
        if SEVERITY_ORDER[finding.severity] < SEVERITY_ORDER[duplicate_of.severity]:
            duplicate_of.severity = finding.severity
    return kept


def _same_defect(a: Finding, b: Finding) -> bool:
    lines_a, lines_b = _ref_lines(a), _ref_lines(b)
    common = lines_a & lines_b
    if len(common) >= 2:
        return True
    if common and a.scope == "rule" and b.scope == "rule":
        return a.rule == b.rule
    if a.scope == "rule" or b.scope == "rule":
        return bool(common) and _similarity(a.problem, b.problem) >= 0.32
    # Model findings often cite the same place from different ends ("line 175 contradicts line 189"),
    # so lines named in the problem text count too, but then the texts must agree more closely.
    if common:
        return _similarity(a.problem, b.problem) >= 0.32
    if (lines_a | _mentioned_lines(a.problem)) & (lines_b | _mentioned_lines(b.problem)):
        return _similarity(a.problem, b.problem) >= 0.4 and bool(_signature(a.problem) & _signature(b.problem))
    return False


def _ref_lines(finding: Finding) -> set[int]:
    return {r.line for r in finding.refs if r.line and r.match != "missing"}


def _mentioned_lines(text: str) -> set[int]:
    """Line numbers named in the text: «строка 189», «строки 169–171», «L64». Long ranges are ignored."""
    found: set[int] = set()
    for m in re.finditer(r"(?:строк\w*|L)\s*(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?((?:\s*(?:,|и)\s*\d{1,4})*)", text):
        start, end = int(m.group(1)), int(m.group(2) or m.group(1))
        if 0 <= end - start <= 5:
            found.update(range(start, end + 1))
        found.update(int(n) for n in re.findall(r"\d{1,4}", m.group(3) or ""))
    return found


def _signature(text: str) -> frozenset[str]:
    """HTTP statuses and requirement ids a finding is about; two findings on 403/404 share it."""
    text = re.sub(r"(?:строк\w*|L)\s*[\d\s,–—-]+", " ", text)
    return frozenset(re.findall(r"\b(?:20[01]|40[0-9]|42[29]|50[0-4])\b|Ф-\d+|НФТ-\d+", text))


def _similarity(x: str, y: str) -> float:
    words_x, words_y = _stems(x), _stems(y)
    if not words_x or not words_y:
        return 0.0
    return len(words_x & words_y) / min(len(words_x), len(words_y))


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[\w-]{4,}", text.lower())}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
