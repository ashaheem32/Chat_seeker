"""
MessageChunk model — window-level embeddings.

A chunk groups ~15 consecutive messages from an upload into a single
embedding unit. Embedding chunks instead of individual messages cuts
OpenAI calls by ~10x on a 200k-message chat and typically improves
retrieval quality, because a single short message ("lol", "yes",
"ok") carries no semantic signal in isolation.

Chunks own the embedding and the joined-text representation; the
original Message rows stay the source of truth for sender, timestamp,
NLP fields, etc. Search returns a chunk; the router expands it back
to messages via (upload_id, start_msg_index, end_msg_index).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.config import settings
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.chat_upload import ChatUpload


class MessageChunk(Base):
    __tablename__ = "message_chunks"
    __table_args__ = (
        Index(
            "ix_chunks_upload_id_chunk_index",
            "upload_id",
            "chunk_index",
            unique=True,
        ),
        Index(
            "ix_chunks_upload_id_start_msg",
            "upload_id",
            "start_msg_index",
        ),
        # HNSW index on embedding is created in migration 0003.
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    upload_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 0, 1, 2 ... within the upload. Stable for re-runs.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Inclusive bounds into Message.msg_index. Used to expand a chunk hit
    # back to the underlying messages without joining on the chunk id.
    start_msg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    end_msg_index: Mapped[int] = mapped_column(Integer, nullable=False)

    start_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Senders who appear in the chunk. Cheap pre-filter for participant-
    # scoped search (avoid pulling chunks where Alice never spoke).
    participants: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )

    # The joined text we feed to the embedding API. Format:
    #     "Alice: hi\nBob: hey\n..."
    content: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSIONS), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    upload: Mapped["ChatUpload"] = relationship()
