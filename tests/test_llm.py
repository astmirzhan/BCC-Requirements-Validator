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


class ScriptedClient:
    """LLMClient whose provider call is replaced: 'busy' models answer 503."""

    @staticmethod
    def make(busy):
        from validator.llm import LLMClient

        class Client(LLMClient):
            def _call(self, model, system, prompt):
                self.tried.append(model)
                if model in busy:
                    raise LLMError("Gemini API 503: This model is currently experiencing high demand")
                return '{"ok": true}'

        config = LLMConfig(provider="gemini", model="strong", api_key="k", base_url=None,
                           fallback_models=["medium", "light"], cache_dir=None, log_path=None)
        client = Client(config)
        client.tried = []
        return client


def test_busy_model_is_skipped_on_the_next_step():
    client = ScriptedClient.make(busy={"strong"})
    assert client.complete_json("s", "p1", "slice") == {"ok": True}
    assert client.tried == ["strong", "medium"]
    client.tried.clear()
    client.complete_json("s", "p2", "requirements")
    assert client.tried == ["medium"]  # goes straight to the model that answered
    assert client.step_models == {"slice": "medium", "requirements": "medium"}
