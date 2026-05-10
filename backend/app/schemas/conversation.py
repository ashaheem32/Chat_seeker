"""Pydantic schemas for the /conversations endpoints.

Why a separate "conversation" vocabulary instead of reusing
ChatUpload's wire shape:
    The DB-side ChatUpload row exposes more internal state than the
    list view needs (raw enum values, user_id, processing_error,
    redundant ucj_data dump). The conversation schemas are the curated
    public-facing view: stable field names, a friendlier `name`
    derived from participant info, a 6-value `processing_status`
    literal that's frontend-friendly, and a `preview` bolted on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# Public-facing status. Different from ProcessingStatus on the DB side:
#   - 'normalizing' replaces the more generic 'processing' (Layer 4
#     language normalization is the first post-persistence stage)
#   - 'nlp' / 'error' are the friendlier names the frontend shows
ConversationStatus = Literal[
    "queued",
    "normalizing",
    "nlp",
    "embedding",
    "done",
    "error",
]


class ConversationDateRange(BaseModel):
    """Inclusive date span over the messages in a conversation."""

    start: datetime
    end: datetime
    span_days: int


class ConversationListItem(BaseModel):
    """One row in the /conversations list response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str = Field(
        ...,
        description=(
            "Auto-generated display name. Two participants render as "
            "'Alice & Bob'; larger groups collapse to 'Alice, Bob & "
            "N others'. Falls back to the filename when participants "
            "aren't known yet."
        ),
    )
    platform: str = Field(
        ...,
        description=(
            "WhatsApp / Telegram / Instagram / Facebook / CSV / Unknown. "
            "Lowercased values straight from SourcePlatform."
        ),
    )
    participants: list[str] = Field(default_factory=list)
    total_messages: int = 0
    date_range: ConversationDateRange | None = Field(
        default=None,
        description="None when the chat is empty or still being parsed.",
    )
    processing_status: ConversationStatus
    job_id: str | None = Field(
        default=None,
        description=(
            "Celery task id of the in-flight pipeline stage. None when "
            "the upload predates job-id capture or has reached `done` / "
            "`error`. Today this is always None — clients should poll "
            "the /status endpoint instead."
        ),
    )
    created_at: datetime
    preview: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 3 most recent message text snippets, oldest first, "
            "truncated to 200 chars. Skips deleted / system rows. "
            "Empty when the chat has no readable messages."
        ),
    )


class ConversationListResponse(BaseModel):
    """Paginated wrapper for the /conversations list."""

    items: list[ConversationListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class ConversationDetail(ConversationListItem):
    """Single-conversation read. Adds the full UCJ meta block.

    `meta` mirrors the JSONB stored on ChatUpload.ucj_data — it has
    the per-participant stats, emoji counts, word totals, AI-analysis
    block, and any platform-specific extras the parser passed
    through. We surface it as a typed-loose dict so the frontend can
    render new keys without forcing a schema migration here.
    """

    meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The full UCJ meta block (participants, stats, ai_analysis, "
            "...). Same shape as ucj.ChatMeta but JSON-passthrough so "
            "platform extras survive."
        ),
    )


class ConversationStatusResponse(BaseModel):
    """Returned from /conversations/{id}/status — the polling endpoint
    the frontend hits to drive the post-upload progress bar."""

    conversation_id: UUID
    processing_status: ConversationStatus
    progress: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Pipeline progress as 0–100. Derived from the upload's "
            "current stage (queued=5, normalizing=25, nlp=55, "
            "embedding=85, done=100) plus any in-flight progress event "
            "from the upload broker — so a single request reflects "
            "both coarse stage and fine-grained sub-stage."
        ),
    )
    stage_detail: str = Field(
        default="",
        description="Human-readable detail (e.g. 'analyzing 12,300 / 50,000').",
    )
    error: str | None = Field(
        default=None,
        description="Populated only when processing_status == 'error'.",
    )
    job_id: str | None = None
    updated_at: datetime
