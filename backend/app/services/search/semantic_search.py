"""
Semantic search over conversation-window embeddings.

Background:
    Older versions stored a vector per message and searched messages
    directly. We now embed CHUNKS (groups of ~15 messages, split on long
    time gaps — see `app/services/embeddings/chunker.py`) and the chunks
    own the vector. Retrieval quality is typically better on chat data
    because a single "lol" message means nothing in isolation, but the
    surrounding window does.

Result shape stays stable on purpose:
    Routes still return `SearchResult(message=…, similarity=…, context=…)`.
    What changes internally:
        - The HNSW scan runs on `message_chunks.embedding` (vector(1536)).
        - For each chunk hit we pick an "anchor" message (the one nearest
          the chunk's temporal center) and expose it as `result.message`.
        - The `context` window is built from the chunk's other messages
          rather than from a fixed ±N neighbor window in `messages`.

Filters:
    Sender / date / emotion / sentiment filters need per-message data,
    so they're applied AFTER the chunk hit by intersecting with the
    chunk's underlying messages. We over-fetch (top_k * 3) to absorb
    that selectivity.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessageChunk
from app.schemas.message import MessageRead
from app.schemas.search import (
    ContextWindow,
    SearchFilters,
    SearchResult,
)
from app.services.embeddings.generator import (
    EmbeddingGenerator,
    get_embedding_generator,
)

logger = logging.getLogger(__name__)


_DEFAULT_MIN_SIMILARITY = 0.3
_DEFAULT_TOP_K = 20
_DEFAULT_CONTEXT_WINDOW = 2


class SemanticSearchService:
    """Stateless service. Holds a reference to the embedding generator;
    DB sessions are passed in per-call so callers control transactions."""

    def __init__(self, generator: EmbeddingGenerator | None = None) -> None:
        self.generator = generator or get_embedding_generator()

    # ---- Main entry point -----------------------------------------------
    async def search(
        self,
        query: str,
        upload_id: UUID,
        db: AsyncSession,
        top_k: int = _DEFAULT_TOP_K,
        filters: SearchFilters | None = None,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,  # kept for signature compat
    ) -> list[SearchResult]:
        if not query or not query.strip():
            return []

        filters = filters or SearchFilters()
        min_similarity = filters.min_similarity

        query_vec = await self.generator.generate(query)

        # Over-fetch so post-filters don't leave us empty.
        fetch_k = max(top_k * 3, top_k + 20)

        chunk_hits = await self._vector_search_chunks(
            db,
            upload_id=upload_id,
            query_vec=query_vec,
            limit=fetch_k,
        )
        chunk_hits = [(c, s) for c, s in chunk_hits if s >= min_similarity]
        if not chunk_hits:
            return []

        # Materialize the messages belonging to each candidate chunk in one
        # IN-list query. We pass the filter set so per-message filters
        # (sender / date / emotion) can prune at the SQL level.
        chunks_only = [c for c, _ in chunk_hits]
        msgs_by_chunk = await self._fetch_messages_for_chunks(
            db, upload_id, chunks_only, filters
        )

        results: list[SearchResult] = []
        for chunk, similarity in chunk_hits:
            msgs = msgs_by_chunk.get(chunk.id, [])
            if not msgs:
                continue  # filtered out at message level
            anchor, context = _pick_anchor_and_context(chunk, msgs)
            results.append(
                SearchResult(
                    message=MessageRead.model_validate(anchor),
                    similarity=similarity,
                    context=context,
                )
            )
            if len(results) >= top_k:
                break
        return results

    # ---- Find similar to a known message --------------------------------
    async def find_similar_to_message(
        self,
        message_id: UUID,
        upload_id: UUID,
        db: AsyncSession,
        top_k: int = 10,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,  # kept for compat
    ) -> list[SearchResult]:
        """Find chunks whose embedding is closest to the chunk containing
        `message_id`. Returns anchor messages from those chunks."""
        # Find the chunk containing this message.
        source = await db.get(Message, message_id)
        if source is None:
            return []

        chunk_stmt = (
            select(MessageChunk)
            .where(MessageChunk.upload_id == upload_id)
            .where(MessageChunk.start_msg_index <= source.msg_index)
            .where(MessageChunk.end_msg_index >= source.msg_index)
            .where(MessageChunk.embedding.is_not(None))
            .limit(1)
        )
        source_chunk = (await db.execute(chunk_stmt)).scalars().first()
        if source_chunk is None or source_chunk.embedding is None:
            return []

        chunk_hits = await self._vector_search_chunks(
            db,
            upload_id=upload_id,
            query_vec=list(source_chunk.embedding),
            limit=top_k + 5,  # +N to drop overlap chunks that contain the source
        )
        # Drop the source chunk itself from results.
        chunk_hits = [(c, s) for c, s in chunk_hits if c.id != source_chunk.id]
        chunk_hits = chunk_hits[:top_k]
        if not chunk_hits:
            return []

        msgs_by_chunk = await self._fetch_messages_for_chunks(
            db, upload_id, [c for c, _ in chunk_hits], SearchFilters(min_similarity=0.0)
        )

        results: list[SearchResult] = []
        for chunk, similarity in chunk_hits:
            msgs = msgs_by_chunk.get(chunk.id, [])
            if not msgs:
                continue
            anchor, context = _pick_anchor_and_context(chunk, msgs)
            results.append(
                SearchResult(
                    message=MessageRead.model_validate(anchor),
                    similarity=similarity,
                    context=context,
                )
            )
        return results

    # ---- Vector search core ---------------------------------------------
    async def _vector_search_chunks(
        self,
        db: AsyncSession,
        upload_id: UUID,
        query_vec: list[float],
        limit: int,
    ) -> list[tuple[MessageChunk, float]]:
        """HNSW scan on message_chunks. Returns (chunk, similarity) pairs."""
        distance = MessageChunk.embedding.cosine_distance(query_vec)
        similarity = (1.0 - distance).label("similarity")

        stmt = (
            select(MessageChunk, similarity)
            .where(MessageChunk.upload_id == upload_id)
            .where(MessageChunk.embedding.is_not(None))
            .order_by(distance.asc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [(row.MessageChunk, float(row.similarity)) for row in rows]

    # ---- Materialize chunk → messages -----------------------------------
    async def _fetch_messages_for_chunks(
        self,
        db: AsyncSession,
        upload_id: UUID,
        chunks: Iterable[MessageChunk],
        filters: SearchFilters,
    ) -> dict[UUID, list[Message]]:
        """For each chunk, fetch its messages in msg_index order, applying
        per-message filters at the SQL level."""
        chunk_list = list(chunks)
        if not chunk_list:
            return {}

        # Build a single (msg_index BETWEEN start AND end) OR ... predicate.
        # For typical top_k (~20-60 chunks) this is small enough; if it ever
        # gets huge we can switch to a unified [min_start, max_end] range
        # and partition in Python.
        from sqlalchemy import and_, or_

        clauses = [
            and_(
                Message.msg_index >= c.start_msg_index,
                Message.msg_index <= c.end_msg_index,
            )
            for c in chunk_list
        ]
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .where(or_(*clauses))
        )
        if filters.sender:
            stmt = stmt.where(Message.sender == filters.sender)
        if filters.date_from:
            stmt = stmt.where(Message.timestamp >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(Message.timestamp <= filters.date_to)
        if filters.emotion_label:
            stmt = stmt.where(Message.emotion_label == filters.emotion_label)
        if filters.sentiment_label:
            stmt = stmt.where(Message.sentiment_label == filters.sentiment_label)
        if filters.msg_type:
            stmt = stmt.where(Message.msg_type == filters.msg_type)
        stmt = stmt.order_by(Message.msg_index.asc())

        rows = (await db.execute(stmt)).scalars().all()

        # Bin each message into the chunk(s) it falls within. Chunks can
        # overlap (the chunker's 2-msg carry-over), so a row may end up in
        # two bins — that's fine; downstream picks one anchor per chunk.
        by_chunk: dict[UUID, list[Message]] = {c.id: [] for c in chunk_list}
        for m in rows:
            for c in chunk_list:
                if c.start_msg_index <= m.msg_index <= c.end_msg_index:
                    by_chunk[c.id].append(m)
        return by_chunk


# ---------------------------------------------------------------------------
# Anchor + context helpers
# ---------------------------------------------------------------------------


def _pick_anchor_and_context(
    chunk: MessageChunk, msgs: list[Message]
) -> tuple[Message, ContextWindow]:
    """Pick the chunk's centermost meaningful message as the anchor.
    Everything else in the chunk becomes the context window."""
    if not msgs:
        # Defensive — caller should have filtered empties already.
        raise ValueError("Cannot build anchor from empty msgs list")

    # Start from the median position and walk outward to the first message
    # with non-empty content. This keeps "lol" / "ok" out of the anchor
    # slot when the chunk has a meatier sibling nearby.
    median = len(msgs) // 2
    anchor_pos = median
    for offset in range(len(msgs)):
        for candidate in (median + offset, median - offset):
            if 0 <= candidate < len(msgs):
                text = (msgs[candidate].content_english or msgs[candidate].content or "").strip()
                if len(text) >= 4:
                    anchor_pos = candidate
                    break
        else:
            continue
        break

    anchor = msgs[anchor_pos]
    before = [MessageRead.model_validate(m) for m in msgs[:anchor_pos]]
    after = [MessageRead.model_validate(m) for m in msgs[anchor_pos + 1 :]]
    return anchor, ContextWindow(before=before, after=after)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_singleton: SemanticSearchService | None = None


def get_semantic_search() -> SemanticSearchService:
    global _singleton
    if _singleton is None:
        _singleton = SemanticSearchService()
    return _singleton
