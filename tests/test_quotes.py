from validator.quotes import QuoteIndex


def test_exact_quote_is_found_on_its_line(sample):
    match = QuoteIndex(sample).locate("На карту приходится максимум один такой push за календарный месяц")
    assert match.status == "exact"
    assert "максимум один такой push" in sample.lines[match.line - 1]


def test_quote_survives_formatting_differences(sample):
    # the model wraps the quote in «», changes case and spacing: still the same place
    match = QuoteIndex(sample).locate("«На  карту приходится МАКСИМУМ один такой push»")
    assert match.status in {"exact", "approx"}
    assert "максимум один такой push" in sample.lines[match.line - 1]


def test_invented_quote_is_missing(sample):
    match = QuoteIndex(sample).locate("Система отправляет SMS каждые пять минут через спутник")
    assert match.status == "missing"
    assert match.line is None
