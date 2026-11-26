"""Render the review as a Russian Markdown report following section 6 of the task."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .criteria import REQUIREMENT_CRITERIA, SET_CRITERIA, SEVERITIES, SEVERITY_ORDER, STATUSES
from .findings import Finding
from .pipeline import Review

SEVERITY_ICONS = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
STATUS_ICONS = {"pass": "✅", "partial": "⚠️", "fail": "❌", "na": "—"}
SCOPE_NAMES = {"rule": "правило", "requirement": "модель", "set": "модель, набор"}
CATEGORY_NAMES = {
    "business": "бизнес-цель", "functional": "функциональное", "nonfunctional": "нефункциональное",
    "data": "данные", "integration": "интеграция", "ui": "UI", "api": "API", "acceptance": "приёмка",
    "other": "прочее",
}
CRITICAL_CRITERIA = {"verifiable", "consistent", "correct", "complete", "unambiguous"}


def render_markdown(review: Review, meta: dict) -> str:
    doc = review.document
    parts: list[str] = []
    add = parts.append

    add(f"# Отчёт о проверке требований: «{_md(doc.title)}»\n")
    add("Статическое ревью требований до разработки по ISO/IEC/IEEE 29148:2018 (п. 5.2.5, 5.2.6) "
        "и IEEE Std 830-1998 (п. 4.3). Отчёт сформирован агентом автоматически.\n")

    # 1. Passport
    add("## 1. Паспорт прогона\n")
    add("| Поле | Значение |\n| :--- | :--- |")
    rows = [
        ("Документ", f"{_md(doc.title)} (`{doc.path.name}`, {doc.fmt.upper()}, {len(doc.lines)} строк)"),
        ("SHA-256 документа", f"`{doc.sha256[:16]}…`"),
        ("Дата и время прогона", meta["started"].strftime("%Y-%m-%d %H:%M %Z").strip()),
        ("Модель", _models_cell(meta)),
        ("Версия агента", f"{meta['agent_version']} (коммит `{meta['commit']}`)"),
        ("Обращений к модели", f"{meta['llm_calls']} (из кэша: {meta['cache_hits']})"),
        ("Длительность", f"{meta['seconds']:.0f} с"),
        ("Критерии", "ISO/IEC/IEEE 29148:2018 п. 5.2.5 (требование), 5.2.6 (набор); IEEE 830-1998 п. 4.3"),
    ]
    parts.extend(f"| {k} | {v} |" for k, v in rows)
    add("")

    # 2. Slicing
    add("## 2. Как агент нарезал требования\n")
    add("Ссылки на место в документе даны номерами строк исходного файла. "
        "Если в документе не было номера, агент присвоил свой и сохранил цитату.\n")
    add("| ID в отчёте | ID в документе | Раздел | Тип | Краткая формулировка | Цитата из документа |")
    add("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in review.requirements:
        add(f"| {r.id} | {_md(r.doc_id) if r.doc_id else '— (нет)'} | {_md(r.section)} | "
            f"{CATEGORY_NAMES.get(r.category, r.category)} | {_md(r.summary)} | "
            f"«{_md(_short(r.quote, 140))}» ({doc.location(r.line)}{'' if r.match == 'exact' else ', ≈'}) |")
    add("")

    # 3. Each requirement
    add("## 3. Проверка каждого требования\n")
    add("Обозначения: ✅ соответствует, ⚠️ частично, ❌ нарушено, — неприменимо.\n")
    keys = list(REQUIREMENT_CRITERIA)
    add("| ID | " + " | ".join(REQUIREMENT_CRITERIA[k]["name"].split(" (")[0] for k in keys) + " | Дефекты |")
    add("| :--- | " + " | ".join(":---:" for _ in keys) + " | :---: |")
    for r in review.requirements:
        icons = " | ".join(STATUS_ICONS.get(r.criteria.get(k, "na"), "—") for k in keys)
        count = len(review.for_requirement(r.id)) + len(
            [f for f in review.by_scope("rule") if r.id in f.related])
        add(f"| {r.id} | {icons} | {count or '—'} |")
    add("")
    for r in review.requirements:
        own = review.for_requirement(r.id)
        rules = [f for f in review.by_scope("rule") if r.id in f.related]
        title = f"{r.id}" + (f" ({_md(r.doc_id)})" if r.doc_id else "")
        add(f"### {title}. {_md(r.summary)}\n")
        add(f"> «{_md(_short(r.quote, 400))}» — {doc.location(r.line)}\n")
        if not own and not rules:
            add("Нарушений не найдено.\n")
            continue
        for f in sorted(own, key=_sort_key):
            add(_finding_md(f, review))
        if rules:
            add("Также см. автоматические проверки: " + ", ".join(f.id for f in rules) + ".\n")

    # 4. Whole set
    add("## 4. Проверка набора требований целиком\n")
    add("| Критерий | Источник | Оценка | Комментарий |\n| :--- | :--- | :---: | :--- |")
    for key, c in SET_CRITERIA.items():
        state = review.set_criteria.get(key)
        status = state.status if state else "na"
        add(f"| {c['name']} | {c['source']} | {STATUS_ICONS.get(status, '—')} | {_md(state.summary if state else '')} |")
    add("")
    set_findings = sorted(review.by_scope("set"), key=_sort_key)
    if set_findings:
        add("### Дефекты набора\n")
        parts.extend(_finding_md(f, review) for f in set_findings)
    else:
        add("Дефектов на уровне набора не найдено.\n")

    # 5. Rules
    add("## 5. Автоматические проверки без модели\n")
    add("Детерминированные правила: размытые формулировки, пустые разделы и поля, заглушки, "
        "единицы измерения, семантика HTTP-методов, неописанные коды ответа, версия документа.\n")
    rule_findings = sorted(review.by_scope("rule"), key=_sort_key)
    parts.extend(_finding_md(f, review) for f in rule_findings) if rule_findings else add("Срабатываний нет.\n")

    # 6. Summary
    counts = Counter(f.severity for f in review.findings)
    critical = sorted([f for f in review.findings if f.severity == "critical"], key=lambda f: f.first_line)
    violated_reqs = {rid for f in review.findings for rid in f.related}
    add("## 6. Сводка\n")
    add("| Показатель | Значение |\n| :--- | :--- |")
    add(f"| Требований выделено | {len(review.requirements)} |")
    add(f"| Требований с нарушениями | {len(violated_reqs)} из {len(review.requirements)} |")
    add(f"| Нарушений всего | {len(review.findings)} |")
    for sev in SEVERITIES:
        add(f"| {SEVERITY_ICONS[sev]} {SEVERITIES[sev]} | {counts.get(sev, 0)} |")
    add(f"| Найдено правилами / моделью | {len(review.by_scope('rule'))} / "
        f"{len(review.by_scope('requirement')) + len(review.by_scope('set'))} |")
    add(f"| Отброшено (цитата не найдена в документе) | {len(review.dropped)} |")
    add("")
    add("### Критичные дефекты\n")
    if critical:
        for f in critical:
            add(f"- **{f.id}** ({_criterion_name(f)}, {_where(f, review)}): {_md(_short(f.problem, 260))}")
    else:
        add("- Критичных дефектов не найдено.")
    add("")
    by_criterion = Counter(_criterion_name(f) for f in review.findings if f.criterion in CRITICAL_CRITERIA)
    if by_criterion:
        add("Распределение по ключевым свойствам: " + ", ".join(f"{k} — {v}" for k, v in by_criterion.most_common()) + ".\n")

    # 7. Verdict
    add("## 7. Итог\n")
    model_dev = bool(review.verdict.get("ready_for_development"))
    model_test = bool(review.verdict.get("ready_for_testing"))
    ready_dev = model_dev and not critical
    ready_test = model_test and not critical
    add(f"- **Начинать разработку:** {'да' if ready_dev else 'нет'}")
    add(f"- **Начинать тестирование:** {'да' if ready_test else 'нет'}")
    if critical and (model_dev or model_test):
        add(f"- Решение «нет» принято агентом по правилу: найдено критичных дефектов — {len(critical)}.")
    add("")
    if review.verdict.get("summary"):
        add(_md(str(review.verdict["summary"])) + "\n")
    priorities = [str(p) for p in review.verdict.get("priorities") or [] if p]
    add("### Что исправить в первую очередь\n")
    if priorities:
        parts.extend(f"{n}. {_md(p)}" for n, p in enumerate(priorities, start=1))
    else:
        parts.extend(f"{n}. {f.id}: {_md(_short(f.recommendation, 200))}" for n, f in enumerate(critical[:5], start=1))
    add("")

    # Appendix
    if review.dropped:
        add("## Приложение. Отброшенные замечания модели\n")
        add("Замечания, цитату которых агент не нашёл в документе. Они не учитываются в сводке, "
            "так как не привязаны к конкретному месту.\n")
        for f in review.dropped:
            add(f"- {_criterion_name(f)}: {_md(_short(f.problem, 200))}")
        add("")
    add(f"---\n*Сформировано агентом BCC Requirements Validator {meta['agent_version']} "
        f"({meta['commit']}), {meta['started'].strftime('%Y-%m-%d %H:%M')}.*\n")
    return "\n".join(parts)


STEP_NAMES = {"slice": "нарезка", "requirements": "проверка требований", "set": "проверка набора"}


def _models_cell(meta: dict) -> str:
    steps = meta.get("step_models") or {}
    provider = meta["model"].split(":")[0]
    if len(set(steps.values())) <= 1:
        return f"`{meta['model']}`"
    return "; ".join(f"{STEP_NAMES.get(k, k)}: `{provider}:{v}`" for k, v in steps.items())


def _finding_md(f: Finding, review: Review) -> str:
    lines = [
        f"**{f.id}** · {SEVERITY_ICONS.get(f.severity, '')} {SEVERITIES.get(f.severity, f.severity)} · "
        f"{_criterion_name(f)} ({_criterion_source(f)}) · {_where(f, review)} · источник: {SCOPE_NAMES[f.scope]}"
        + (f" · требования: {', '.join(f.related)}" if f.related and f.scope != "requirement" else "")
        + "\n"
    ]
    for ref in f.refs:
        if ref.match == "missing":
            continue
        approx = " (цитата приблизительная)" if ref.match == "approx" else ""
        lines.append(f"> «{_md(_short(ref.quote, 300))}» — {review.document.location(ref.line)}{approx}\n")
    lines.append(f"- **Проблема:** {_md(f.problem)}")
    if f.recommendation:
        lines.append(f"- **Рекомендация:** {_md(f.recommendation)}")
    lines.append("")
    return "\n".join(lines)


def _where(f: Finding, review: Review) -> str:
    lines = sorted({ref.line for ref in f.refs if ref.line and ref.match != "missing"})
    if not lines:
        return "место не определено"
    return ("строки " if len(lines) > 1 else "строка ") + ", ".join(map(str, lines))


def _criterion_name(f: Finding) -> str:
    c = REQUIREMENT_CRITERIA.get(f.criterion) if f.scope != "set" else SET_CRITERIA.get(f.criterion)
    c = c or SET_CRITERIA.get(f.criterion) or REQUIREMENT_CRITERIA.get(f.criterion)
    return c["name"].split(" (")[0] if c else (f.criterion or "прочее")


def _criterion_source(f: Finding) -> str:
    c = REQUIREMENT_CRITERIA.get(f.criterion) if f.scope != "set" else SET_CRITERIA.get(f.criterion)
    c = c or SET_CRITERIA.get(f.criterion) or REQUIREMENT_CRITERIA.get(f.criterion)
    return c["source"] if c else "—"


def _sort_key(f: Finding):
    return (SEVERITY_ORDER.get(f.severity, 9), f.first_line)


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _md(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def started_now() -> datetime:
    return datetime.now().astimezone()
