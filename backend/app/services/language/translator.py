"""
Claude-backed translator for the language normalization layer.

Two paths:
    Path A — romanized / mixed code-switched
        Latin-script messages whose vocabulary signals a non-English
        language (Manglish, Hinglish, …) plus French/Spanish/German.
    Path B — script-based (Devanagari, Tamil, Arabic, Han, Hangul, …)
        Same Claude pipeline; the model handles transliteration and
        translation in one pass.

Both paths share the implementation: batch up to 30 messages per API
call, send a strict-JSON prompt, parse the response, write each
translation back into a Redis cache keyed by `MD5(original_content)`.
The cache is per-message (not per-batch) so a re-upload of the same
chat with one extra line only pays for the new line.

Cache:
    Key:    `lang:translation:<md5_hex>`
    Value:  the English string
    TTL:    30 days

Failure modes:
    - No `ANTHROPIC_API_KEY`        → Translator.is_enabled returns False;
                                      the normalizer should detect this
                                      up-front and skip the translation
                                      stage cleanly.
    - Per-batch Claude failure      → that batch's messages return None
                                      (translation skipped, original kept).
    - Malformed JSON in response    → same as above; we don't crash the
                                      whole upload over one batch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.cache import cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Spec-mandated model for this layer. Different from settings.LLM_MODEL on
# purpose — translation is a narrow task and we want a stable, dated build
# so output formatting doesn't drift.
TRANSLATION_MODEL = "claude-sonnet-4-20250514"

# Max messages per Claude API call. Keeps the prompt under ~12k tokens
# (30 × ~400 chars) which is well under the model's context but big
# enough that the system-prompt overhead is amortized.
BATCH_SIZE = 30

# Cache TTL — 30 days, per spec. Entries are stable: the same input text
# always maps to the same English output, so we never need to invalidate.
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30

# Per-message content cap. Anything longer is truncated before we send
# it to Claude. Real chat messages rarely exceed this.
MAX_MESSAGE_CHARS = 1000

# How many concurrent batches we let run. Above 4 the Anthropic rate
# limits start kicking in for typical accounts.
MAX_CONCURRENT_BATCHES = 4


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslationItem:
    """One entry in / out of the translator.

    `id` is whatever string the caller wants to use as a key — usually
    the Message UUID stringified or the UCJ msg_id token. The translator
    doesn't care; it just round-trips the value so the caller can map
    results back."""

    id: str
    text: str
    """Original (non-English) content."""

    detected_language: str
    """Detector category, used as a hint in the LLM prompt."""

    english: str | None = None
    """Populated after `translate(...)`. None on a per-batch failure."""

    cache_hit: bool = False
    """True when we resolved this item from Redis without an API call."""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are a precise translator. Convert chat messages from any language "
    "(romanized Indian languages like Manglish, Hinglish, Tanglish, "
    "Tenglish, Bengali Roman, plus French, Spanish, German, Arabic, "
    "Devanagari, Tamil, Telugu, Kannada, Malayalam, Bengali, Han Chinese, "
    "Hangul Korean, Cyrillic, or any mix of these with English) into "
    "natural conversational English.\n\n"
    "Rules:\n"
    "- Preserve the original tone (humor, sadness, anger, sarcasm, "
    "  affection). Don't soften or moralize.\n"
    "- Don't add information that wasn't in the original. If something is "
    "  ambiguous, translate it as-is rather than guessing context.\n"
    "- Keep proper nouns (names, places, brands) unchanged.\n"
    "- If a message is already in English, return it verbatim.\n"
    "- Drop emojis from the translation but keep them in the original "
    "  text — they're handled separately downstream.\n"
    "- Output spoken/written English a native speaker would actually use, "
    "  not literal word-by-word substitution.\n\n"
    "Return ONLY valid JSON, no preamble, no code fences, no commentary:\n"
    '{"translations":[{"id":"<id>","english":"<text>"}, ...]}\n'
    "Every input id must appear in the output. If you genuinely can't "
    "translate one, return its original text unchanged in `english`."
)


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------


class Translator:
    """Stateless wrapper around the Claude client + Redis cache.

    Usage:
        items = [TranslationItem(...), ...]
        translated = await translator.translate(items)
    """

    def __init__(self, model: str = TRANSLATION_MODEL) -> None:
        self.model = model
        self._client: Any = None
        self._client_unavailable: bool = False

    @property
    def is_enabled(self) -> bool:
        """Whether translation can run. Cheap — does not construct the client."""
        from app.services.llm import get_llm_client

        return get_llm_client().is_enabled()

    # ---- Public API ------------------------------------------------------
    async def translate(
        self, items: list[TranslationItem]
    ) -> list[TranslationItem]:
        """Translate every item in `items` in place. Returns the same list
        with `english` and `cache_hit` populated. Items the API couldn't
        process keep `english=None` so callers can decide on a fallback."""
        if not items:
            return items

        # 1. Resolve cache hits first. These cost zero API tokens.
        misses: list[TranslationItem] = []
        for item in items:
            cached = await _cache_get(item.text)
            if cached is not None:
                item.english = cached
                item.cache_hit = True
            else:
                misses.append(item)

        if not misses:
            return items

        # 2. If the LLM client is unavailable, don't try the API path.
        if not self.is_enabled:
            logger.info(
                "Translator: no LLM provider configured; %d cache-misses pass through untranslated",
                len(misses),
            )
            return items

        from app.services.llm import get_llm_client

        client = get_llm_client()

        # 3. Batch the misses. Limit concurrency with a semaphore so we
        # don't fan out 50 simultaneous Claude calls on huge chats.
        sem = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)
        batches = [misses[i : i + BATCH_SIZE] for i in range(0, len(misses), BATCH_SIZE)]

        async def run_batch(batch: list[TranslationItem]) -> None:
            async with sem:
                await self._translate_batch(client, batch)

        await asyncio.gather(*(run_batch(b) for b in batches))
        return items

    # ---- Internals -------------------------------------------------------
    async def _translate_batch(
        self, client: Any, batch: list[TranslationItem]
    ) -> None:
        """Translate one batch via the configured LLM. Mutates each item's `english`."""
        # Build the user prompt. We include the detected_language as a hint
        # so the model doesn't waste tokens re-detecting (same answer it
        # would arrive at, but cheaper).
        lines: list[str] = []
        for item in batch:
            text = (item.text or "").replace("\n", " ").strip()
            if len(text) > MAX_MESSAGE_CHARS:
                text = text[:MAX_MESSAGE_CHARS] + "…"
            lines.append(
                f'{{"id":"{item.id}","language":"{item.detected_language}",'
                f'"text":{json.dumps(text)}}}'
            )
        user_prompt = (
            "Translate each of the following chat messages to English.\n\n"
            "Inputs (one JSON object per line):\n"
            + "\n".join(lines)
            + "\n\nReturn the JSON described in your instructions."
        )

        try:
            raw = await client.complete(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=4000,
                temperature=0.2,
                json_mode=True,
            )
        except Exception as e:
            logger.warning(
                "Translator batch failed (%d items): %s", len(batch), e
            )
            return

        results = _parse_translations(raw)
        if not results:
            return

        # Write back into the items + warm the cache.
        for item in batch:
            translated = results.get(item.id)
            if translated is None:
                continue
            # Skip degenerate "translations" that just echo the original
            # verbatim — keeps the cache clean and lets callers tell
            # "Claude tried but had nothing to do" apart from a hit.
            item.english = translated
            await _cache_set(item.text, translated)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_key(text: str) -> str:
    """MD5 of the original text. We use MD5 because the cache key is
    purely a content-addressable lookup — collision resistance against
    adversarial inputs isn't a concern here, and MD5 produces a tight,
    fixed-width key Redis can index efficiently."""
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


async def _cache_get(text: str) -> str | None:
    if not text:
        return None
    key = f"lang:translation:{_hash_key(text)}"
    raw = await cache.get_json(key)
    if isinstance(raw, dict):
        # Stored as {"english": "..."} so future fields (e.g. detector
        # category) can ride along without breaking the existing entries.
        value = raw.get("english")
        return value if isinstance(value, str) else None
    if isinstance(raw, str):
        return raw
    return None


async def _cache_set(text: str, english: str) -> None:
    if not text or english is None:
        return
    key = f"lang:translation:{_hash_key(text)}"
    await cache.set_json(
        key,
        {"english": english, "model": TRANSLATION_MODEL},
        ttl_seconds=CACHE_TTL_SECONDS,
    )


def _extract_text(msg: Any) -> str:
    """Concatenate text content blocks from an Anthropic response."""
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out)


def _parse_translations(raw: str) -> dict[str, str]:
    """Tolerant JSON parse. Strips code fences if present, validates the
    expected shape, returns an `id -> english` map."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Translator: couldn't parse response: %s", raw[:200])
        return {}
    items = payload.get("translations")
    if not isinstance(items, list):
        return {}
    out: dict[str, str] = {}
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id")
        english = entry.get("english")
        if isinstance(item_id, str) and isinstance(english, str):
            out[item_id] = english
    return out


# Convenience iterable form for callers that want a streaming API.
def chunk(items: Iterable[TranslationItem], size: int = BATCH_SIZE):
    buf: list[TranslationItem] = []
    for it in items:
        buf.append(it)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# Module-level singleton — translators are stateless past the lazy client.
translator = Translator()
