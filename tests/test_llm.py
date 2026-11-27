import pytest

from validator.llm import LLMConfig, LLMError, parse_json


def test_parse_json_accepts_fenced_and_wrapped_answers():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Вот ответ: {"a": 2} конец') == {"a": 2}


def test_parse_json_rejects_non_json():
    with pytest.raises(LLMError):
        parse_json("нет json")


def test_placeholder_key_from_env_example_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key_here")
    with pytest.raises(LLMError, match="API-ключ"):
        LLMConfig.from_env()
