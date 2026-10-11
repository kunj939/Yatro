"""LLM providers behind one interface.

OpenAICompatibleProvider speaks the /chat/completions protocol, which both OpenRouter and a
self-hosted vLLM server (e.g. Qwen on A100/H100/L40S) implement. Moving from OpenRouter to vLLM is a
configuration change: LLM_PROVIDER=openai_compatible + OPENAI_COMPAT_BASE_URL.
"""
from __future__ import annotations

import json
import logging
import re
import time

from app.core.config import get_settings
from app.core.errors import ProviderUnavailable
from app.providers import http
from app.providers.base import LLMProvider
from app.providers.types import LLMMessage, LLMResult

log = logging.getLogger("tripai.llm")


def parse_json_loose(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                value = json.loads(m.group(0))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"
    live = True
    supports_json_schema = True

    def __init__(self, base_url: str, api_key: str | None, model: str, extra_headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}

    def _payload(self, messages, json_schema, temperature, max_tokens) -> dict:
        payload = {"model": self.model, "messages": [{"role": m.role, "content": m.content} for m in messages],
                   "temperature": temperature, "max_tokens": max_tokens or get_settings().llm_max_tokens}
        if json_schema and self.supports_json_schema:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": json_schema.get("title", "output"), "strict": True,
                                                          "schema": json_schema}}
        return payload

    def complete(self, messages: list[LLMMessage], *, json_schema: dict | None = None, temperature: float = 0.3,
                 max_tokens: int | None = None) -> LLMResult:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = self._payload(messages, json_schema, temperature, max_tokens)
        started = time.perf_counter()
        try:
            resp = http.request("POST", f"{self.base_url}/chat/completions", provider=self.name, headers=headers,
                                json=payload, timeout=get_settings().llm_timeout_seconds, retries=0,
                                group="llm", idempotent=False)  # billed per answer: never retried (the template text is the fallback)
        except ProviderUnavailable:
            if json_schema and "response_format" in payload:  # model may not support structured outputs
                payload["response_format"] = {"type": "json_object"}
                resp = http.request("POST", f"{self.base_url}/chat/completions", provider=self.name, headers=headers,
                                    json=payload, timeout=get_settings().llm_timeout_seconds, retries=0,
                                    group="llm", idempotent=False)
            else:
                raise
        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            raise ProviderUnavailable(f"{self.name} returned no choices.")
        text = choices[0].get("message", {}).get("content") or ""
        usage = body.get("usage") or {}
        if get_settings().gateway_enabled and isinstance(usage.get("cost"), (int, float)):
            from app.gateway import adjust_cost
            adjust_cost("llm", float(usage["cost"]) - 0.001)  # the reservation was an estimate; the answer says what it cost
        data = parse_json_loose(text) if json_schema else None
        if json_schema and data is None:
            raise ProviderUnavailable(f"{self.name} returned malformed JSON.")
        return LLMResult(text=text, data=data, provider=self.name, model=body.get("model", self.model),
                         prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                         cost_usd=usage.get("cost"), latency_ms=int((time.perf_counter() - started) * 1000))


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str, base_url: str, app_name: str, app_url: str):
        super().__init__(base_url, api_key, model, {"HTTP-Referer": app_url, "X-Title": app_name})

    def _payload(self, messages, json_schema, temperature, max_tokens) -> dict:
        payload = super()._payload(messages, json_schema, temperature, max_tokens)
        payload["usage"] = {"include": True}  # OpenRouter returns cost in usage
        return payload


class TemplateLLMProvider(LLMProvider):
    """Deterministic, offline stand-in. The LLM service detects it and uses template narration."""
    name = "template"
    model = "deterministic-templates"

    def complete(self, messages, *, json_schema=None, temperature=0.3, max_tokens=None) -> LLMResult:
        raise ProviderUnavailable("No language model configured.")
