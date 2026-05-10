"""Pydantic schemas for the emotion / sentiment endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Time-bucket granularity for the sentiment timeline.
Granularity = Literal["day", "week", "month"]

# The seven emotion classes our NLP pipeline emits, in the order the
# frontend renders them in the legend. Keep in sync with
# `app/services/nlp/emotion._VALID_LABELS`.
EmotionLabel = Literal[
    "joy", "love", "sadness", "anger", "fear", "surprise", "disgust"
]


# ---------------------------------------------------------------------------
# Per-message sample (used inside peaks + per-emotion examples)
# ---------------------------------------------------------------------------


class SampleMessage(BaseModel):
    """Lightweight message reference used when surfacing examples.

    Different from `MessageReference` (in stats.py) in that it always carries
    the analyzed sentiment + emotion, since callers always want them when
    exploring emotional context."""

    model_config = ConfigDict(from_attributes=True)

    msg_id: str
    sender: str
    timestamp: datetime
    content_preview: str = Field(..., description="First ~280 chars of content.")
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    emotion_label: EmotionLabel | None = None
    emotion_score: float | None = None


# ---------------------------------------------------------------------------
# Sentiment timeline (Section 1 of the dashboard module)
# ---------------------------------------------------------------------------


class SenderSentimentSlice(BaseModel):
    """Per-sender values inside one timeline bucket."""

    sender: str
    avg_sentiment: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Mean sentiment_score for this sender in this bucket.",
    )
    message_count: int = 0


class SentimentTimelinePoint(BaseModel):
    """One bucket on the sentiment timeline."""

    date: date
    avg_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    message_count: int = 0
    dominant_emotion: EmotionLabel | None = None
    per_sender: list[SenderSentimentSlice] = Field(default_factory=list)


class SentimentTimeline(BaseModel):
    """Wrapper that includes the participant list (so the frontend can map
    senders to deterministic colors without re-deriving from the points)."""

    upload_id: UUID
    granularity: Granularity
    senders: list[str]
    points: list[SentimentTimelinePoint]


# ---------------------------------------------------------------------------
# Emotion distribution (Section 2)
# ---------------------------------------------------------------------------


class EmotionShare(BaseModel):
    """Per-emotion counts + share. Share is normalized to [0, 1] across the
    seven labels (NULL emotions excluded), so the seven values sum to 1."""

    emotion: EmotionLabel
    count: int = 0
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_messages: list[SampleMessage] = Field(
        default_factory=list,
        description="Up to 2 representative messages, ranked by emotion_score.",
    )


class ParticipantEmotionDistribution(BaseModel):
    """Distribution + samples for one participant. `total_classified` is the
    denominator of the shares — useful when the UI wants to display
    'X messages classified across 7 emotions'."""

    sender: str
    total_classified: int = 0
    distribution: list[EmotionShare] = Field(default_factory=list)


class EmotionDistribution(BaseModel):
    """Overall (chat-wide) emotion shares + sample messages per emotion."""

    upload_id: UUID
    total_classified: int = 0
    distribution: list[EmotionShare] = Field(default_factory=list)


class EmotionByParticipant(BaseModel):
    """Per-participant emotion distribution. The frontend renders one
    radial / donut chart per item in `participants`."""

    upload_id: UUID
    participants: list[ParticipantEmotionDistribution] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Emotional peaks (Section 4)
# ---------------------------------------------------------------------------


PeakType = Literal["peak", "valley"]


class EmotionalPeak(BaseModel):
    """A run of days that registered as significantly happier (peak) or
    harder (valley) than the chat's baseline.

    `score` is the bucket's avg_sentiment; the consumer can rank/colour off
    that value directly. `top_messages` are sentiment-extreme exemplars
    pulled from the bucket."""

    type: PeakType
    bucket_start: date
    bucket_end: date
    score: float = Field(..., ge=-1.0, le=1.0)
    message_count: int = 0
    dominant_emotion: EmotionLabel | None = None
    top_messages: list[SampleMessage] = Field(default_factory=list)


class EmotionalPeaks(BaseModel):
    upload_id: UUID
    peaks: list[EmotionalPeak] = Field(
        default_factory=list, description="Up to 5 highest-sentiment buckets."
    )
    valleys: list[EmotionalPeak] = Field(
        default_factory=list, description="Up to 5 lowest-sentiment buckets."
    )


# ---------------------------------------------------------------------------
# Mood calendar (Section 3)
# ---------------------------------------------------------------------------


class MoodCalendarDay(BaseModel):
    date: date
    avg_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    dominant_emotion: EmotionLabel | None = None
    message_count: int = 0


class MoodCalendar(BaseModel):
    upload_id: UUID
    days: list[MoodCalendarDay] = Field(
        default_factory=list,
        description=(
            "One entry per calendar day from first message to last. Empty days "
            "(no messages) are included with message_count=0 and "
            "avg_sentiment=null so the heatmap renders a continuous grid."
        ),
    )
    happiest_day: MoodCalendarDay | None = None
    hardest_day: MoodCalendarDay | None = None
