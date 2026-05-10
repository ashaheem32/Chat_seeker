"""
Embedding generation.

Primary backend: OpenAI `text-embedding-3-small` (1536 dim, $0.02/M tokens).
This is the cheapest production-grade embedding API and works very well on
short, informal text — exactly what chat messages are.

Fallback backend: `sentence-transformers/all-MiniLM-L6-v2` (384 dim, free,
runs locally on CPU). Used automatically when no OPENAI_API_KEY is set or
when the OpenAI client raises an unrecoverable error (quota exhausted,
auth denied). Vectors are zero-padded to settings.EMBEDDING_DIMENSIONS so
they fit the same DB column — see `_pad_to_target_dim` for why this is
safe for cosine similarity.

Design notes:
    - Async-first. The OpenAI SDK has both sync and async clients;
      we use AsyncOpenAI so callers can `await generate_batch(...)`.
    - Retry policy is delegated to tenacity. Rate-limit errors get
      exponential backoff; auth / quota errors are not retried.
    - Cost accounting uses tiktoken (we already depend on it) so we
      report exact token counts, not estimates.
    - The fallback is loaded lazily only if the primary is unusable —
      a worker that has OPENAI_API_KEY set never pays the
      sentence-transformers import cost.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class EmbeddingProvider(str, enum.Enum):
    """Which backend produced a given embedding. Surfaced in cost reports
    and useful for debugging "why does similarity look weird on this chat"."""

    openai = "openai"
    local = "local"


@dataclass(slots=True)
class EmbeddingCost:
    """Token + dollar accounting for one generation call.

    Cost numbers are sourced from OpenAI's pricing page — kept here as a
    constant so the worker logs an estimate at runtime rather than us
    reconciling against billing exports."""

    provider: EmbeddingProvider
    input_tokens: int = 0
    estimated_usd: float = 0.0
    n_texts: int = 0

    def __add__(self, other: "EmbeddingCost") -> "EmbeddingCost":
        if self.provider != other.provider:
            # Mixing providers in one accumulator is a bug — don't paper over it.
            raise ValueError("cannot add EmbeddingCost across providers")
        return EmbeddingCost(
            provider=self.provider,
            input_tokens=self.input_tokens + other.input_tokens,
            estimated_usd=self.estimated_usd + other.estimated_usd,
            n_texts=self.n_texts + other.n_texts,
        )


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

# OpenAI text-embedding-3-small: $0.020 per 1M tokens (verify periodically
# at platform.openai.com/docs/pricing). We round to 6 dp internally so
# very small batches don't print as $0.000000.
_OPENAI_USD_PER_TOKEN = 0.020 / 1_000_000

# Local model is free — but we still record token counts so dashboards can
# compare "what would this have cost on OpenAI" against the local stack.
_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_LOCAL_NATIVE_DIM = 384


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class EmbeddingGenerator:
    """Lazy-loading embedding client with automatic OpenAI → local fallback.

    Instances are cheap to construct but model loading is deferred until the
    first `generate*` call. Use `get_embedding_generator()` for the
    process-wide singleton."""

    def __init__(
        self,
        model: str | None = None,
        target_dim: int | None = None,
    ) -> None:
        self.model = model or settings.EMBEDDING_MODEL
        self.target_dim = target_dim or settings.EMBEDDING_DIMENSIONS

        # Lazy-loaded clients.
        self._openai_client: Any = None
        self._tiktoken_encoder: Any = None
        self._local_model: Any = None
        self._provider: EmbeddingProvider | None = None
        self._fallback_active: bool = False

    # ---- Public API ------------------------------------------------------
    @property
    def provider(self) -> EmbeddingProvider:
        """Which backend will actually be used. Triggers backend probing."""
        self._ensure_backend()
        assert self._provider is not None
        return self._provider

    async def generate(self, text: str) -> list[float]:
        """Embed a single string. Empty inputs return a zero vector — they're
        cheap, never billed, and let callers avoid `if text: ...` checks."""
        if not text or not text.strip():
            return [0.0] * self.target_dim
        vectors, _cost = await self.generate_batch([text])
        return vectors[0]

    async def generate_batch(
        self, texts: list[str], batch_size: int = 100
    ) -> tuple[list[list[float]], EmbeddingCost]:
        """Embed many strings, returning (vectors, cost_summary). The cost
        summary aggregates across all sub-batches.

        OpenAI's embeddings endpoint accepts up to 2048 inputs per request,
        but smaller sub-batches give better failure granularity (a single
        bad row poisons the whole call) and keep individual responses
        small. 100 is the practical sweet spot for chat-length text.
        """
        if not texts:
            return [], EmbeddingCost(provider=self.provider, n_texts=0)

        self._ensure_backend()
        assert self._provider is not None

        all_vectors: list[list[float]] = []
        total_cost = EmbeddingCost(provider=self._provider)

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            if self._provider is EmbeddingProvider.openai:
                vectors, cost = await self._openai_embed(chunk)
            else:
                vectors, cost = await self._local_embed(chunk)
            all_vectors.extend(vectors)
            total_cost = total_cost + cost

        logger.info(
            "Generated %d embeddings via %s (%d tokens, ~$%.6f)",
            total_cost.n_texts,
            total_cost.provider.value,
            total_cost.input_tokens,
            total_cost.estimated_usd,
        )
        return all_vectors, total_cost

    # ---- preprocess_for_embedding ---------------------------------------
    @staticmethod
    def preprocess_for_embedding(
        message: Any, prev_message: Any | None = None
    ) -> str | None:
        """Format a Message ORM instance into the text we send to the model.

        Returns None for messages that should NOT be embedded — deleted,
        media-only, system, or empty. Callers use the None signal to skip
        the row without touching the embedding column.

        Format we settled on (in order of importance):
            "{sender}: {content}"

        Source of `content`:
            Reads `content_english` (written by the Layer-4 language
            normalization pipeline) when present and falls back to the
            original `content` otherwise. Embedding the English form
            gives semantically-comparable vectors across mixed-language
            chats — a Manglish "ende mone" embeds near "my son" instead
            of clustering with arbitrary Manglish noise.

        Why include the sender:
            Cosine similarity over chat snippets without speaker context
            collapses "I love you" said by either person into one cluster.
            Including the sender preserves "what did Alex say about X"
            queries — easily 30% of real-world ChatLens questions.

        Temporal context for very short messages:
            A bare "yes" or "lol" embeds into a near-meaningless point in
            vector space. We prepend the previous message as context so
            the embedding reflects what was being agreed with.
        """
        if message is None:
            return None
        # ORM attributes used here: is_deleted, has_media, msg_type,
        # content_english (preferred) / content (fallback), sender,
        # word_count, char_count.
        if getattr(message, "is_deleted", False):
            return None
        msg_type = getattr(message, "msg_type", "text")
        if msg_type in {"deleted", "system"}:
            return None

        content = _effective_content(message).strip()
        if not content:
            return None
        if getattr(message, "has_media", False) and len(content) < 10:
            # Media-only messages with only an alt-text or filename are
            # noisy — better skipped than indexed.
            return None

        sender = getattr(message, "sender", "") or ""

        # Very short → prepend prev for context. "Very short" defined by
        # word count rather than char count so a single emoji counts as
        # short even though it's many chars in UTF-8.
        word_count = getattr(message, "word_count", None)
        if word_count is None:
            word_count = len(content.split())
        if word_count < 3 and prev_message is not None:
            prev_content = _effective_content(prev_message).strip()
            prev_sender = getattr(prev_message, "sender", "") or ""
            if prev_content:
                return (
                    f"{prev_sender}: {prev_content}\n"
                    f"{sender}: {content}"
                )

        return f"{sender}: {content}" if sender else content

    # ---- Backend selection ----------------------------------------------
    def _ensure_backend(self) -> None:
        if self._provider is not None:
            return
        if settings.OPENAI_API_KEY:
            self._init_openai()
        else:
            logger.info("No OPENAI_API_KEY set; using local embedding fallback")
            self._init_local()

    def _init_openai(self) -> None:
        try:
            from openai import AsyncOpenAI

            import tiktoken
        except ImportError as e:  # pragma: no cover
            logger.warning("openai/tiktoken not importable (%s); falling back", e)
            self._init_local()
            return

        self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        try:
            # text-embedding-3-* uses cl100k_base.
            self._tiktoken_encoder = tiktoken.encoding_for_model(self.model)
        except Exception:
            self._tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        self._provider = EmbeddingProvider.openai
        logger.info("EmbeddingGenerator using OpenAI model=%s", self.model)

    def _init_local(self) -> None:
        # Imported here so that workers with an OpenAI key never pull the
        # ~80 MB sentence-transformers package into memory.
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "No embedding backend available: OPENAI_API_KEY is unset and "
                "sentence-transformers is not installed. Install the `nlp` "
                "Poetry group or set OPENAI_API_KEY."
            ) from e

        logger.info("Loading local embedding model %s", _LOCAL_MODEL)
        self._local_model = SentenceTransformer(_LOCAL_MODEL)
        self._provider = EmbeddingProvider.local
        self._fallback_active = True

    # ---- Backend implementations ----------------------------------------
    async def _openai_embed(
        self, texts: list[str]
    ) -> tuple[list[list[float]], EmbeddingCost]:
        """Call OpenAI with retry. Falls back to local on hard auth/quota errors."""
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            PermissionDeniedError,
            RateLimitError,
        )

        # Sanitize empty strings — the API rejects them. We embed a single
        # space and let the caller's None-handling skip the result.
        sanitized = [t if t and t.strip() else " " for t in texts]

        retryable = (RateLimitError, APIConnectionError, APITimeoutError)

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(retryable),
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                reraise=True,
            ):
                with attempt:
                    response = await self._openai_client.embeddings.create(
                        model=self.model,
                        input=sanitized,
                    )
        except (AuthenticationError, PermissionDeniedError) as e:
            # Quota / auth errors are not transient. Try the local model
            # so the upload still completes; if local isn't installed,
            # surface the real OpenAI error rather than a misleading
            # "OPENAI_API_KEY is unset" from _init_local.
            logger.error(
                "OpenAI auth/quota failure (%s); attempting local fallback",
                e,
            )
            try:
                self._init_local()
            except RuntimeError:
                raise RuntimeError(
                    f"OpenAI embedding request failed ({type(e).__name__}: {e}) "
                    "and no local fallback is available. Check that your "
                    "OPENAI_API_KEY in .env is valid (the current key returned "
                    "an auth error), or install the `nlp` Poetry group for a "
                    "local sentence-transformers backend."
                ) from e
            return await self._local_embed(texts)
        except BadRequestError as e:
            # Indicates malformed input or unsupported parameter — not
            # retryable, but also not fatal: drop to local.
            logger.error("OpenAI rejected the batch (%s); attempting local fallback", e)
            try:
                self._init_local()
            except RuntimeError:
                raise RuntimeError(
                    f"OpenAI rejected the batch ({e}) and no local fallback "
                    "is available. Install the `nlp` Poetry group to enable "
                    "the local sentence-transformers backend."
                ) from e
            return await self._local_embed(texts)

        vectors = [item.embedding for item in response.data]
        # Pad if the OpenAI model returns fewer dims than target (won't
        # happen for text-embedding-3-small at default settings, but
        # protects us when a custom dim parameter is used elsewhere).
        vectors = [_pad_to_target_dim(v, self.target_dim) for v in vectors]

        # Cost accounting: prefer the API's reported usage if present;
        # otherwise count tokens locally.
        usage = getattr(response, "usage", None)
        input_tokens = (
            int(usage.prompt_tokens) if usage and usage.prompt_tokens else
            sum(len(self._tiktoken_encoder.encode(t)) for t in sanitized)
        )
        cost = EmbeddingCost(
            provider=EmbeddingProvider.openai,
            input_tokens=input_tokens,
            estimated_usd=input_tokens * _OPENAI_USD_PER_TOKEN,
            n_texts=len(texts),
        )
        return vectors, cost

    async def _local_embed(
        self, texts: list[str]
    ) -> tuple[list[list[float]], EmbeddingCost]:
        """Run the local sentence-transformer. Wrapped in to_thread so the
        event loop isn't blocked by torch inference."""
        if self._local_model is None:
            self._init_local()
        assert self._local_model is not None

        sanitized = [t if t and t.strip() else " " for t in texts]
        vectors = await asyncio.to_thread(
            self._local_model.encode,
            sanitized,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=False,
        )
        # SentenceTransformer can return torch.Tensors; tolist() handles both.
        vectors = [list(map(float, v.tolist() if hasattr(v, "tolist") else v)) for v in vectors]
        vectors = [_pad_to_target_dim(v, self.target_dim) for v in vectors]

        # Local has no $ cost; record approx token count for parity.
        approx_tokens = sum(len(t.split()) for t in sanitized)  # rough word count
        cost = EmbeddingCost(
            provider=EmbeddingProvider.local,
            input_tokens=approx_tokens,
            estimated_usd=0.0,
            n_texts=len(texts),
        )
        return vectors, cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _effective_content(message: Any) -> str:
    """Pick the analysis text for a Message-shaped object.

    Reads `content_english` (written by the Layer-4 language normalization
    pipeline) when present, falls back to the original `content` otherwise.
    Tolerates both ORM Message instances and dict-like rows; returns "" when
    neither attribute is set."""
    english = getattr(message, "content_english", None)
    if english:
        return english
    original = getattr(message, "content", None)
    return original or ""


def _pad_to_target_dim(vec: list[float], target_dim: int) -> list[float]:
    """Zero-pad (or truncate) a vector to `target_dim`.

    Why zero padding is OK for cosine similarity:
        cos(a, b) = (a · b) / (|a| · |b|)
        Zeros in the padding contribute 0 to the dot product and
        do not change |a| or |b|. So a 384-dim vector padded to 1536 has
        identical cosine similarity to any other 384-dim vector padded
        the same way. Mixing native 1536-dim OpenAI vectors and padded
        384-dim local vectors in the same index would be unsafe — but
        we never mix providers within a single upload (see
        EmbeddingIndexer.index_upload).
    """
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    return list(vec) + [0.0] * (target_dim - len(vec))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_singleton: EmbeddingGenerator | None = None


def get_embedding_generator() -> EmbeddingGenerator:
    """Return the process-wide generator. Created on first call."""
    global _singleton
    if _singleton is None:
        _singleton = EmbeddingGenerator()
    return _singleton
