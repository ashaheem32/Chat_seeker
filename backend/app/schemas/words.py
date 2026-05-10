"""Pydantic schemas for the words / emoji analytics endpoints."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.emotion import SampleMessage


# ---------------------------------------------------------------------------
# Word frequency
# ---------------------------------------------------------------------------


class WordFrequencyItem(BaseModel):
    """One word in the frequency list. Includes per-sender contribution so
    the dashboard can render stacked bars without a second round-trip."""

    word: str
    count: int = 0
    pct: float = Field(default=0.0, ge=0.0, le=1.0)
    per_sender: dict[str, int] = Field(default_factory=dict)


class WordFrequency(BaseModel):
    """Filtered + ranked word frequency for the chat (or a single sender).

    `total_tokens` is the post-filter denominator; the seven `pct` values
    don't necessarily sum to 1 because we cap the list at top_n."""

    upload_id: UUID
    sender: str | None = Field(
        default=None, description="If set, the request was filtered to this sender."
    )
    exclude_stopwords: bool = True
    total_tokens: int = 0
    items: list[WordFrequencyItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Emoji frequency
# ---------------------------------------------------------------------------


class EmojiFrequencyItem(BaseModel):
    """One emoji entry. `unicode_name` is best-effort (some pictographs
    have no `unicodedata.name()`); `avg_sentiment` is the mean
    sentiment_score across messages that contained this emoji — useful
    for the "emoji sentiment correlation" view."""

    emoji: str
    count: int = 0
    pct: float = Field(default=0.0, ge=0.0, le=1.0)
    unicode_name: str | None = None
    per_sender: dict[str, int] = Field(default_factory=dict)
    avg_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)


class UniqueEmojiUse(BaseModel):
    """Emoji that's heavily skewed toward one sender (>= 70% of usage)."""

    emoji: str
    sender: str
    count: int = 0
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    unicode_name: str | None = None


class EmojiFrequency(BaseModel):
    upload_id: UUID
    sender: str | None = None
    total_emojis: int = 0
    items: list[EmojiFrequencyItem] = Field(default_factory=list)
    unique_to_sender: list[UniqueEmojiUse] = Field(
        default_factory=list,
        description="Top emojis where one sender accounts for ≥70% of uses.",
    )


# ---------------------------------------------------------------------------
# Bigrams
# ---------------------------------------------------------------------------


class BigramItem(BaseModel):
    phrase: str
    count: int = 0
    per_sender: dict[str, int] = Field(default_factory=dict)


class Bigrams(BaseModel):
    upload_id: UUID
    sender: str | None = None
    items: list[BigramItem] = Field(default_factory=list)
    inside_phrases: list[BigramItem] = Field(
        default_factory=list,
        description=(
            "Bigrams used at least N times by every participant — surfaces "
            "shared references / inside jokes."
        ),
    )


# ---------------------------------------------------------------------------
# Unique words / vocabulary richness
# ---------------------------------------------------------------------------


class DistinctiveWord(BaseModel):
    """A word one sender uses much more than the others.

    `score` is in [0, 1]: 1.0 means this word is exclusive to `sender`,
    0.5 means even split. Frontend can use it for sorting + a tiny inline
    bar showing how skewed the usage is."""

    word: str
    count: int
    score: float = Field(..., ge=0.0, le=1.0)


class SenderVocab(BaseModel):
    sender: str
    unique_count: int = 0
    total_words: int = 0
    richness_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Type-token ratio: distinct non-stopword tokens / total tokens. "
            "Higher means more vocabulary variety."
        ),
    )
    distinctive_words: list[DistinctiveWord] = Field(default_factory=list)


class MessageLengthPoint(BaseModel):
    """One bucket on the average-message-length timeline."""

    date: date
    per_sender: dict[str, float] = Field(
        default_factory=dict,
        description="Mean char_count per sender within the bucket.",
    )


class UniqueWords(BaseModel):
    upload_id: UUID
    total_unique: int = 0
    per_sender: list[SenderVocab] = Field(default_factory=list)
    length_over_time: list[MessageLengthPoint] = Field(
        default_factory=list,
        description="Per-day mean message length; drives the Section 4 line chart.",
    )


# ---------------------------------------------------------------------------
# Word trends (single word over time)
# ---------------------------------------------------------------------------


class WordTrendPoint(BaseModel):
    date: date
    count: int = 0


class WordTrend(BaseModel):
    upload_id: UUID
    word: str
    total_uses: int = 0
    points: list[WordTrendPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Late-night messages
# ---------------------------------------------------------------------------


class LateNightHourBucket(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    count: int = 0


class LateNightStats(BaseModel):
    """Late-night = 23:00 → 04:59 local-equivalent (we treat all timestamps
    as UTC; if a chat lives in a single time zone the result is still
    semantically accurate within ±1h)."""

    model_config = ConfigDict(from_attributes=True)

    upload_id: UUID
    total_late_night: int = 0
    pct_of_total: float = Field(default=0.0, ge=0.0, le=1.0)
    per_sender: dict[str, int] = Field(default_factory=dict)
    by_hour: list[LateNightHourBucket] = Field(default_factory=list)
    sample_messages: list[SampleMessage] = Field(default_factory=list)
