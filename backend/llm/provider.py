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


def _json_object(content: str) -> str:
    """Extract the JSON object from a response that may be fenced or wrapped in prose."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if text.startswith("{"):
        return text
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


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
    """Any OpenAI-compatible chat-completions endpoint (OpenAI, DeepSeek, local servers)."""

    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    #: Name of the output-length parameter this endpoint expects.
    token_parameter: str = "max_completion_tokens"
    #: Models that cannot use ``response_format: json_object`` (JSON is then requested in the prompt).
    no_json_mode: tuple[str, ...] = ()

    async def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1600) -> dict[str, Any]:
        if not self.available:
            raise LLMUnavailableError(f"No API key configured for the {self.name} provider.")
        json_mode = not any(name in self.model for name in self.no_json_mode)
        instruction = f"{GUARDRAIL}\n\n{system}"
        if not json_mode:
            instruction += "\n\nReturn only the JSON object, with no markdown fences and no text around it."
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": prompt},
            ],
            self.token_parameter: max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
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
            result = json.loads(_json_object(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM returned a malformed response") from exc
        if not isinstance(result, dict):
            raise LLMError("LLM returned JSON that is not an object")
        return result


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek (``deepseek-chat`` / ``deepseek-reasoner``) over its OpenAI-compatible API.

    DeepSeek expects ``max_tokens``, and the reasoning model has no JSON mode, so
    the schema is requested in the prompt and fenced output is unwrapped.
    """

    name = "deepseek"
    token_parameter = "max_tokens"
    no_json_mode = ("reasoner",)


def create_provider(settings: Settings) -> LLMProvider:
    """Pick a provider: explicit ``EVO_LLM_PROVIDER``, else the first configured key, else local."""
    choice = settings.llm_provider
    if choice == "mock":
        return MockProvider()
    if settings.deepseek_api_key and choice in {"auto", "deepseek"}:
        return DeepSeekProvider(
            settings.deepseek_api_key,
            settings.deepseek_model,
            settings.deepseek_base_url,
            settings.llm_timeout_seconds,
        )
    if settings.openai_api_key and choice in {"auto", "openai"}:
        return OpenAIProvider(
            settings.openai_api_key,
            settings.openai_model,
            settings.openai_base_url,
            settings.llm_timeout_seconds,
        )
    if choice in {"openai", "deepseek"}:
        variable = "DEEPSEEK_API_KEY" if choice == "deepseek" else "OPENAI_API_KEY"
        logger.warning("EVO_LLM_PROVIDER=%s but %s is empty; using local analysis", choice, variable)
    return MockProvider()
