"""LLM providers.

``OpenAIProvider`` calls the Chat Completions API over HTTPS (JSON mode).
``MockProvider`` is the deterministic local mode: it reports itself as
unavailable so every agent runs its deterministic analysis and labels results
as LOCAL. The product never crashes because an LLM is missing or failing.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from config import Settings
from llm.prompts import GUARDRAIL

logger = logging.getLogger("evo.llm")


class LLMError(RuntimeError):
    """The LLM call failed (network, HTTP status, invalid JSON)."""


class LLMUnavailableError(LLMError):
    """No LLM is configured."""


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1600) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "available": self.available,
            "mode": "llm" if self.available else "local",
            "label": f"LLM · {self.model}" if self.available else "DEMO / LOCAL ANALYSIS",
        }


class MockProvider(LLMProvider):
    """Deterministic offline mode: agents fall back to local analysis."""

    name = "local"
    model = "deterministic-rules"

    @property
    def available(self) -> bool:
        return False

    async def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1600) -> dict[str, Any]:
        raise LLMUnavailableError("No LLM configured; running deterministic local analysis.")


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1600) -> dict[str, Any]:
        if not self.available:
            raise LLMUnavailableError("OPENAI_API_KEY is not set.")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{GUARDRAIL}\n\n{system}"},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}/chat/completions", headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed ({exc.__class__.__name__})") from exc
        if response.status_code != 200:
            # Never log the request (it contains the API key header) or the full body.
            raise LLMError(f"LLM API returned HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"] or "{}"
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM returned a malformed response") from exc
        if not isinstance(result, dict):
            raise LLMError("LLM returned JSON that is not an object")
        return result


def create_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockProvider()
    if settings.openai_api_key and settings.llm_provider in {"auto", "openai"}:
        return OpenAIProvider(
            settings.openai_api_key,
            settings.openai_model,
            settings.openai_base_url,
            settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "openai":
        logger.warning("EVO_LLM_PROVIDER=openai but OPENAI_API_KEY is empty; using local analysis")
    return MockProvider()
