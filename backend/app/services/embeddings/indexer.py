"""
Embedding indexer — chunk-level.

After build_chunks groups the upload's messages into conversation
windows, the indexer:
    1. Streams chunk drafts that don't yet have an embedding.
    2. Fans out the OpenAI calls under a bounded asyncio.Semaphore so
       multiple sub-batches are in flight simultaneously (~5-8x wall-
       clock speedup on big chats; tier-1 OpenAI rate limits absorb
       8x easily).
    3. Bulk-INSERTs the chunks with their embeddings into
       message_chunks.
    4. Marks the embedding stage complete; the cache coordinator flips
       ChatUpload.status to `done` once NLP also finishes.

Idempotency:
    On a re-run the indexer detects existing chunks for the upload
    (via the unique (upload_id, chunk_index) constraint) and skips them
    by re-chunking deterministically and only persisting rows where the
    chunk_index isn't already present. This matches the "WHERE column
    IS NULL" resume pattern used elsewhere in the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatUpload, Message, MessageChunk, ProcessingStatus
from app.services.embeddings.chunker import ChunkDraft, build_chunks
from app.services.embeddings.generator import (
    EmbeddingGenerator,
    EmbeddingProvider,
    get_embedding_generator,
)

logger = logging.getLogger(__name__)


ProgressCallback = Callable[[str, float, str], Awaitable[None]]


@dataclass(slots=True)
class IndexingResult:
    """Returned by EmbeddingIndexer.index_upload — surfaced through the
    Celery task for monitoring + cost tracking."""

    upload_id: UUID
    indexed_count: int
    skipped_count: int
    total_tokens_used: int
    estimated_cost_usd: float
    provider: EmbeddingProvider
    elapsed_seconds: float
    error: str | None = None


# Max in-flight embedding requests against OpenAI. text-embedding-3-small
# tier-1 RPM is ~5000 — 8 concurrent is well within headroom.
_MAX_INFLIGHT = 8

# Sub-batch size handed to a single OpenAI call. OpenAI accepts up to
# 2048 inputs; 200 gives good granularity for partial-failure retries.
_SUB_BATCH = 200


class EmbeddingIndexer:
    """Async embedding indexer. Construct per upload run; the generator
    singleton is shared so model loads / API clients are reused."""

    def __init__(
        self,
        generator: EmbeddingGenerator | None = None,
        max_inflight: int = _MAX_INFLIGHT,
        sub_batch: int = _SUB_BATCH,
    ) -> None:
        self.generator = generator or get_embedding_generator()
        self.max_inflight = max_inflight
        self.sub_batch = sub_batch

    async def index_upload(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None = None,
    ) -> IndexingResult:
        """Build chunks for `upload_id` if missing, embed them concurrently,
        write into message_chunks."""

        started = time.perf_counter()

        upload = await db.get(ChatUpload, upload_id)
        if upload is None:
            raise ValueError(f"ChatUpload {upload_id} not found")

        # Status sentinel. Accept `embedding` or `done` (re-runs); flip
        # anything else into `embedding` so observers see progress.
        if upload.status not in {
            ProcessingStatus.embedding,
            ProcessingStatus.done,
        }:
            logger.info(
                "Indexer setting upload_id=%s status=%s -> embedding",
                upload_id,
                upload.status,
            )
            upload.status = ProcessingStatus.embedding
            upload.processing_error = None
            await db.commit()

        # ---- 1. Pull all messages for the upload in msg_index order.
        # Even a 200k-message chat fits comfortably in RAM (~30MB of
        # python objects) and the alternative — paging — bakes a hard
        # boundary into the chunker that we'd then need to repair.
        messages = await self._fetch_all_messages(upload_id, db)
        if not messages:
            logger.info("Embedding indexer: upload=%s has no messages", upload_id)
            return await self._finalize(
                upload_id, db, started, indexed=0, skipped=0,
                total_tokens=0, total_usd=0.0, progress=progress,
            )

        # ---- 2. Build chunks. Idempotent: if some chunks already exist
        # for this upload (resume case), skip those chunk_indexes.
        all_chunks = build_chunks(upload_id, messages)
        existing_indexes = await self._existing_chunk_indexes(upload_id, db)
        pending = [c for c in all_chunks if c.chunk_index not in existing_indexes]
        skipped = len(all_chunks) - len(pending)

        logger.info(
            "Embedding indexer upload=%s chunks=%d already_persisted=%d to_embed=%d",
            upload_id, len(all_chunks), skipped, len(pending),
        )
        await _emit(
            progress, "embedding_start", 0.0,
            f"{len(pending)} chunks to embed",
        )

        if not pending:
            return await self._finalize(
                upload_id, db, started, indexed=0, skipped=skipped,
                total_tokens=0, total_usd=0.0, progress=progress,
            )

        # ---- 3. Embed concurrently. Build sub-batches of `sub_batch`
        # chunks each, gather under a Semaphore so at most
        # `max_inflight` OpenAI calls are in flight simultaneously.
        try:
            total_tokens, total_usd = await self._embed_and_persist(
                upload_id, db, pending, progress,
            )
        except Exception as e:
            logger.exception("Embedding indexer failed for upload_id=%s", upload_id)
            await db.rollback()

            # Flip the upload to `failed` so the frontend can surface the
            # error instead of silently rendering a search-broken dashboard.
            # Previously this path called `mark_stage_complete` and promoted
            # to `done`, which masked broken embeddings as "ready".
            upload = await db.get(ChatUpload, upload_id)
            if upload is not None:
                upload.status = ProcessingStatus.failed
                upload.processing_error = f"Embedding indexer: {e}"
                await db.commit()
            await _emit(progress, "embedding_failed", 1.0, str(e))
            raise

        return await self._finalize(
            upload_id, db, started,
            indexed=len(pending), skipped=skipped,
            total_tokens=total_tokens, total_usd=total_usd,
            progress=progress,
        )

    # ------------------------------------------------------------------
    # Embedding core
    # ------------------------------------------------------------------

    async def _embed_and_persist(
        self,
        upload_id: UUID,
        db: AsyncSession,
        pending: list[ChunkDraft],
        progress: ProgressCallback | None,
    ) -> tuple[int, float]:
        """Embed all `pending` chunks under a concurrency cap; bulk-INSERT
        each completed sub-batch. Returns (total_tokens, total_usd).
        """
        semaphore = asyncio.Semaphore(self.max_inflight)
        # Partition into sub-batches up front so each gather()
        # element is one OpenAI call.
        sub_batches: list[list[ChunkDraft]] = [
            pending[i : i + self.sub_batch]
            for i in range(0, len(pending), self.sub_batch)
        ]
        total_tokens = 0
        total_usd = 0.0
        done_count = 0

        async def run_one(
            batch: list[ChunkDraft],
        ) -> tuple[list[ChunkDraft], list[list[float]], int, float]:
            async with semaphore:
                texts = [c.content for c in batch]
                vectors, cost = await self.generator.generate_batch(
                    texts, batch_size=len(texts)
                )
                return batch, vectors, cost.input_tokens, cost.estimated_usd

        # Fire all sub-batches concurrently. We use as_completed so the
        # bulk INSERT can start as soon as the first batch finishes,
        # overlapping API + DB work.
        coros = [run_one(b) for b in sub_batches]
        for future in asyncio.as_completed(coros):
            batch, vectors, tokens, usd = await future
            total_tokens += tokens
            total_usd += usd

            rows = [
                {
                    "id": c.id,
                    "upload_id": c.upload_id,
                    "chunk_index": c.chunk_index,
                    "start_msg_index": c.start_msg_index,
                    "end_msg_index": c.end_msg_index,
                    "start_ts": c.start_ts,
                    "end_ts": c.end_ts,
                    "participants": c.participants,
                    "content": c.content,
                    "embedding": vectors[i],
                }
                for i, c in enumerate(batch)
            ]
            await db.execute(insert(MessageChunk), rows)
            await db.commit()
            done_count += len(batch)

            await _emit_progress(progress, done_count, len(pending))

        return total_tokens, total_usd

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _fetch_all_messages(
        self, upload_id: UUID, db: AsyncSession
    ) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .order_by(Message.msg_index.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def _existing_chunk_indexes(
        self, upload_id: UUID, db: AsyncSession
    ) -> set[int]:
        stmt = (
            select(MessageChunk.chunk_index)
            .where(MessageChunk.upload_id == upload_id)
        )
        return set((await db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    async def _finalize(
        self,
        upload_id: UUID,
        db: AsyncSession,
        started: float,
        *,
        indexed: int,
        skipped: int,
        total_tokens: int,
        total_usd: float,
        progress: ProgressCallback | None,
    ) -> IndexingResult:
        from app.core.cache import cache

        stages_done = await cache.mark_stage_complete(str(upload_id), "embedding")
        upload = await db.get(ChatUpload, upload_id)
        assert upload is not None
        # Respect a prior `failed` status — the NLP pipeline may have already
        # crashed and recorded the error. Overwriting to `done` here would
        # mask a broken upload as ready.
        if upload.status != ProcessingStatus.failed and (
            stages_done >= 2 or stages_done == -1
        ):
            upload.status = ProcessingStatus.done
        await db.commit()

        elapsed = time.perf_counter() - started
        logger.info(
            "Embedding indexer finished upload_id=%s indexed=%d skipped=%d "
            "tokens=%d cost=$%.4f elapsed=%.2fs",
            upload_id, indexed, skipped, total_tokens, total_usd, elapsed,
        )
        await _emit(progress, "embedding_done", 1.0, f"{indexed} chunks indexed")

        return IndexingResult(
            upload_id=upload_id,
            indexed_count=indexed,
            skipped_count=skipped,
            total_tokens_used=total_tokens,
            estimated_cost_usd=total_usd,
            provider=self.generator.provider,
            elapsed_seconds=elapsed,
        )


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def _emit(
    progress: ProgressCallback | None, stage: str, fraction: float, detail: str
) -> None:
    if progress is None:
        return
    try:
        await progress(stage, fraction, detail)
    except Exception:
        logger.warning("Embedding progress callback raised; ignoring", exc_info=True)


async def _emit_progress(
    progress: ProgressCallback | None, processed: int, total: int
) -> None:
    if progress is None or total <= 0:
        return
    fraction = min(1.0, processed / total)
    await _emit(progress, "embedding", fraction, f"{processed}/{total}")
