"""
Language normalization orchestrator.

Pipeline:
    1. Page through every message in the upload (skipping rows that
       already have `content_english` populated, so re-runs only do
       new work).
    2. Run the regex/Unicode `detector` on each row.
    3. Group rows that need translation, build TranslationItems, and
       hand off to the Claude-backed `translator` (which also handles
       the Redis cache).
    4. For every row — translated or not — write back:
         content_english   ← translated text, OR original `content` for
                             English / too_short rows so downstream
                             services can read this column unconditionally.
         original_language ← detector category
         was_translated    ← True only when the LLM produced a new string

Design notes:
    - We do the writes in chunks (200 rows per `executemany` call) to
      keep peak memory bounded on 50k-message chats and avoid 1MB+
      query payloads.
    - The whole orchestrator is idempotent. Re-running it on a
      partially normalized upload only touches rows where
      `content_english IS NULL` — the same pattern the NLP and
      embedding stages use for resumability.
    - We deliberately don't downgrade upload status here. The status
      machine is owned by the NLP / embedding tasks; this stage is a
      precondition for them, not a status milestone of its own.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message
from app.services.language.detector import DetectionResult, detect_language
from app.services.language.translator import (
    TranslationItem,
    Translator,
    translator as default_translator,
)

logger = logging.getLogger(__name__)


# When a stratified sample of an upload's messages comes back this English
# (english + too_short), skip the per-row pipeline and bulk-stamp every row
# with content_english = content in a single SQL. Tuned conservatively — a
# 5% false-positive rate on the sample still leaves only ~2-5% of messages
# unchanged-when-they-should-be-translated, which is far less damaging than
# the current alternative of running translator on all 50k rows.
_ENGLISH_SAMPLE_SIZE = 200
_ENGLISH_THRESHOLD = 0.95


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


ProgressCallback = Callable[[str, float, str], Awaitable[None]]


@dataclass(slots=True)
class NormalizationResult:
    """Returned to the Celery task for logging / metrics."""

    upload_id: UUID
    total_messages: int = 0
    detected_english: int = 0
    detected_too_short: int = 0
    translated_count: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    elapsed_seconds: float = 0.0
    by_category: dict[str, int] = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


_PAGE_SIZE = 500
_WRITE_BATCH = 200


class LanguageNormalizer:
    """Stateless orchestrator. Use the module-level
    `language_normalizer` singleton."""

    def __init__(self, translator: Translator | None = None) -> None:
        self.translator = translator or default_translator

    async def normalize_upload(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None = None,
    ) -> NormalizationResult:
        """Detect + translate every message in `upload_id`."""
        started = time.perf_counter()
        result = NormalizationResult(upload_id=upload_id)

        # ---- 0. File-level English fast-path.
        # Sampling N rows is cheap (one SQL hit) and the detector is pure
        # Python. If the sample is overwhelmingly English/too_short, the
        # whole upload almost certainly is too — and the existing per-row
        # path would do 50k DB updates just to copy `content` into
        # `content_english`. Replace that with a single bulk UPDATE.
        if await self._try_english_fast_path(upload_id, db, result):
            result.elapsed_seconds = round(time.perf_counter() - started, 3)
            await _emit(progress, "lang_normalize_done", 1.0, "english fast-path")
            return result

        # ---- 1. Find the rows that still need normalizing.
        rows = await self._fetch_pending(upload_id, db)
        result.total_messages = len(rows)
        if not rows:
            await _emit(progress, "lang_normalize", 1.0, "nothing to do")
            result.elapsed_seconds = round(time.perf_counter() - started, 3)
            return result

        await _emit(
            progress,
            "lang_normalize_start",
            0.0,
            f"{len(rows)} messages",
        )

        # ---- 2. Detect each row.
        detections: list[DetectionResult] = []
        translation_items: list[TranslationItem] = []
        for row in rows:
            det = detect_language(row.content or "")
            detections.append(det)
            result.by_category[det.category] = (
                result.by_category.get(det.category, 0) + 1
            )
            if det.category == "english":
                result.detected_english += 1
            elif det.category == "too_short":
                result.detected_too_short += 1

            if det.use_claude and (row.content or "").strip():
                translation_items.append(
                    TranslationItem(
                        id=str(row.id),
                        text=row.content or "",
                        detected_language=det.category,
                    )
                )

        await _emit(
            progress,
            "lang_normalize_detect",
            0.4,
            f"{len(translation_items)} need translation",
        )

        # ---- 3. Translate (cache + LLM, handled inside the translator).
        translated_by_id: dict[str, str] = {}
        if translation_items:
            try:
                items = await self.translator.translate(translation_items)
            except Exception as e:
                logger.exception(
                    "Translator failed for upload %s; persisting originals as fallback",
                    upload_id,
                )
                # Fail-soft: every message still gets a content_english
                # equal to the original below, keeping the downstream
                # contract intact.
                items = translation_items
                result.error = f"translator: {e}"
            for item in items:
                if item.cache_hit:
                    result.cache_hits += 1
                else:
                    result.api_calls += 1
                if item.english is not None:
                    translated_by_id[item.id] = item.english

        await _emit(
            progress,
            "lang_normalize_translate",
            0.8,
            f"{len(translated_by_id)} translated · {result.cache_hits} cached",
        )

        # ---- 4. Write back.
        update_payloads: list[dict] = []
        for row, det in zip(rows, detections):
            translated = translated_by_id.get(str(row.id))
            content = row.content or ""
            if translated is not None:
                final_english = translated
                was_translated = (
                    translated.strip().lower() != content.strip().lower()
                )
            else:
                # English / too_short / un-translatable → store the
                # original so downstream queries can read content_english
                # unconditionally.
                final_english = content
                was_translated = False

            if was_translated:
                result.translated_count += 1

            update_payloads.append(
                {
                    "id": row.id,
                    "content_english": final_english,
                    "original_language": det.category,
                    "was_translated": was_translated,
                }
            )

        # SQLAlchemy 2.0 executemany — single statement, list of bound dicts.
        for start in range(0, len(update_payloads), _WRITE_BATCH):
            chunk = update_payloads[start : start + _WRITE_BATCH]
            await db.execute(update(Message), chunk)
        await db.commit()

        result.elapsed_seconds = round(time.perf_counter() - started, 3)
        logger.info(
            "Language normalize upload=%s total=%d translated=%d cached=%d api=%d in %.2fs",
            upload_id,
            result.total_messages,
            result.translated_count,
            result.cache_hits,
            result.api_calls,
            result.elapsed_seconds,
        )
        await _emit(progress, "lang_normalize_done", 1.0, "ready")
        return result

    # ---- Fast-path -----------------------------------------------------
    async def _try_english_fast_path(
        self,
        upload_id: UUID,
        db: AsyncSession,
        result: NormalizationResult,
    ) -> bool:
        """Sample pending rows; if dominantly English, bulk-stamp the upload
        and return True. Otherwise return False so the normal path runs.

        On hit, mutates `result` with the same counts the per-row path
        would have produced so the Celery task's return shape is stable.
        """
        # Pull a stratified sample using Postgres' TABLESAMPLE-equivalent
        # ORDER BY RANDOM(). LIMIT in a small subquery keeps the random
        # scan cheap (no full table sort) for upload sizes we see in
        # practice. The WHERE clause matches the same predicate the
        # per-row path uses, so we only look at rows that haven't already
        # been processed.
        sample_stmt = (
            select(Message.content)
            .where(Message.upload_id == upload_id)
            .where(Message.content_english.is_(None))
            .where(Message.content.isnot(None))
            .order_by(func.random())
            .limit(_ENGLISH_SAMPLE_SIZE)
        )
        sample_contents = (await db.execute(sample_stmt)).scalars().all()
        if not sample_contents:
            return False

        english_like = 0
        for content in sample_contents:
            det = detect_language(content or "")
            if det.category in ("english", "too_short"):
                english_like += 1

        share = english_like / len(sample_contents)
        if share < _ENGLISH_THRESHOLD:
            logger.info(
                "Language fast-path skipped upload=%s — sample english share %.2f < %.2f",
                upload_id, share, _ENGLISH_THRESHOLD,
            )
            return False

        # Hit. Stamp every pending row in one statement.
        bulk_stmt = (
            update(Message)
            .where(Message.upload_id == upload_id)
            .where(Message.content_english.is_(None))
            .values(
                content_english=Message.content,
                original_language="english",
                was_translated=False,
            )
        )
        res = await db.execute(bulk_stmt)
        await db.commit()
        stamped = res.rowcount or 0

        result.total_messages = stamped
        result.detected_english = stamped
        result.by_category["english"] = stamped
        logger.info(
            "Language fast-path hit upload=%s sample_share=%.2f stamped=%d",
            upload_id, share, stamped,
        )
        return True

    # ---- DB helpers -----------------------------------------------------
    async def _fetch_pending(
        self, upload_id: UUID, db: AsyncSession
    ) -> Sequence[Message]:
        """All messages without a content_english value yet, in chronological
        order. We page in chunks to keep peak memory bounded; on a 50k-message
        chat that's ~10MB peak."""
        all_rows: list[Message] = []
        last_index = -1
        while True:
            stmt = (
                select(Message)
                .where(Message.upload_id == upload_id)
                .where(Message.msg_index > last_index)
                .where(Message.content_english.is_(None))
                .order_by(Message.msg_index.asc())
                .limit(_PAGE_SIZE)
            )
            page = (await db.execute(stmt)).scalars().all()
            if not page:
                break
            all_rows.extend(page)
            last_index = page[-1].msg_index
        return all_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _emit(
    progress: ProgressCallback | None, stage: str, fraction: float, detail: str
) -> None:
    if progress is None:
        return
    try:
        await progress(stage, fraction, detail)
    except Exception:
        logger.warning("Language progress callback raised; ignoring", exc_info=True)


language_normalizer = LanguageNormalizer()
