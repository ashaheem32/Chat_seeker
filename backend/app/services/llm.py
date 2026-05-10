"""
Provider-agnostic LLM client.

One small abstraction every AI feature in the app calls instead of
constructing an Anthropic / OpenAI SDK directly. Picks the backend at
runtime based on which API key is configured:

    OPENAI_API_KEY set        → OpenAI Chat Completions
    only ANTHROPIC_API_KEY    → Anthropic Messages API
    neither                   → is_enabled() returns False; callers should
                                fall back to deterministic / heuristic paths

OpenAI is preferred when both are set, since the rest of the app already
uses it for embeddings — keeping a single billed provider is cleaner.

Why an abstraction at all: every feature (NL search, conflict themes,
health-score narrative, love-language classifier, translator) builds its
own prompt and parses its own response. The transport layer was the part
duplicated across files; concentrating it here means we can swap models,
add retry/timeout policies, or route by request shape from one spot.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from app.core.config import settings

logger = logging.getLogger(__name__)


# Sensible defaults. Override per call when a feature needs more tokens
# or a tighter creativity budget.
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


Provider = Literal["openai", "anthropic"]


class LLMClient:
    """Lazy, single-instance LLM transport. Construct via `get_llm_client()`."""

    def __init__(self) -> None:
        self._provider: Provider | None = None
        self._openai: Any = None
        self._anthropic: Any = None
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Pick a provider based on configured keys. Idempotent."""
        if self._initialized:
            return
        self._initialized = True

        if settings.OPENAI_API_KEY:
            try:
                from openai import AsyncOpenAI

                self._openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                self._provider = "openai"
                logger.info("LLMClient using OpenAI (%s)", self.openai_model)
                return
            except ImportError:  # pragma: no cover
                logger.warning("openai SDK missing; falling back to Anthropic if available")

        if settings.ANTHROPIC_API_KEY:
            try:
                from anthropic import AsyncAnthropic

                self._anthropic = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
                self._provider = "anthropic"
                logger.info("LLMClient using Anthropic (%s)", self.anthropic_model)
                return
            except ImportError:  # pragma: no cover
                logger.warning("anthropic SDK missing")

        logger.info("LLMClient: no provider configured; AI features will use fallbacks")

    @property
    def provider(self) -> Provider | None:
        self._init()
        return self._provider

    @property
    def openai_model(self) -> str:
        # `LLM_MODEL` is the legacy field; only honor it when it looks like
        # an OpenAI model name. Otherwise use the safe default.
        m = getattr(settings, "LLM_MODEL", "") or ""
        return m if m.startswith(("gpt-", "o1", "o3", "o4")) else _DEFAULT_OPENAI_MODEL

    @property
    def anthropic_model(self) -> str:
        m = getattr(settings, "LLM_MODEL", "") or ""
        return m if m.startswith("claude-") else _DEFAULT_ANTHROPIC_MODEL

    def is_enabled(self) -> bool:
        return self.provider is not None

    # ------------------------------------------------------------------
    # Non-streaming completion
    # ------------------------------------------------------------------

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """Single round-trip completion. Returns the assistant text.

        `json_mode=True` asks the provider to constrain output to a JSON
        object (OpenAI: response_format=json_object; Anthropic: relies on
        the prompt to ask for JSON, which is what the existing services
        already do).
        """
        self._init()
        if self._provider is None:
            raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")

        if self._provider == "openai":
            return await self._openai_complete(
                system=system,
                user=user,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
            )
        return await self._anthropic_complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _openai_complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.openai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._openai.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    async def _anthropic_complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        msg = await self._anthropic.messages.create(
            model=self.anthropic_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = getattr(msg, "content", None) or []
        out: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                out.append(text)
        return "\n".join(out).strip()

    # ------------------------------------------------------------------
    # Streaming completion
    # ------------------------------------------------------------------

    async def stream(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Yield text deltas as they arrive. Caller concatenates."""
        self._init()
        if self._provider is None:
            raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")

        if self._provider == "openai":
            async for delta in self._openai_stream(
                system=system, user=user, max_tokens=max_tokens, temperature=temperature
            ):
                yield delta
        else:
            async for delta in self._anthropic_stream(
                system=system, user=user, max_tokens=max_tokens, temperature=temperature
            ):
                yield delta

    async def _openai_stream(
        self, *, system: str, user: str, max_tokens: int, temperature: float
    ) -> AsyncIterator[str]:
        stream = await self._openai.chat.completions.create(
            model=self.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def _anthropic_stream(
        self, *, system: str, user: str, max_tokens: int, temperature: float
    ) -> AsyncIterator[str]:
        async with self._anthropic.messages.stream(
            model=self.anthropic_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text


# Module-level singleton. AI features grab this once.
_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton


__all__ = ["LLMClient", "Provider", "get_llm_client"]
