"""
Message model.

Each row is a single chat message exploded out of UCJ. Storing messages as
rows (not as a JSONB array on ChatUpload) lets the dashboard run indexed
filters (sender, date, sentiment) and lets pgvector run HNSW similarity
search on the `embedding` column.

NLP fields (sentiment_*, emotion_*, topics, embedding) are nullable because
they're populated by the async pipeline after initial ingest.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.chat_upload import ChatUpload


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # The bread-and-butter dashboard query: a chat's timeline in order.
        Index("ix_messages_upload_id_timestamp", "upload_id", "timestamp"),
        # "Messages by this sender in this chat" — sender breakdown panels.
        Index("ix_messages_upload_id_sender", "upload_id", "sender"),
        # Original-order pagination fallback; also used to rehydrate UCJ.
        Index("ix_messages_upload_id_msg_index", "upload_id", "msg_index"),
        # Sentiment / emotion filter panels.
        Index("ix_messages_upload_id_sentiment_label", "upload_id", "sentiment_label"),
        Index("ix_messages_upload_id_emotion_label", "upload_id", "emotion_label"),
        # Note: HNSW index on `embedding` is created in the migration via raw
        # SQL — SQLAlchemy can't express vector_cosine_ops + WITH options.
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    upload_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Original ordering within the file. Stable across re-imports of the same UCJ.
    msg_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # UCJ-level message id ("msg_0", "msg_1", ...). Unique within an upload but
    # not globally; reply_to_id refers to this value.
    msg_id: Mapped[str] = mapped_column(String(128), nullable=False)

    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ---- Language normalization (Layer 4) ----------------------------------
    # `content_english` is the English-translated form of `content`, written
    # by the language normalization pipeline. Downstream NLP/embedding/search
    # all read this field with `content` as the fallback (so they keep
    # working before the normalizer finishes). For pure-English messages,
    # this column equals `content` and `was_translated` stays False.
    content_english: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The detector's classification — e.g. "english", "manglish", "hindi",
    # "hinglish", "arabic_script", "mixed_code_switched", "too_short".
    # Free-form so adding a new category in the detector doesn't require a
    # migration.
    original_language: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # True only when the normalizer actually produced a translation
    # (i.e. content_english is meaningfully different from content).
    was_translated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # text | image | video | audio | sticker | file | deleted | system | ...
    # Stored as plain text rather than an enum so adding a new platform's
    # message kind doesn't require a migration.
    msg_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="text", server_default="text"
    )
    reply_to_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ---- Lightweight metadata (computed at ingest time) ---------------------
    word_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    char_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    has_emoji: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    emojis: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    has_url: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    has_media: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ---- NLP outputs (populated by Celery worker, hence nullable) -----------
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emotion_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emotion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # pgvector. Dimension matches settings.EMBEDDING_DIMENSIONS (default 1536
    # for text-embedding-3-small). Changing the model means a migration.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSIONS), nullable=True
    )

    upload: Mapped["ChatUpload"] = relationship(back_populates="messages")
