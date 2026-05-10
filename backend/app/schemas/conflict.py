"""Pydantic schemas for the conflict-analysis endpoints.

A note on framing:
    The product surfaces this as "difficult moments" rather than "fights".
    The schema field names stay neutral too — `peak_negativity_score`,
    `who_escalates_more`, `resolution_type` — so frontend copy can soften
    the tone without us re-engineering the data model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.emotion import SampleMessage


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


ResolutionType = Literal["apology", "topic_change", "time_gap", "unresolved"]


class ResolutionInfo(BaseModel):
    """How (and whether) a difficult moment ended."""

    type: ResolutionType
    sender: str | None = Field(
        default=None,
        description="Who sent the message that signaled resolution. None for `unresolved`.",
    )
    message: SampleMessage | None = None
    minutes_after_window: int | None = Field(
        default=None,
        description=(
            "Minutes between the last conflict message and the resolution "
            "message. Null when unresolved."
        ),
    )


# ---------------------------------------------------------------------------
# Conflict window
# ---------------------------------------------------------------------------


class ConflictWindow(BaseModel):
    """One detected difficult-moment window."""

    model_config = ConfigDict(from_attributes=True)

    # Stable per-window identifier we generate at detect time. Frontend
    # uses it as React keys + when grouping windows into themes.
    window_id: str

    start_msg_id: str = Field(
        ..., description="UCJ msg_id of the first message in the window."
    )
    end_msg_id: str
    start_db_id: UUID = Field(
        ..., description="DB UUID of the first message — used for the context drawer."
    )
    end_db_id: UUID

    start_timestamp: datetime
    end_timestamp: datetime
    duration_minutes: int = 0
    duration_messages: int = 0

    peak_negativity_score: float = Field(
        default=0.0, ge=-1.0, le=1.0, description="Min sentiment_score in the window."
    )

    trigger_sender: str | None = None
    trigger_message: SampleMessage | None = None

    resolution: ResolutionInfo

    top_words: list[str] = Field(
        default_factory=list,
        description="Up to 8 most frequent non-stopword tokens inside the window.",
    )
    sample_messages: list[SampleMessage] = Field(
        default_factory=list,
        description=(
            "Up to 5 representative messages — picked by emotion-extreme + "
            "trigger + resolution to give the UI a story arc."
        ),
    )
    sentiment_arc: list[float | None] = Field(
        default_factory=list,
        description=(
            "Sentiment scores for messages in [start - 5, end + 5], in order. "
            "Drives the per-conflict mini line-chart. None entries mark messages "
            "that weren't sentiment-classified — frontend renders gaps."
        ),
    )


# ---------------------------------------------------------------------------
# Theme clustering (Claude-generated)
# ---------------------------------------------------------------------------


class ConflictTheme(BaseModel):
    """A recurring pattern across multiple windows.

    Themes are inferred by Claude from the trigger messages of the
    detected windows. The model picks the label, description, and
    membership; we just persist the result so the dashboard can render
    "Response time frustration", "Making plans", etc."""

    label: str = Field(..., max_length=80)
    description: str = Field(default="", max_length=240)
    frequency: int = 0
    window_ids: list[str] = Field(default_factory=list)
    avg_sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    example_messages: list[SampleMessage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Language / time patterns
# ---------------------------------------------------------------------------


class WordCount(BaseModel):
    word: str
    count: int


class HourCount(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    count: int = 0


class DayOfWeekCount(BaseModel):
    """0 == Monday, 6 == Sunday (matches OverviewStats.busiest_day_of_week)."""

    dow: int = Field(..., ge=0, le=6)
    count: int = 0


class ConflictLanguage(BaseModel):
    """Word frequencies during conflict windows + during their resolutions,
    plus the time-of-day / day-of-week distributions of when conflicts start."""

    conflict_words: list[WordCount] = Field(default_factory=list)
    resolution_words: list[WordCount] = Field(default_factory=list)
    hour_distribution: list[HourCount] = Field(default_factory=list)
    dow_distribution: list[DayOfWeekCount] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class SenderRole(BaseModel):
    """Per-sender count toward an aggregate role (e.g. "escalates more").

    Frontend pairs this with neutral copy: "X tends to send the first
    message in difficult periods (Y of N)." Including `total` lets the UI
    show the ratio so users see the pattern in context, not as a
    standalone accusation."""

    sender: str
    count: int = 0
    total: int = 0


class MonthCount(BaseModel):
    month: str = Field(..., description="ISO 'YYYY-MM'")
    count: int = 0


class ConflictSummary(BaseModel):
    total_conflicts_detected: int = 0
    avg_duration_hours: float = 0.0
    avg_recovery_time_hours: float | None = Field(
        default=None,
        description=(
            "Mean time between window end and resolution message. None when "
            "no conflicts have a resolution timestamp (all unresolved)."
        ),
    )
    most_common_triggers: list[str] = Field(
        default_factory=list,
        description="Up to 5 conflict-keyword triggers, ranked by appearance.",
    )
    who_escalates_more: SenderRole | None = None
    who_resolves_more: SenderRole | None = None
    conflict_frequency_by_month: list[MonthCount] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Composite response
# ---------------------------------------------------------------------------


class ConflictAnalysisResponse(BaseModel):
    """Returned from GET /api/stats/{id}/conflicts.

    `themes` is `None` until the (slow + paid) Claude clustering runs.
    Frontend can fetch /conflict-themes lazily after the rest of the
    dashboard renders so the page never waits on the LLM round-trip."""

    upload_id: UUID
    summary: ConflictSummary
    windows: list[ConflictWindow] = Field(default_factory=list)
    language: ConflictLanguage
    themes: list[ConflictTheme] | None = None


class ConflictThemesResponse(BaseModel):
    """Returned from GET /api/stats/{id}/conflict-themes."""

    upload_id: UUID
    themes: list[ConflictTheme] = Field(default_factory=list)
    used_llm: bool = Field(
        default=False,
        description=(
            "False when Anthropic was unavailable and we fell back to a "
            "deterministic 'unclustered' single-bucket result."
        ),
    )
