"""The whole pipeline on a small document with a fake model: no network, no key."""

from validator.findings import Finding, Reference
from validator.pipeline import _dedupe, run_review
from validator.report import render_markdown, started_now

DOC = """# Уведомление о расходах

Версия 1.0

## Функциональные требования

- **Ф-1.** На карту приходится максимум один push за календарный месяц.
- **Ф-2.** После смены порога push приходит независимо от истории.

Откат: функция выключается флагом.
"""


class FakeLLM:
    def __init__(self, answers):
        self.answers = answers
        self.steps = []

    def complete_json(self, system, prompt, step):
        self.steps.append(step)
        return self.answers[step]


def answers(critical: bool, invented_quote: bool = False):
    quote = "Система шлёт SMS через спутник" if invented_quote else "максимум один push за календарный месяц"
    return {
        "slice": {"requirements": [
            {"doc_id": "Ф-1", "section": "ФТ", "category": "functional", "summary": "Лимит push",
             "quote": "На карту приходится максимум один push за календарный месяц.", "line": 7},
            {"doc_id": "Ф-2", "section": "ФТ", "category": "functional", "summary": "Push после смены порога",
             "quote": "После смены порога push приходит независимо от истории.", "line": 8},
        ]},
        "requirements": {"results": [{"id": "R-01", "criteria": {"unambiguous": "pass"}, "findings": []}]},
        "conflicts": {"conflicts": [{
            "criterion": "consistent", "severity": "critical" if critical else "minor", "related": ["R-01", "R-02"],
            "quotes": [{"quote": quote, "line": 7}, {"quote": "независимо от истории", "line": 8}],
            "problem": "Ф-1 ограничивает push одним в месяц, а Ф-2 требует push после смены порога.",
            "scenario": "Порог 50 000, push, порог 70 000, второй push в том же месяце.",
            "recommendation": "Уточнить, сбрасывает ли смена порога месячный лимит.",
        }]},
        "set": {"findings": [], "criteria": {}, "verdict": {
            "ready_for_development": True, "ready_for_testing": True, "summary": "Готово.", "fix_first": []}},
    }


META = {"started": started_now(), "model": "fake:model", "step_models": {}, "agent_version": "test",
        "commit": "test", "llm_calls": 4, "cache_hits": 0, "seconds": 1}


def test_four_model_steps_and_all_report_sections(make_doc):
    llm = FakeLLM(answers(critical=True))
    review = run_review(make_doc(DOC), llm, log=lambda *_: None)
    assert llm.steps == ["slice", "requirements", "conflicts", "set"]
    assert [r.doc_id for r in review.requirements] == ["Ф-1", "Ф-2"]
    report = render_markdown(review, META)
    for section in ["## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7."]:
        assert section in report


def test_critical_defect_forces_verdict_no(make_doc):
    review = run_review(make_doc(DOC), FakeLLM(answers(critical=True)), log=lambda *_: None)
    assert any(f.severity == "critical" for f in review.findings)
    report = render_markdown(review, META)
    assert "**Начинать разработку:** нет" in report  # the model said "ready", the agent overrides it


def test_without_critical_defects_model_verdict_stands(make_doc):
    review = run_review(make_doc(DOC), FakeLLM(answers(critical=False)), log=lambda *_: None)
    assert "**Начинать разработку:** да" in render_markdown(review, META)


def test_finding_with_invented_quote_still_needs_one_real_place(make_doc):
    # one of two quotes is invented: the finding stays, the invented quote is marked missing
    review = run_review(make_doc(DOC), FakeLLM(answers(critical=True, invented_quote=True)), log=lambda *_: None)
    conflict = next(f for f in review.findings if f.criterion == "consistent")
    assert [r.match for r in conflict.refs].count("missing") == 1


def test_finding_with_only_invented_quotes_is_dropped(make_doc):
    data = answers(critical=True)
    data["conflicts"]["conflicts"][0]["quotes"] = [{"quote": "Система шлёт SMS через спутник", "line": 3}]
    review = run_review(make_doc(DOC), FakeLLM(data), log=lambda *_: None)
    assert not any(f.criterion == "consistent" for f in review.findings)
    assert len(review.dropped) == 1


def finding(lines, problem, scope="set", severity="major"):
    return Finding(scope=scope, criterion="consistent", severity=severity, problem=problem,
                   recommendation="", refs=[Reference("q", n) for n in lines])


def test_duplicates_on_the_same_lines_are_merged_keeping_the_worst_severity():
    a = finding([39, 40], "Ф-3 противоречит Ф-4", severity="major")
    b = finding([39, 40], "Лимит push конфликтует со сменой порога", severity="critical")
    merged = _dedupe([a, b])
    assert len(merged) == 1 and merged[0].severity == "critical"


def test_findings_citing_each_other_by_line_are_merged():
    a = finding([175], "Статус 403 заявлен, но в строке 189 для чужой карты возвращается 404")
    b = finding([189], "Для чужой карты возвращается 404, а в строке 175 заявлен статус 403")
    merged = _dedupe([a, b])
    assert len(merged) == 1
    assert {r.line for r in merged[0].refs} == {175, 189}


def test_different_defects_stay_separate():
    a = finding([104], "Сохранение объявлено методом GET")
    b = finding([157], "Поле amount в ответе имеет строковый тип")
    assert len(_dedupe([a, b])) == 2
