"""
Embedding indexer.

Glues the EmbeddingGenerator to the database. The flow:
    1. Fetch messages without an embedding for the given upload.
    2. Format each message via EmbeddingGenerator.preprocess_for_embedding,
       passing the previous message in for short-message context.
    3. Generate vectors in batches.
    4. Bulk-update the messages table.
    5. Advance ChatUpload.status to `done`.

The indexer is idempotent — re-running it on a partially-indexed upload
only touches messages where `embedding IS NULL`. So a crash mid-batch
just means the worker resumes from the same point on retry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatUpload, Message, ProcessingStatus
from app.services.embeddings.generator import (
    EmbeddingCost,
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


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------


# Page size for the DB-streaming loop. The OpenAI endpoint accepts up to
# 2048 inputs but we go smaller per the comment in generator.py — failure
# granularity beats raw throughput on chats this size.
_PAGE_SIZE = 200

# Inner generator batch — passed to generate_batch. Smaller than _PAGE_SIZE
# means we make multiple HTTP calls per page; we keep them equal so each
# DB page maps to one API call.
_API_BATCH_SIZE = 100


class EmbeddingIndexer:
    """Async embedding indexer. Construct per upload run; the generator
    singleton is shared across runs so model loads / API clients are reused."""

    def __init__(
        self,
        generator: EmbeddingGenerator | None = None,
        page_size: int = _PAGE_SIZE,
        api_batch_size: int = _API_BATCH_SIZE,
    ) -> None:
        self.generator = generator or get_embedding_generator()
        self.page_size = page_size
        self.api_batch_size = api_batch_size

    async def index_upload(
        self,
        upload_id: UUID,
        db: AsyncSession,
        progress: ProgressCallback | None = None,
    ) -> IndexingResult:
        """Embed every un-embedded message for `upload_id`."""

        started = time.perf_counter()

        upload = await db.get(ChatUpload, upload_id)
        if upload is None:
            raise ValueError(f"ChatUpload {upload_id} not found")

        # The pipeline expects to receive an upload in the `embedding` state.
        # If we get here from a fresh re-run we may also be in `done` — that's
        # fine, treat as a no-op resume. Failed/pending are not.
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

        # Total messages remaining to embed (for progress %).
        total_remaining = await db.scalar(
            select(func.count(Message.id))
            .where(Message.upload_id == upload_id)
            .where(Message.embedding.is_(None))
        )
        total_remaining = int(total_remaining or 0)
        logger.info(
            "Embedding indexer starting upload_id=%s remaining=%d",
            upload_id,
            total_remaining,
        )
        await _emit(progress, "embedding_start", 0.0, f"{total_remaining} to embed")

        indexed = 0
        skipped = 0
        total_tokens = 0
        total_usd = 0.0
        provider = self.generator.provider  # locks in the backend choice

        try:
            last_index = -1
            while True:
                page = await self._fetch_page(upload_id, db, last_index)
                if not page:
                    break
                last_index = page[-1].msg_index

                # Build the text-to-embed for each message; preprocess_for_embedding
                # returns None for skip-candidates. We pass the previous DB
                # message as context for very short messages.
                prev_messages = await self._fetch_prev_messages(
                    upload_id, db, [m.msg_index for m in page]
                )

                texts: list[str] = []
                indices_to_embed: list[int] = []  # positions within `page`
                for i, msg in enumerate(page):
                    formatted = self.generator.preprocess_for_embedding(
                        msg, prev_message=prev_messages.get(msg.msg_index)
                    )
                    if formatted is None:
                        skipped += 1
                        continue
                    texts.append(formatted)
                    indices_to_embed.append(i)

                if not texts:
                    # Whole page was un-embeddable. Mark progress and move on.
                    await _emit_progress(
                        progress, indexed + skipped, total_remaining
                    )
                    continue

                vectors, cost = await self.generator.generate_batch(
                    texts, batch_size=self.api_batch_size
                )
                total_tokens += cost.input_tokens
                total_usd += cost.estimated_usd

                # Bulk update — one parameter dict per row.
                update_rows: list[dict] = []
                for offset, page_idx in enumerate(indices_to_embed):
                    update_rows.append(
                        {
                            "id": page[page_idx].id,
                            "embedding": vectors[offset],
                        }
                    )
                if update_rows:
                    await db.execute(update(Message), update_rows)
                    await db.commit()
                    indexed += len(update_rows)

                await _emit_progress(progress, indexed + skipped, total_remaining)

            # All pages processed → advance status to done.
            upload = await db.get(ChatUpload, upload_id)
            assert upload is not None
            upload.status = ProcessingStatus.done
            await db.commit()

            elapsed = time.perf_counter() - started
            logger.info(
                "Embedding indexer finished upload_id=%s indexed=%d skipped=%d tokens=%d cost=$%.4f elapsed=%.2fs",
                upload_id,
                indexed,
                skipped,
                total_tokens,
                total_usd,
                elapsed,
            )
            await _emit(progress, "embedding_done", 1.0, f"{indexed} indexed")

            return IndexingResult(
                upload_id=upload_id,
                indexed_count=indexed,
                skipped_count=skipped,
                total_tokens_used=total_tokens,
                estimated_cost_usd=total_usd,
                provider=provider,
                elapsed_seconds=elapsed,
            )

        except Exception as e:
            logger.exception("Embedding indexer failed for upload_id=%s", upload_id)
            await db.rollback()
            upload = await db.get(ChatUpload, upload_id)
            if upload is not None:
                # Embedding failure must not block the dashboard. NLP already
                # ran successfully by the time we got here, so the sentiment /
                # emotion / mood views are good to render. Mark status=done
                # but keep `processing_error` populated so the search panel
                # can detect the missing embeddings and show a focused state.
                upload.status = ProcessingStatus.done
                upload.processing_error = f"Embedding indexer: {e}"
                await db.commit()
            await _emit(progress, "embedding_failed", 1.0, str(e))
            raise

    # ---- DB helpers -----------------------------------------------------
    async def _fetch_page(
        self, upload_id: UUID, db: AsyncSession, after_msg_index: int
    ) -> Sequence[Message]:
        """Next page of un-embedded messages, in chronological order."""
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .where(Message.msg_index > after_msg_index)
            .where(Message.embedding.is_(None))
            .order_by(Message.msg_index.asc())
            .limit(self.page_size)
        )
        return (await db.execute(stmt)).scalars().all()

    async def _fetch_prev_messages(
        self, upload_id: UUID, db: AsyncSession, msg_indices: list[int]
    ) -> dict[int, Message]:
        """Map current msg_index → previous Message, for the messages in this page.

        We need the previous-message context only for very short rows; rather
        than executing N queries, we fetch all rows with msg_index in
        {idx-1 for idx in page} in one shot."""
        wanted = [i - 1 for i in msg_indices if i > 0]
        if not wanted:
            return {}
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .where(Message.msg_index.in_(wanted))
        )
        rows = (await db.execute(stmt)).scalars().all()
        by_index = {m.msg_index: m for m in rows}
        return {i: by_index[i - 1] for i in msg_indices if (i - 1) in by_index}


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
    await _emit(
        progress, "embedding", fraction, f"{processed}/{total}"
    )
