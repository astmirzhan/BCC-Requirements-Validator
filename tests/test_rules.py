from validator.rules import run_gap_rules, run_rules


def rules_hit(findings):
    return {f.rule for f in findings}


def test_sample_document_triggers_the_planted_rule_defects(sample):
    findings = run_rules(sample) + run_gap_rules(sample)
    hit = rules_hit(findings)
    for rule in ["http-method", "unit-mismatch", "no-version", "no-rollback-plan"]:
        assert rule in hit, f"rule {rule} did not fire on the sample"
    assert all(f.verified for f in findings), "every rule finding must point to a line"


def test_get_used_for_saving_is_critical(make_doc):
    doc = make_doc("| **Description** | Сохранение порога |\n| **REST type** | GET |\n")
    found = [f for f in run_rules(doc) if f.rule == "http-method"]
    assert found and found[0].severity == "critical"


def test_vague_wording_is_reported_with_its_line(make_doc):
    doc = make_doc("Версия 1.0\n\nPush приходит быстро после покупки.\n")
    vague = [f for f in run_rules(doc) if f.rule == "vague-term"]
    assert vague and vague[0].refs[0].line == 3


def test_clean_text_has_no_vague_findings(make_doc):
    doc = make_doc("Версия 1.0\n\nPush приходит не позже 60 секунд после авторизации покупки.\n")
    assert not [f for f in run_rules(doc) if f.rule == "vague-term"]


def test_rollback_plan_described_means_no_finding(make_doc):
    doc = make_doc("Версия 1.0\n\nОткат: функция выключается флагом за 5 минут.\n")
    assert run_gap_rules(doc) == []


def test_rollback_missing_entirely(make_doc):
    doc = make_doc("Версия 1.0\n\nКлиент получает push.\n")
    assert rules_hit(run_gap_rules(doc)) == {"no-rollback-plan"}
