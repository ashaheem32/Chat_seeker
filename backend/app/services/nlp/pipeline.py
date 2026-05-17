"""
NLP pipeline orchestrator.

Pipeline stages:
    1. preprocess — clean text for the models, expand abbreviations, etc.
    2. sentiment — twitter-roberta classification, write per-message label/score
    3. emotion   — j-hartmann classification, write per-message label/score
    4. keywords  — KeyBERT per-message, write into Message.topics array
    5. topics    — BERTopic over the whole conversation, write into
                   ChatUpload.ucj_data['ai_analysis']['topic_clusters']
    6. entities  — spaCy NER, store aggregated counts in
                   ChatUpload.ucj_data['ai_analysis']['entities']

Memory strategy:
    A 50,000-message chat could be ~50 MB of text in memory, plus ~100 MB
    of intermediate model activations. Loading every message at once + every
    sentiment and emotion logits tensor at once would push past 1.5 GB on
    a CPU worker. Instead we paginate: PAGE_SIZE messages at a time, run
    all per-message stages on that page, commit, move on. Conversation-
    level stages (BERTopic, entity aggregation) need the whole chat — for
    those we stream just the cleaned text, not the ORM rows.

Checkpointing:
    Each stage writes into a different DB column. We resume by querying
    "messages where this column is still NULL" — no separate checkpoint
    state needed. The pipeline is idempotent: re-running it on a partially
    processed upload will only touch unprocessed rows. This is simpler and
    more correct than tracking an "I got to message 12345 in stage X"
    pointer; the source of truth is the data itself.

Progress callbacks:
    Tests pass `None`. Production passes a coroutine that pushes a
    UploadProgressEvent over the WebSocket connection set up by the
    upload route. We don't import or depend on the WebSocket layer here;
    that's the caller's job.

Async / sync split:
    DB I/O is async (AsyncSession). Model inference is sync — torch /
    transformers are not async-aware. We run inference inside
    `asyncio.to_thread` so the event loop stays responsive to other
    tasks (e.g. progress callbacks, healthchecks).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatUpload, Message, ProcessingStatus
from app.services.nlp.emotion import EmotionAnalyzer, get_emotion_analyzer
from app.services.nlp.entities import EntityExtractor, get_entity_extractor
from app.services.nlp.preprocessor import TextPreprocessor
from app.services.nlp.sentiment import SentimentAnalyzer, get_sentiment_analyzer
from app.services.nlp.topics import TopicExtractor, get_topic_extractor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

#: Async callback emitted at every stage boundary and intermittently within
#: long stages. Signature: (stage_name, fraction_complete_0_to_1, detail).
ProcessingStage = str
ProgressCallback = Callable[[ProcessingStage, float, str], Awaitable[None]]


@dataclass(slots=True)
class StageTiming:
    stage: str
    seconds: float
    rows: int


@dataclass(slots=True)
class PipelineStats:
    """Summary returned to the Celery task for logging / metrics."""

    upload_id: UUID
    total_messages: int
    processed_messages: int
    stages: list[StageTiming] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Page size for the streaming "fetch → analyze → write" loop. Tuned so that
# ~64 messages fit comfortably in a sentiment batch on accelerator devices
# while still amortizing DB round-trip cost.
_PAGE_SIZE = 256

# Per-stage inference batch size. Inside one page (256 messages) we still
# pass the texts to the model in batches of this size. Picked by the
# analyzer at runtime based on device, but a sane fallback for callers that
# bypass the analyzer's heuristic:
_DEFAULT_BATCH_SIZE = 32

# Skip rules for "should this message be analyzed at all?".
_MIN_PROCESSABLE_WORDS = 3
_MIN_PROCESSABLE_CHARS = 10


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class NLPPipeline:
    """Async orchestrator. One instance per upload run, but the underlying
    analyzer singletons are shared across runs (model load is cached)."""

    def __init__(
        self,
        preprocessor: TextPreprocessor | None = None,
        sentiment: SentimentAnalyzer | None = None,
        emotion: EmotionAnalyzer | None = None,
        topics: TopicExtractor | None = None,
        entities: EntityExtractor | None = None,
        page_size: int = _PAGE_SIZE,
    ) -> None:
        self.preprocessor = preprocessor or TextPreprocessor()
        self.sentiment = sentiment or get_sentiment_analyzer()
        self.emotion = emotion or get_emotion_analyzer()
        self.topics = topics or get_topic_extractor()
        self.entities = entities or get_entity_extractor()
        self.page_size = page_size

    # ---- Entry point ----------------------------------------------------
    async def process_upload(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None = None,
    ) -> PipelineStats:
        """Run the full pipeline against `upload_id`. Updates rows in place.

        Stages run sequentially per page, then conversation-level stages
        run after all pages are done. ChatUpload.status is advanced through
        `nlp_processing` and finally `embedding` (the next pipeline stage,
        owned by the embedding worker).
        """
        stats = PipelineStats(upload_id=upload_id, total_messages=0, processed_messages=0)

        # ---- Sanity-check the upload exists and load total message count.
        upload = await db.get(ChatUpload, upload_id)
        if upload is None:
            raise ValueError(f"ChatUpload {upload_id} not found")

        # Mark upload as nlp_processing. Do this first so a crashed worker
        # leaves an obvious "stuck in nlp_processing" row that the operator
        # can find. We commit immediately so observers see the status change.
        upload.status = ProcessingStatus.nlp_processing
        upload.processing_error = None
        await db.commit()

        total = await db.scalar(
            select(func.count(Message.id)).where(Message.upload_id == upload_id)
        )
        stats.total_messages = int(total or 0)
        logger.info(
            "NLP pipeline starting upload_id=%s total_messages=%d",
            upload_id,
            stats.total_messages,
        )
        await _emit(progress, "nlp_start", 0.0, f"{stats.total_messages} messages")

        try:
            # ---- 1-4: per-message stages, paged through the table.
            per_msg_stats = await self._run_per_message_stages(
                upload_id, db, stats.total_messages, progress
            )
            stats.processed_messages = per_msg_stats["processed"]
            for stage, seconds, rows in per_msg_stats["timings"]:
                stats.stages.append(StageTiming(stage=stage, seconds=seconds, rows=rows))

            # ---- 5: conversation-level topics (BERTopic).
            # BERTopic's UMAP + Numba stack segfaults on Apple Silicon with
            # anaconda's numpy/MKL combo. Skip on macOS unless the operator
            # has explicitly opted in via CHATLENS_ENABLE_BERTOPIC=1.
            import os as _os
            import sys as _sys

            if _sys.platform == "darwin" and _os.getenv("CHATLENS_ENABLE_BERTOPIC") != "1":
                logger.info(
                    "Skipping conversation_topics stage (BERTopic disabled on macOS by default; "
                    "set CHATLENS_ENABLE_BERTOPIC=1 to force-enable)"
                )
            else:
                await self._run_conversation_topics(upload_id, db, progress, stats)

            # ---- 6: conversation-level entities.
            await self._run_conversation_entities(upload_id, db, progress, stats)

            # ---- Finalize: coordinate with the parallel embedding stage.
            # NLP and embeddings now run concurrently from the language
            # task. Whichever finishes second flips status to `done`; the
            # first finisher leaves status in the in-progress sentinel so
            # the search endpoint keeps 409'ing until both are ready.
            from app.core.cache import cache

            stages_done = await cache.mark_stage_complete(str(upload_id), "nlp")
            upload = await db.get(ChatUpload, upload_id)
            assert upload is not None
            # Respect a prior `failed` status — the embedding stage may have
            # already crashed. Overwriting to `done` here would mask a
            # broken upload as ready.
            if upload.status != ProcessingStatus.failed:
                if stages_done >= 2 or stages_done == -1:
                    upload.status = ProcessingStatus.done
                else:
                    upload.status = ProcessingStatus.embedding
            await db.commit()

            await _emit(progress, "nlp_done", 1.0, "ready for embedding")
            logger.info(
                "NLP pipeline finished upload_id=%s stages=%s",
                upload_id,
                [(s.stage, round(s.seconds, 2)) for s in stats.stages],
            )

        except Exception as e:
            logger.exception("NLP pipeline failed for upload_id=%s", upload_id)
            stats.error = str(e)
            # Roll back any in-flight transaction state, then write the
            # failure status in a fresh transaction so it actually persists.
            await db.rollback()
            upload = await db.get(ChatUpload, upload_id)
            if upload is not None:
                upload.status = ProcessingStatus.failed
                upload.processing_error = f"NLP pipeline: {e}"
                await db.commit()
            await _emit(progress, "nlp_failed", 1.0, stats.error)
            raise

        return stats

    # ---- Per-message stages ---------------------------------------------
    async def _run_per_message_stages(
        self,
        upload_id: UUID,
        db: AsyncSession,
        total_messages: int,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Page through messages, running preprocess → sentiment → emotion
        → keywords on each page, committing per page so a crash doesn't
        lose the work already done."""

        timings: dict[str, list[float]] = {
            "preprocess": [],
            "sentiment": [],
            "emotion": [],
            "keywords": [],
        }
        rows_processed = 0
        page_num = 0

        # We iterate by msg_index ascending so the work order matches the
        # logical conversation order — useful for live progress UIs that
        # render "currently analyzing message N of M".
        last_index = -1
        while True:
            page = await self._fetch_unprocessed_page(
                upload_id, db, last_index, self.page_size
            )
            if not page:
                break
            page_num += 1
            last_index = page[-1].msg_index

            # ---- 1. Preprocess
            t0 = time.perf_counter()
            cleaned: list[str] = []
            processable_indices: list[int] = []
            for i, msg in enumerate(page):
                if not _should_process(msg):
                    cleaned.append("")
                    continue
                cleaned.append(self.preprocessor.clean_for_nlp(_text(msg)))
                processable_indices.append(i)
            timings["preprocess"].append(time.perf_counter() - t0)

            # Skip the rest of this page entirely if nothing is processable.
            # We still write nothing back — the columns are nullable.
            if not processable_indices:
                rows_processed += len(page)
                await self._emit_page_progress(
                    progress, rows_processed, total_messages, "preprocess"
                )
                continue

            texts_for_inference = [cleaned[i] for i in processable_indices]

            # ---- 2. Sentiment
            t0 = time.perf_counter()
            sentiment_results = await asyncio.to_thread(
                self.sentiment.analyze_batch, texts_for_inference
            )
            timings["sentiment"].append(time.perf_counter() - t0)

            # ---- 3. Emotion
            t0 = time.perf_counter()
            emotion_results = await asyncio.to_thread(
                self.emotion.analyze_batch, texts_for_inference
            )
            timings["emotion"].append(time.perf_counter() - t0)

            # ---- 4. Keywords (per-message)
            t0 = time.perf_counter()
            keyword_results = await asyncio.to_thread(
                self.topics.extract_keywords_batch, texts_for_inference, 5
            )
            timings["keywords"].append(time.perf_counter() - t0)

            # ---- Bulk write back to the DB.
            updates: list[dict[str, Any]] = []
            for offset, page_idx in enumerate(processable_indices):
                msg = page[page_idx]
                s = sentiment_results[offset]
                e = emotion_results[offset]
                kws = keyword_results[offset]
                updates.append(
                    {
                        "id": msg.id,
                        "sentiment_score": s.score,
                        "sentiment_label": s.label,
                        "emotion_label": e.label,
                        "emotion_score": e.score,
                        # KeyBERT can return [] on near-empty messages; store
                        # NULL not [] so dashboards can distinguish "tried
                        # and got nothing" from "skipped".
                        "topics": kws if kws else None,
                    }
                )

            # SQLAlchemy 2.0 executemany-style update: one statement, list
            # of bound param dicts. Much faster than individual UPDATEs.
            await db.execute(update(Message), updates)
            await db.commit()

            rows_processed += len(page)
            await self._emit_page_progress(
                progress, rows_processed, total_messages, "per_message"
            )

        return {
            "processed": rows_processed,
            "timings": [
                (stage, sum(times), rows_processed)
                for stage, times in timings.items()
            ],
        }

    async def _fetch_unprocessed_page(
        self,
        upload_id: UUID,
        db: AsyncSession,
        after_msg_index: int,
        limit: int,
    ) -> Sequence[Message]:
        """Return the next page of messages that haven't been NLP-processed.

        We define "unprocessed" as `sentiment_label IS NULL` — that's the
        first NLP column we write, so a row missing it must not have been
        through the per-message stages yet. This makes the pipeline
        idempotent (safe to re-run after a crash) without a separate
        checkpoint table.
        """
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .where(Message.msg_index > after_msg_index)
            .where(Message.sentiment_label.is_(None))
            .order_by(Message.msg_index.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def _emit_page_progress(
        self,
        progress: ProgressCallback | None,
        processed: int,
        total: int,
        stage: str,
    ) -> None:
        if total <= 0:
            return
        # Per-message stages account for ~70% of total pipeline progress.
        # We map them into [0.0, 0.7] so the conversation-level stages can
        # finish out [0.7, 1.0]. This is a UX smoothing decision, not load-bearing.
        fraction = (processed / total) * 0.7
        await _emit(
            progress, stage, fraction, f"{processed}/{total} messages"
        )

    # ---- Conversation-level topics --------------------------------------
    async def _run_conversation_topics(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None,
        stats: PipelineStats,
    ) -> None:
        await _emit(progress, "conversation_topics", 0.7, "clustering messages")
        t0 = time.perf_counter()

        # Stream cleaned text — we only need strings for BERTopic, so we
        # don't materialize Message ORM rows. The ucj_data has the text
        # too; either source works. We use the DB to be authoritative.
        rows = (
            await db.execute(
                select(
                    Message.msg_index, Message.content, Message.content_english
                )
                .where(Message.upload_id == upload_id)
                .order_by(Message.msg_index.asc())
            )
        ).all()
        if not rows:
            return

        cleaned = [self.preprocessor.clean_for_nlp(_text(r)) for r in rows]
        # BERTopic does poorly on short/empty docs and will assign them all
        # to outliers — drop them up-front and remember the indices we kept.
        keep = [(r.msg_index, txt) for r, txt in zip(rows, cleaned) if len(txt.split()) >= _MIN_PROCESSABLE_WORDS]
        if len(keep) < 10:
            # Too few documents to fit a useful topic model.
            logger.info("Skipping BERTopic: only %d processable messages", len(keep))
            return

        kept_indices = [m for m, _ in keep]
        kept_texts = [t for _, t in keep]

        msg_idx_to_label = await asyncio.to_thread(
            self.topics.fit_conversation, upload_id, kept_texts
        )
        # Note: msg_idx_to_label uses positions into kept_texts (0..len-1),
        # NOT the original msg_index. Map back here.
        positional_to_msg_index = {pos: orig for pos, orig in enumerate(kept_indices)}
        labelled: dict[int, str] = {
            positional_to_msg_index[pos]: lbl
            for pos, lbl in msg_idx_to_label.items()
        }

        topic_summary = self.topics.get_topic_summary(upload_id)

        # Persist into ChatUpload.ucj_data['ai_analysis']['topic_clusters'].
        # We re-fetch the upload (the earlier reference may be stale across
        # commits) and use a JSONB merge — overwrite the topic block only.
        upload = await db.get(ChatUpload, upload_id)
        assert upload is not None
        ucj_data = dict(upload.ucj_data or {})
        ai_analysis = dict(ucj_data.get("ai_analysis") or {})
        ai_analysis["topic_clusters"] = [
            {
                "topic_id": t.topic_id,
                "label": t.label,
                "keywords": t.keywords,
                "size": t.size,
            }
            for t in topic_summary
        ]
        ai_analysis["topic_assignments"] = labelled
        ucj_data["ai_analysis"] = ai_analysis
        upload.ucj_data = ucj_data
        await db.commit()

        elapsed = time.perf_counter() - t0
        stats.stages.append(
            StageTiming(stage="conversation_topics", seconds=elapsed, rows=len(kept_texts))
        )
        await _emit(progress, "conversation_topics", 0.85, f"{len(topic_summary)} topics")

    # ---- Conversation-level entities ------------------------------------
    async def _run_conversation_entities(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None,
        stats: PipelineStats,
    ) -> None:
        await _emit(progress, "entities", 0.85, "extracting entities")
        t0 = time.perf_counter()

        rows = (
            await db.execute(
                select(Message.content, Message.content_english)
                .where(Message.upload_id == upload_id)
                .order_by(Message.msg_index.asc())
            )
        ).all()
        if not rows:
            return

        texts = [t for t in (_text(r) for r in rows) if t]
        per_msg = await asyncio.to_thread(self.entities.extract_batch, texts)
        aggregated = self.entities.aggregate_for_conversation(per_msg)

        upload = await db.get(ChatUpload, upload_id)
        assert upload is not None
        ucj_data = dict(upload.ucj_data or {})
        ai_analysis = dict(ucj_data.get("ai_analysis") or {})
        ai_analysis["entities"] = {
            "persons": aggregated.persons,
            "places": aggregated.places,
            "dates": aggregated.dates,
            "events": aggregated.events,
        }
        ucj_data["ai_analysis"] = ai_analysis
        upload.ucj_data = ucj_data
        await db.commit()

        elapsed = time.perf_counter() - t0
        stats.stages.append(
            StageTiming(stage="entities", seconds=elapsed, rows=len(texts))
        )
        await _emit(progress, "entities", 0.95, "entities ready")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_process(msg: Message) -> bool:
    """Skip rules per spec: deleted, media-only, very short, system messages.

    We check the *English* form when present so a 100-character Hindi
    message that translates to a 5-character "ok" still gets correctly
    skipped as too-short for analysis."""
    if msg.is_deleted:
        return False
    if msg.msg_type in {"deleted", "system"}:
        return False
    text = _text(msg)
    if not text or not text.strip():
        return False
    if msg.has_media and len(text.strip()) < _MIN_PROCESSABLE_CHARS:
        return False
    if msg.word_count < _MIN_PROCESSABLE_WORDS:
        return False
    if len(text) < _MIN_PROCESSABLE_CHARS:
        return False
    return True


def _text(row: object) -> str:
    """Return the analysis text for a Message-shaped row.

    Reads `content_english` when populated by the language normalization
    layer, falls back to `content` otherwise. Accepts either an ORM
    Message instance or a SQLAlchemy Row that has both columns selected
    — both expose attribute access. Empty string when neither is set."""
    english = getattr(row, "content_english", None)
    if english:
        return english
    original = getattr(row, "content", None)
    return original or ""


async def _emit(
    progress: ProgressCallback | None, stage: str, fraction: float, detail: str
) -> None:
    """Safely call an optional progress callback. Errors in the callback
    must not abort the pipeline (UI socket can drop without breaking analysis)."""
    if progress is None:
        return
    try:
        await progress(stage, fraction, detail)
    except Exception:
        logger.warning("Progress callback raised; ignoring", exc_info=True)
