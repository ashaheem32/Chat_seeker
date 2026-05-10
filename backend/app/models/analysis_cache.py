"""
AnalysisCache model.

Pre-computed dashboard aggregates (sender breakdowns, hourly histograms,
sentiment timelines, ...) keyed by (upload_id, stat_type).

Why a cache table instead of recomputing on every request:
- Many dashboard widgets need the same aggregate; without caching every panel
  would re-scan the messages table.
- Aggregates are expensive enough that even with indexes a 50k-message chat
  takes hundreds of ms.
- The cache is invalidated on re-analysis, not on every message write, so
  the ratio of reads to writes is heavily skewed toward reads.

`stat_type` is a free-form string ("sender_breakdown", "hourly_histogram",
"sentiment_timeline", etc.) so we can add new aggregate kinds without a
schema change. `expires_at` is a soft TTL — readers should treat NULL as
"never expires" and a past timestamp as "stale, please regenerate".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.chat_upload import ChatUpload


class AnalysisCache(Base):
    __tablename__ = "analysis_cache"
    __table_args__ = (
        # Lookup pattern: "give me stat X for upload Y". The unique constraint
        # is also the natural lookup index — Postgres uses it automatically.
        UniqueConstraint("upload_id", "stat_type", name="uq_analysis_cache_upload_stat"),
        # Janitor query: "find all stale cache entries to refresh".
        Index("ix_analysis_cache_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    upload_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )

    # e.g. "sender_breakdown", "hourly_histogram", "sentiment_timeline",
    # "topic_clusters", "summary_stats". Free-form on purpose.
    stat_type: Mapped[str] = mapped_column(String(64), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # NULL => never expires. Past timestamp => stale, regenerate on next read.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    upload: Mapped["ChatUpload"] = relationship(back_populates="analysis_caches")
