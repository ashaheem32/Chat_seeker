"""
Semantic search over message embeddings.

Uses pgvector's cosine-distance operator (`<=>`) — a built-in C function
backed by the HNSW index we created in migration 0001:
    CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);

`Vector.cosine_distance(...)` returns distance ∈ [0, 2]. Cosine similarity
is `1 - distance` and ranges over [-1, 1]; for normalized vectors (which
text-embedding-3-* always returns) it's [0, 1]. We surface the similarity
in that range so dashboard thresholds are intuitive.

Filters (sender, date range, emotion, ...) are applied in SQL — pushing
them into the WHERE clause is much faster than filtering in Python after
top-k retrieval. The HNSW index handles ORDER BY ... LIMIT efficiently
even when narrowing through B-tree predicates because pgvector's planner
falls back to an index scan with re-check.

Context window:
    For each hit, we optionally fetch up to N messages immediately before
    and after via msg_index. One small query per hit isn't great at high
    top_k, so we coalesce all needed indices into a single IN-list query
    and assemble the windows in Python.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message
from app.schemas.search import (
    ContextWindow,
    SearchFilters,
    SearchResult,
)
from app.schemas.message import MessageRead
from app.services.embeddings.generator import (
    EmbeddingGenerator,
    get_embedding_generator,
)

logger = logging.getLogger(__name__)


# Default similarity floor matches the spec ("similarity > 0.3"). Callers
# can override via SearchFilters.min_similarity.
_DEFAULT_MIN_SIMILARITY = 0.3
_DEFAULT_TOP_K = 20
_DEFAULT_CONTEXT_WINDOW = 2


class SemanticSearchService:
    """Stateless service. Holds a reference to the embedding generator;
    DB sessions are passed in per-call so callers control transactions."""

    def __init__(self, generator: EmbeddingGenerator | None = None) -> None:
        self.generator = generator or get_embedding_generator()

    # ---- Main entry point ------------------------------------------------
    async def search(
        self,
        query: str,
        upload_id: UUID,
        db: AsyncSession,
        top_k: int = _DEFAULT_TOP_K,
        filters: SearchFilters | None = None,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,
    ) -> list[SearchResult]:
        """Run a vector search and return top hits.

        Args:
            query: User's natural-language query. Embedded via the same
                   model used at index time so similarity is meaningful.
            upload_id: Restrict search to this chat.
            top_k: How many results to return (after filtering).
            filters: Optional SearchFilters; min_similarity defaults to 0.3.
            context_window: Number of messages to include before/after each
                            hit. Set to 0 to skip context fetch entirely.
        """
        if not query or not query.strip():
            return []

        filters = filters or SearchFilters()
        min_similarity = filters.min_similarity

        query_vec = await self.generator.generate(query)

        # Pull a wider candidate pool than top_k so post-filters don't
        # leave us empty-handed when most hits are off-topic. 3× is a
        # rough heuristic — enough to absorb typical filter selectivity
        # without measurably slowing the HNSW scan.
        fetch_k = max(top_k * 3, top_k + 20)

        hits = await self._vector_search(
            db,
            upload_id=upload_id,
            query_vec=query_vec,
            limit=fetch_k,
            filters=filters,
        )

        # Drop hits below the similarity floor (HNSW gives us nearest
        # neighbors but they can still be very far in absolute terms).
        hits = [(m, s) for m, s in hits if s >= min_similarity]
        hits = hits[:top_k]

        # Fetch context windows in one query if requested.
        contexts = (
            await self._fetch_contexts(db, upload_id, [m for m, _ in hits], context_window)
            if context_window > 0
            else {}
        )

        return [
            SearchResult(
                message=MessageRead.model_validate(m),
                similarity=score,
                context=contexts.get(m.id),
            )
            for m, score in hits
        ]

    # ---- Find similar to a known message --------------------------------
    async def find_similar_to_message(
        self,
        message_id: UUID,
        upload_id: UUID,
        db: AsyncSession,
        top_k: int = 10,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,
    ) -> list[SearchResult]:
        """Find messages whose embedding is closest to a given message.

        Uses the source message's stored vector instead of re-embedding,
        so this stays fast even on large chats. The source message itself
        is filtered out of the results.
        """
        source = await db.get(Message, message_id)
        if source is None or source.embedding is None:
            return []

        hits = await self._vector_search(
            db,
            upload_id=upload_id,
            query_vec=list(source.embedding),
            limit=top_k + 1,  # +1 because the source itself will rank #1
            filters=SearchFilters(min_similarity=0.0),
        )
        hits = [(m, s) for m, s in hits if m.id != message_id][:top_k]

        contexts = (
            await self._fetch_contexts(db, upload_id, [m for m, _ in hits], context_window)
            if context_window > 0
            else {}
        )

        return [
            SearchResult(
                message=MessageRead.model_validate(m),
                similarity=score,
                context=contexts.get(m.id),
            )
            for m, score in hits
        ]

    # ---- Vector search core ---------------------------------------------
    async def _vector_search(
        self,
        db: AsyncSession,
        upload_id: UUID,
        query_vec: list[float],
        limit: int,
        filters: SearchFilters,
    ) -> list[tuple[Message, float]]:
        """Run the actual pgvector query. Returns (message, similarity) pairs
        sorted by similarity desc."""

        # cosine_distance is provided by pgvector.sqlalchemy.Vector. similarity
        # = 1 - distance. We compute it server-side via a label so it lands
        # in the SELECT list and Postgres can ORDER BY it without recomputing.
        distance = Message.embedding.cosine_distance(query_vec)
        similarity = (1 - distance).label("similarity")

        stmt = (
            select(Message, similarity)
            .where(Message.upload_id == upload_id)
            .where(Message.embedding.is_not(None))
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

        # ORDER BY distance ASC == similarity DESC; pgvector's HNSW index
        # is keyed on distance so ascending order keeps it index-friendly.
        stmt = stmt.order_by(distance.asc()).limit(limit)

        rows = (await db.execute(stmt)).all()
        return [(row.Message, float(row.similarity)) for row in rows]

    # ---- Context windows ------------------------------------------------
    async def _fetch_contexts(
        self,
        db: AsyncSession,
        upload_id: UUID,
        hits: Iterable[Message],
        window: int,
    ) -> dict[UUID, ContextWindow]:
        """Batch-fetch the messages immediately before/after each hit.

        We collect every msg_index we need across all hits, do one IN query,
        then partition into per-hit before/after lists in Python."""
        wanted_indices: set[int] = set()
        hit_list = list(hits)
        for m in hit_list:
            for delta in range(1, window + 1):
                if m.msg_index - delta >= 0:
                    wanted_indices.add(m.msg_index - delta)
                wanted_indices.add(m.msg_index + delta)

        if not wanted_indices:
            return {}

        rows = (
            await db.execute(
                select(Message)
                .where(Message.upload_id == upload_id)
                .where(Message.msg_index.in_(wanted_indices))
            )
        ).scalars().all()

        by_index = {m.msg_index: m for m in rows}

        contexts: dict[UUID, ContextWindow] = {}
        for m in hit_list:
            before: list[MessageRead] = []
            for delta in range(window, 0, -1):
                neighbor = by_index.get(m.msg_index - delta)
                if neighbor is not None:
                    before.append(MessageRead.model_validate(neighbor))
            after: list[MessageRead] = []
            for delta in range(1, window + 1):
                neighbor = by_index.get(m.msg_index + delta)
                if neighbor is not None:
                    after.append(MessageRead.model_validate(neighbor))
            contexts[m.id] = ContextWindow(before=before, after=after)
        return contexts


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_singleton: SemanticSearchService | None = None


def get_semantic_search() -> SemanticSearchService:
    global _singleton
    if _singleton is None:
        _singleton = SemanticSearchService()
    return _singleton
