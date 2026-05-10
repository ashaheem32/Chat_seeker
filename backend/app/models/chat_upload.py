"""
ChatUpload model.

One row per uploaded chat file. Holds the UCJ `meta` block (participants,
stats, ai_analysis, ...) in JSONB. Messages are NOT stored here — they're
exploded into the `messages` table for query performance.

Status transitions are linear:
    pending -> processing -> nlp_processing -> embedding -> done
                               `-> failed (terminal, from any state)
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.analysis_cache import AnalysisCache
    from app.models.message import Message
    from app.models.user import User


class SourcePlatform(str, enum.Enum):
    """Originating chat platform. `unknown` is the safe fallback during ingest."""

    whatsapp = "whatsapp"
    telegram = "telegram"
    instagram = "instagram"
    facebook = "facebook"
    csv = "csv"
    unknown = "unknown"


class ProcessingStatus(str, enum.Enum):
    """Lifecycle of an upload through the analysis pipeline."""

    pending = "pending"
    processing = "processing"
    nlp_processing = "nlp_processing"
    embedding = "embedding"
    done = "done"
    failed = "failed"


class ChatUpload(Base):
    __tablename__ = "chat_uploads"
    __table_args__ = (
        # Dashboard list view: "my recent uploads, newest first".
        Index("ix_chat_uploads_user_id_created_at", "user_id", "created_at"),
        # Worker poll: "find me the next pending job".
        Index("ix_chat_uploads_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)

    platform: Mapped[SourcePlatform] = mapped_column(
        SAEnum(
            SourcePlatform,
            name="source_platform",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SourcePlatform.unknown,
    )

    status: Mapped[ProcessingStatus] = mapped_column(
        SAEnum(
            ProcessingStatus,
            name="processing_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ProcessingStatus.pending,
        server_default=ProcessingStatus.pending.value,
    )

    # Full UCJ meta block (participants, stats, ai_analysis). NOT messages.
    ucj_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    total_messages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Denormalized from ucj_data for indexable filters / sorts.
    participants: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )

    date_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    date_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    span_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ---- Relationships ------------------------------------------------------
    user: Mapped["User"] = relationship(back_populates="uploads")

    # selectin keeps message hydration off the chat_upload fetch by default;
    # routes that need messages should query the messages table directly with
    # pagination. lazy="raise" forces that discipline.
    messages: Mapped[list["Message"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        lazy="raise",
        passive_deletes=True,
    )

    analysis_caches: Mapped[list["AnalysisCache"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        lazy="raise",
        passive_deletes=True,
    )
