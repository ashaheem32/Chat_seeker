"""Pydantic schemas for the stats endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Per-participant rollup (used inside OverviewStats and on its own)
# ---------------------------------------------------------------------------


class ParticipantStats(BaseModel):
    """One row in the participant-comparison panel.

    `most_used_word` and `favorite_emoji` are nullable because tiny chats
    or media-heavy senders might not have a meaningful answer."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    message_count: int = 0
    word_count: int = 0
    emoji_count: int = 0
    avg_message_length: float = 0.0
    question_count: int = 0
    exclamation_count: int = 0
    most_used_word: str | None = None
    favorite_emoji: str | None = None


# ---------------------------------------------------------------------------
# Fun-fact items (ad-hoc; rendered as small tiles on the dashboard)
# ---------------------------------------------------------------------------


class MessageReference(BaseModel):
    """A pointer to a single message — used for "longest message" /
    "most-replied-to" / "first message" etc.

    `msg_id` is the UCJ-level token (e.g. "msg_42") so the frontend can
    deep-link directly into the timeline view; `id` is the DB UUID for
    cases where the consumer prefers stable internal references.
    """

    id: UUID
    msg_id: str
    sender: str
    timestamp: datetime
    content_preview: str = Field(
        ..., description="First ~280 chars of the message content."
    )
    char_count: int = 0
    reply_count: int | None = Field(
        default=None, description="For 'most replied to' — how many replies it received."
    )


# ---------------------------------------------------------------------------
# OverviewStats — the main payload returned by /stats/{id}/overview
# ---------------------------------------------------------------------------


class WhoTextsFirstSlice(BaseModel):
    """Single slice of the who-texts-first donut. `share` is in [0, 1]
    so the frontend can render percentage labels without re-deriving."""

    sender: str
    days_started: int
    share: float = Field(..., ge=0.0, le=1.0)


class HourlyHistogramBucket(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    count: int = 0


class OverviewStats(BaseModel):
    """Aggregate statistics rendered by the dashboard's StatsOverview module.

    Naming convention: snake_case throughout (matches every other backend
    payload). The frontend uses these names verbatim — see
    `frontend/lib/types.ts`."""

    upload_id: UUID

    # ---- Hero metrics --------------------------------------------------
    total_messages: int = 0
    total_words: int = 0
    total_emojis: int = 0
    total_characters: int = 0

    # ---- Date / activity windows ---------------------------------------
    conversation_days: int = Field(
        default=0,
        description=(
            "Calendar span between first and last message, inclusive. "
            "Same value as ChatUpload.span_days."
        ),
    )
    active_days: int = Field(
        default=0, description="Number of distinct days with at least one message."
    )
    longest_streak: int = Field(
        default=0, description="Longest run of consecutive days with messages."
    )
    longest_silence: int = Field(
        default=0, description="Longest gap (in days) between consecutive active days."
    )
    avg_messages_per_day: float = 0.0

    # ---- Behavioral ----------------------------------------------------
    avg_response_time_minutes: float | None = Field(
        default=None,
        description=(
            "Median minutes between consecutive messages where the sender "
            "changes. Excludes gaps over 24h to avoid overnight noise."
        ),
    )
    who_texts_first: list[WhoTextsFirstSlice] = Field(default_factory=list)

    # ---- Time patterns -------------------------------------------------
    busiest_hour: int | None = Field(
        default=None,
        ge=0,
        le=23,
        description="Hour-of-day (0-23) with the most messages, in UTC.",
    )
    busiest_day_of_week: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="ISO day of week, 0=Monday … 6=Sunday.",
    )
    most_active_month: str | None = Field(
        default=None, description="ISO month string like '2024-08'."
    )
    hourly_histogram: list[HourlyHistogramBucket] = Field(
        default_factory=list,
        description="24-bucket histogram for the busiest-hour sparkline.",
    )

    # ---- Per-participant ----------------------------------------------
    per_participant: list[ParticipantStats] = Field(default_factory=list)

    # ---- Fun facts -----------------------------------------------------
    longest_message: MessageReference | None = None
    most_replied_to: MessageReference | None = None
    first_message: MessageReference | None = None
    most_used_word_overall: str | None = None


# ---------------------------------------------------------------------------
# Per-participant deep dive — /stats/{id}/participant/{sender}
# ---------------------------------------------------------------------------


class TopItem(BaseModel):
    value: str
    count: int


class DetailedParticipantStats(BaseModel):
    """Returned from GET /stats/{id}/participant/{sender}.

    Extends the basic ParticipantStats with sentiment averages, time-of-day
    activity, top-N word and emoji lists, and first/last message references
    so the UI can render a full "participant detail" pane."""

    upload_id: UUID
    sender: str

    message_count: int = 0
    word_count: int = 0
    emoji_count: int = 0
    avg_message_length: float = 0.0
    question_count: int = 0
    exclamation_count: int = 0

    avg_sentiment: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Mean of Message.sentiment_score over this sender's messages.",
    )
    sentiment_share: dict[str, int] = Field(
        default_factory=dict,
        description='{"positive": N, "neutral": N, "negative": N}',
    )
    emotion_share: dict[str, int] = Field(default_factory=dict)

    hourly_histogram: list[HourlyHistogramBucket] = Field(default_factory=list)
    top_words: list[TopItem] = Field(default_factory=list)
    top_emojis: list[TopItem] = Field(default_factory=list)

    first_message: MessageReference | None = None
    last_message: MessageReference | None = None
