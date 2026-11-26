"""Provider-agnostic LLM client with JSON output, retries, disk cache and call log.

Providers:
- gemini: Google Gemini API (google-genai SDK).
- openai: any OpenAI-compatible Chat Completions endpoint (OpenAI, Ollama, OpenRouter, vLLM),
  called over plain HTTP so no extra dependency is needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ATTEMPTS_PER_MODEL = 3
CHAIN_ROUNDS = int(os.getenv("LLM_CHAIN_ROUNDS", "4"))
CHAIN_PAUSE = 60


class LLMError(Exception):
    """Raised when the model cannot return a usable answer."""


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    temperature: float = 0.0
    fallback_models: list[str] = field(default_factory=list)
    cache_dir: Path | None = Path(".cache/llm")
    read_cache: bool = True
    log_path: Path | None = Path("logs/llm_calls.jsonl")

    @classmethod
    def from_env(cls, model_override: str | None = None, use_cache: bool = True) -> "LLMConfig":
        provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        if provider == "gemini":
            model = model_override or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            api_key = os.getenv("GEMINI_API_KEY")
            base_url = None
            fallbacks = _split(os.getenv("GEMINI_FALLBACK_MODELS", ""))
        elif provider in {"openai", "ollama"}:
            default_url = "http://localhost:11434/v1" if provider == "ollama" else "https://api.openai.com/v1"
            model = model_override or os.getenv("OPENAI_MODEL") or os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
            api_key = os.getenv("OPENAI_API_KEY") or ("ollama" if provider == "ollama" else None)
            base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OLLAMA_BASE_URL", default_url)
            if provider == "ollama" and not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            provider = "openai"
            fallbacks = _split(os.getenv("OPENAI_FALLBACK_MODELS", ""))
        else:
            raise LLMError(f"Неизвестный LLM_PROVIDER={provider}. Допустимо: gemini, openai, ollama")
        if not api_key or api_key.startswith("your_"):
            raise LLMError(
                "Не найден API-ключ. Скопируйте .env.example в .env и укажите GEMINI_API_KEY "
                "(или OPENAI_API_KEY для OpenAI-совместимого провайдера)."
            )
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            fallback_models=[m for m in fallbacks if m != model],
            read_cache=use_cache,
        )


@dataclass
class LLMClient:
    config: LLMConfig
    calls: int = 0
    cache_hits: int = 0
    models_used: list[str] = field(default_factory=list)
    step_models: dict[str, str] = field(default_factory=dict)
    _gemini: object = field(default=None, repr=False)

    @property
    def label(self) -> str:
        models = self.models_used or [self.config.model]
        return ", ".join(f"{self.config.provider}:{m}" for m in models)

    # ---- public API -------------------------------------------------------

    def complete_json(self, system: str, prompt: str, step: str) -> dict:
        """Ask the model for a JSON object.

        Each model gets a few attempts; an overloaded or out-of-quota model hands over to the next
        fallback. When every model is overloaded, the whole chain is retried after a pause.
        """
        candidates = [self.config.model, *self.config.fallback_models]
        for model in candidates:
            cached = self._cache_get(self._key(model, system, prompt))
            if cached is not None:
                self.cache_hits += 1
                self._used(step, model)
                return cached

        last_error: LLMError | None = None
        for round_no in range(1, CHAIN_ROUNDS + 1):
            for model in candidates:
                try:
                    return self._complete_with(model, system, prompt, step)
                except LLMError as exc:
                    last_error = exc
                    if not (_is_overloaded(exc) or _is_daily_quota(exc)):
                        raise
                    print(f"  ! {step}: {model} недоступна ({_short(exc)}), пробую следующую модель")
            if round_no < CHAIN_ROUNDS:
                print(f"  ! {step}: все модели заняты, жду {CHAIN_PAUSE} с (круг {round_no}/{CHAIN_ROUNDS})")
                time.sleep(CHAIN_PAUSE)
        if last_error and _is_daily_quota(last_error):
            raise LLMError(
                "Исчерпан дневной лимит бесплатного тарифа для всех моделей. Укажите другую модель "
                "(--model, GEMINI_MODEL) или подождите сброса лимита."
            ) from last_error
        raise LLMError(f"{step}: модель не ответила: {last_error}")

    def _complete_with(self, model: str, system: str, prompt: str, step: str) -> dict:
        for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
            started = time.monotonic()
            try:
                raw = self._call(model, system, prompt)
                data = parse_json(raw)
            except LLMError as exc:
                # Failed calls to an overloaded model still count against the free quota,
                # so hand over to the next model at once instead of retrying the same one.
                if _is_overloaded(exc) or _is_daily_quota(exc):
                    raise
                wait = _retry_delay(exc, attempt)
                if wait is None or attempt == ATTEMPTS_PER_MODEL:
                    raise
                print(f"  ! {step}: {model}: {_short(exc)}. Повтор через {wait:.0f} с")
                time.sleep(wait)
                continue
            self.calls += 1
            self._used(step, model)
            self._log(step, model, system, prompt, raw, time.monotonic() - started)
            self._cache_put(self._key(model, system, prompt), data)
            return data
        raise LLMError("unreachable")

    def _key(self, model: str, system: str, prompt: str) -> str:
        payload = json.dumps([self.config.provider, model, system, prompt], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _used(self, step: str, model: str) -> None:
        self.step_models[step] = model
        if model not in self.models_used:
            self.models_used.append(model)

    # ---- providers --------------------------------------------------------

    def _call(self, model: str, system: str, prompt: str) -> str:
        if self.config.provider == "gemini":
            return self._call_gemini(model, system, prompt)
        return self._call_openai_compatible(model, system, prompt)

    def _call_gemini(self, model: str, system: str, prompt: str) -> str:
        from google import genai
        from google.genai import errors, types

        if self._gemini is None:
            self._gemini = genai.Client(
                api_key=self.config.api_key,
                # Our own loop handles retries and fallbacks; thinking models need a long deadline.
                http_options=types.HttpOptions(timeout=600_000, retry_options=types.HttpRetryOptions(attempts=1)),
            )
        try:
            response = self._gemini.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=self.config.temperature,
                    response_mime_type="application/json",
                ),
            )
        except errors.APIError as exc:
            raise LLMError(f"Gemini API {exc.code}: {exc.message}") from exc
        except Exception as exc:  # network errors from the SDK transport
            raise LLMError(f"Сетевая ошибка Gemini: {exc}") from exc
        if not response.text:
            raise LLMError("Gemini вернул пустой ответ")
        return response.text

    def _call_openai_compatible(self, model: str, system: str, prompt: str) -> str:
        body = {
            "model": model,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"Сетевая ошибка: {exc}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Неожиданный ответ провайдера: {str(payload)[:300]}") from exc

    # ---- cache and log ----------------------------------------------------

    def _cache_get(self, key: str) -> dict | None:
        if not self.config.cache_dir or not self.config.read_cache:
            return None
        path = self.config.cache_dir / f"{key}.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else None

    def _cache_put(self, key: str, data: dict) -> None:
        if not self.config.cache_dir:
            return
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.config.cache_dir / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    def _log(self, step: str, model: str, system: str, prompt: str, raw: str, seconds: float) -> None:
        if not self.config.log_path:
            return
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "model": f"{self.config.provider}:{model}",
            "seconds": round(seconds, 1),
            "system": system,
            "prompt": prompt,
            "response": raw,
        }
        with self.config.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_json(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Ответ модели не является JSON")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ответ модели не является корректным JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("Ожидался JSON-объект")
    return data


def _short(exc: Exception) -> str:
    return str(exc).split(". {")[0][:90]


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_overloaded(exc: LLMError) -> bool:
    message = str(exc)
    return any(marker in message for marker in ("503", "504", "UNAVAILABLE", "DEADLINE_EXCEEDED", "high demand", "overloaded", "500"))


def _is_daily_quota(exc: LLMError) -> bool:
    message = str(exc)
    return "429" in message and (
        "per day" in message.lower() or "PerDay" in message or "exceeded your current quota" in message
    )


def _retry_delay(exc: LLMError, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None when the error is not transient."""
    message = str(exc)
    if any(code in message for code in ("400", "401", "403", "404")) and "429" not in message:
        return None
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        hint = re.search(r"retry in ([\d.]+)s", message, re.I)
        if _is_daily_quota(exc):
            return None
        return float(hint.group(1)) + 1 if hint else min(60.0, 10.0 * attempt)
    return min(30.0, 3.0 * 2 ** (attempt - 1))
