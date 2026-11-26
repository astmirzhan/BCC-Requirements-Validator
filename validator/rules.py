"""Deterministic checks that need no model: precise, repeatable, never hallucinate."""

from __future__ import annotations

import re

from .findings import Finding, Reference
from .loader import Document

# Words that make a requirement ambiguous or unverifiable unless a measure follows (IEEE 830 4.3.2).
VAGUE_TERMS = [
    "быстро", "быстрый", "медленно", "своевременно", "оперативно", "удобно", "удобный", "интуитивно",
    "понятный", "простой", "легко", "эффективно", "эффективный", "оптимально", "оптимальный",
    "надёжно", "надежно", "гибко", "гибкий", "достаточно", "достаточный", "максимально", "минимально",
    "по возможности", "при необходимости", "как правило", "обычно", "в разумные сроки", "и т. д.",
    "и т.д.", "и т. п.", "и т.п.", "и др.", "примерно", "около", "приблизительно", "некоторые",
    "различные", "соответствующий", "соответствующие", "в течение", "незамедлительно", "сразу",
    "современный", "user-friendly", "etc",
]
_VAGUE_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(t) for t in sorted(VAGUE_TERMS, key=len, reverse=True)) + r")(?![\w])",
    re.I,
)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")
_PLACEHOLDER_RE = re.compile(
    r"(заполня(ется|ются)\s+\w+|\bTBD\b|\bTODO\b|\bуточнить\b|\bxxx\b|не заполнен[ао]?|"
    r"\[\s*(вставить|указать)[^\]]*\])",
    re.I,
)
_TEMPLATE_CHOICE_RE = re.compile(r"Да\s*/\s*Нет")
_EMPTY_CELL_VALUE_RE = re.compile(r"\|\s*(?:\*\*[^|*]+\*\*|[^|]+?)\s*\|\s*(—|-|–)?\s*\|\s*$")
_HTTP_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b")
_WRITE_WORDS_RE = re.compile(
    r"(сохран|создан|созда|изменен|изменит|обнов|удал|запис|save|create|update|delete|set[A-Z]|/set)", re.I
)
_STATUS_DEFINED_RE = re.compile(r"\*\*\s*([1-5]\d\d)\s*\.?\s*\*\*")
_STATUS_HEADING_RE = re.compile(r"^#{2,6}\s*([1-5]\d\d)\b")
_STATUS_MENTION_RE = re.compile(r"(?<![\d\s.,])\b([1-5][0-5]\d)\b(?![\d.,]\d)")
_VERSION_RE = re.compile(r"(верси[яи]|version|ред\.|редакция)\s*[:№]?\s*v?\d", re.I)


def run_rules(document: Document) -> list[Finding]:
    findings: list[Finding] = []
    findings += _vague_terms(document)
    findings += _empty_sections(document)
    findings += _placeholders(document)
    findings += _unit_mismatch(document)
    findings += _http_method_semantics(document)
    findings += _undescribed_statuses(document)
    findings += _body_vs_content_type(document)
    findings += _missing_version(document)
    return findings


def _is_template_table(line: str) -> bool:
    return line.lstrip().startswith("|") and bool(_TEMPLATE_CHOICE_RE.search(line))


def _vague_terms(document: Document) -> list[Finding]:
    found = []
    for no, line in enumerate(document.lines, start=1):
        if _is_template_table(line) or _HEADING_RE.match(line):
            continue
        for match in _VAGUE_RE.finditer(line):
            term = match.group(1)
            # "в течение N минут/секунд" still lacks percentile/conditions; keep it but as major.
            severity = "major" if term.lower() == "в течение" else "minor"
            found.append(
                Finding(
                    scope="rule",
                    rule="vague-term",
                    criterion="unambiguous",
                    severity=severity,
                    problem=f"Размытая формулировка «{term}»: без точной меры, условий и способа измерения "
                    "требование допускает разные толкования и его трудно проверить.",
                    recommendation="Заменить на измеримое условие: значение, единица, доля событий "
                    "(перцентиль), условия нагрузки и точка начала/окончания отсчёта.",
                    refs=[Reference(line.strip()[:200], no)],
                )
            )
    return found


def _empty_sections(document: Document) -> list[Finding]:
    found = []
    lines = document.lines
    for no, line in enumerate(lines, start=1):
        if not _HEADING_RE.match(line):
            continue
        level = len(line) - len(line.lstrip("#"))
        body: list[str] = []
        for nxt in lines[no:]:
            if _HEADING_RE.match(nxt):
                nxt_level = len(nxt) - len(nxt.lstrip("#"))
                if nxt_level <= level or not body:
                    break
            body.append(nxt)
        content = [b for b in body if b.strip() and b.strip() != "---"]
        if not content:
            next_is_child = no < len(lines) and any(
                _HEADING_RE.match(n) and len(n) - len(n.lstrip("#")) > level for n in lines[no : no + 3]
            )
            if next_is_child:
                continue
            found.append(_section_finding(line, no, "Раздел пуст: под заголовком нет содержания."))
    return found


def _section_finding(line: str, no: int, problem: str) -> Finding:
    return Finding(
        scope="rule",
        rule="empty-section",
        criterion="complete",
        severity="major",
        problem=problem,
        recommendation="Заполнить раздел или явно указать «не применимо» с обоснованием.",
        refs=[Reference(line.strip(), no)],
    )


def _placeholders(document: Document) -> list[Finding]:
    found = []
    template_rows = []
    empty_rows = []
    for no, line in enumerate(document.lines, start=1):
        stripped = line.strip()
        if _PLACEHOLDER_RE.search(stripped):
            found.append(
                Finding(
                    scope="rule",
                    rule="placeholder",
                    criterion="complete",
                    severity="major",
                    problem="Заглушка вместо содержания: раздел или поле не заполнены.",
                    recommendation="Заполнить содержание до передачи в разработку и тестирование.",
                    refs=[Reference(stripped[:200], no)],
                )
            )
        if _is_template_table(stripped):
            template_rows.append(no)
        if re.fullmatch(r"\|(\s*\|)+", stripped):
            empty_rows.append(no)
        else:
            cell = _EMPTY_CELL_VALUE_RE.search(stripped)
            if stripped.startswith("|") and cell and cell.group(1) and stripped.count("|") == 3:
                key = stripped.split("|")[1].strip(" *")
                found.append(
                    Finding(
                        scope="rule",
                        rule="empty-field",
                        criterion="complete",
                        severity="minor" if key.lower() in {"swagger"} else "major",
                        problem=f"Поле «{key}» не заполнено (стоит прочерк).",
                        recommendation=f"Указать значение поля «{key}» или причину отсутствия.",
                        refs=[Reference(stripped[:200], no)],
                    )
                )
            elif stripped.startswith("|"):
                for cell_match in re.finditer(r"\*\*([^*|]+)\*\*\s*\|\s*[^|]*?(?:Prod|Прод)\s*:\s*(—|-|–)\s*", stripped):
                    found.append(
                        Finding(
                            scope="rule",
                            rule="empty-field",
                            criterion="complete",
                            severity="major",
                            problem=f"В поле «{cell_match.group(1).strip()}» значение для Prod не указано (прочерк).",
                            recommendation="Указать значение для промышленной среды.",
                            refs=[Reference(stripped[:200], no)],
                        )
                    )
    if template_rows:
        found.append(
            Finding(
                scope="rule",
                rule="unfilled-checklist",
                criterion="complete",
                severity="major",
                problem=f"Чек-лист содержит {len(template_rows)} строк(и) с невыбранными вариантами «Да / Нет»: "
                "ревью требований не проведено, отметки не проставлены.",
                recommendation="Провести ревью и проставить отметки по каждому пункту чек-листа.",
                refs=[Reference(document.lines[template_rows[0] - 1].strip()[:200], template_rows[0])],
            )
        )
    if empty_rows:
        found.append(
            Finding(
                scope="rule",
                rule="empty-table-row",
                criterion="traceable",
                severity="major",
                problem="Таблица содержит пустые строки (нет значений ни в одной ячейке), "
                "например блок согласующих не заполнен.",
                recommendation="Заполнить таблицу: подразделения, ответственные, подписи/даты согласования.",
                refs=[Reference(document.lines[n - 1].strip()[:200], n) for n in empty_rows[:3]],
            )
        )
    return found


def _unit_mismatch(document: Document) -> list[Finding]:
    """Same quantity in minor and major currency units (tiyn vs tenge, kopeck vs rouble, cents vs dollars)."""
    pairs = [("тиын", r"тенге|₸|KZT"), ("копе", r"рубл|₽|RUB"), ("цент", r"доллар|\$|USD")]
    found = []
    for minor, major in pairs:
        for no, line in enumerate(document.lines, start=1):
            if re.search(minor, line, re.I) and re.search(major, line, re.I):
                found.append(
                    Finding(
                        scope="rule",
                        rule="unit-mismatch",
                        criterion="consistent",
                        severity="major",
                        problem="В одном требовании денежные величины заданы в разных единицах "
                        f"(«{minor}…» и основная валюта): при сравнении сумм с порогом нужен явный "
                        "пересчёт, иначе ошибка в 100 раз.",
                        recommendation="Хранить все суммы в одной единице (например, в минимальных единицах "
                        "валюты) и явно описать правило пересчёта и округления.",
                        refs=[Reference(line.strip()[:200], no)],
                    )
                )
    return found


def _http_method_semantics(document: Document) -> list[Finding]:
    found = []
    lines = document.lines
    for no, line in enumerate(lines, start=1):
        method = _HTTP_METHOD_RE.search(line)
        if not method or method.group(1) != "GET":
            continue
        if not re.search(r"(REST|method|метод|type)", line, re.I) and not line.strip().startswith("|"):
            continue
        context_range = range(max(0, no - 6), min(len(lines), no + 6))
        writes = [(i + 1, lines[i]) for i in context_range if _WRITE_WORDS_RE.search(lines[i])]
        if writes:
            w_no, w_line = writes[0]
            found.append(
                Finding(
                    scope="rule",
                    rule="http-method",
                    criterion="correct",
                    severity="critical",
                    problem="Операция изменения данных (сохранение) объявлена методом GET. GET по RFC 9110 — "
                    "безопасный метод только для чтения: такой запрос кэшируется, логируется в URL и "
                    "может повторяться прокси, что недопустимо для изменения настроек.",
                    recommendation="Использовать PUT (замена настройки, идемпотентно) или POST; передавать "
                    "параметры в теле запроса; GET оставить для чтения настройки.",
                    refs=[Reference(line.strip()[:200], no), Reference(w_line.strip()[:200], w_no)],
                )
            )
    return found


def _undescribed_statuses(document: Document) -> list[Finding]:
    lines = document.lines
    defined: dict[str, int] = {}
    for no, line in enumerate(lines, start=1):
        for match in _STATUS_DEFINED_RE.finditer(line):
            if re.search(r"\*\*\s*\d{3}\s*\.\s*\*\*", match.group(0)):
                defined.setdefault(match.group(1), no)
    if not defined:
        return []
    found = []
    reported = set()
    for no, line in enumerate(lines, start=1):
        if not re.search(r"(возвращ|статус|status|код|ответ|результат)", line, re.I) and not _STATUS_HEADING_RE.match(line) \
                and not re.fullmatch(r"\s*\*\*\s*[1-5]\d\d\s*\*\*\s*", line):
            continue
        for match in _STATUS_MENTION_RE.finditer(line):
            code = match.group(1)
            if code in defined or code in reported or not ("100" <= code <= "599"):
                continue
            reported.add(code)
            found.append(
                Finding(
                    scope="rule",
                    rule="undescribed-status",
                    criterion="complete",
                    severity="major",
                    problem=f"Код ответа {code} используется, но отсутствует в перечне описанных статусов "
                    f"({', '.join(sorted(defined))}).",
                    recommendation=f"Добавить {code} в перечень статусов с условием возникновения и примером "
                    "ответа или убрать его использование.",
                    refs=[Reference(line.strip()[:200], no)],
                )
            )
    return found


def _body_vs_content_type(document: Document) -> list[Finding]:
    lines = document.lines
    found = []
    for no, line in enumerate(lines, start=1):
        if "content-type" not in line.lower():
            continue
        for offset in range(1, 4):
            if no - 1 + offset >= len(lines):
                break
            other = lines[no - 1 + offset]
            if re.search(r"(тело не используется|без тела|тело отсутствует|body is not used)", other, re.I):
                found.append(
                    Finding(
                        scope="rule",
                        rule="content-type-without-body",
                        criterion="consistent",
                        severity="minor",
                        problem="Заявлен Content-Type тела запроса, но тут же указано, что тело не "
                        "используется: противоречие в описании запроса.",
                        recommendation="Либо убрать Content-Type для запроса без тела, либо перенести "
                        "параметры в JSON-тело и описать его схему.",
                        refs=[Reference(line.strip()[:200], no), Reference(other.strip()[:200], no + offset)],
                    )
                )
                break
    return found


def _missing_version(document: Document) -> list[Finding]:
    if any(_VERSION_RE.search(line) for line in document.lines):
        return []
    anchor_no = next(
        (no for no, line in enumerate(document.lines, start=1) if re.search(r"верси", line, re.I)), 1
    )
    return [
        Finding(
            scope="rule",
            rule="no-version",
            criterion="modifiable",
            severity="major",
            problem="В документе не указаны номер версии и дата: невозможно сослаться на конкретную "
            "редакцию требований и отследить изменения.",
            recommendation="Добавить номер версии, дату и историю изменений (кто, когда, что изменил).",
            refs=[Reference(document.lines[anchor_no - 1].strip()[:200], anchor_no)],
        )
    ]
