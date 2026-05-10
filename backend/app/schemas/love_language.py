"""Pydantic schemas for the love-language analysis endpoint.

Maps onto Gary Chapman's five love languages. We deliberately don't
extend the taxonomy — the framework's value is its concision, and
adding categories would dilute the per-category sample sizes."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.emotion import SampleMessage


# The five canonical love-language keys. Frontend keeps these in this
# exact order for the radar chart so the axes don't reflow when senders
# rank differently.
LoveLanguageCategory = Literal[
    "words_of_affirmation",
    "acts_of_service",
    "quality_time",
    "physical_touch",
    "gift_giving",
]

LOVE_LANGUAGE_ORDER: tuple[LoveLanguageCategory, ...] = (
    "words_of_affirmation",
    "acts_of_service",
    "quality_time",
    "physical_touch",
    "gift_giving",
)


class LoveLanguageCount(BaseModel):
    """One row in a sender's category distribution."""

    category: LoveLanguageCategory
    count: int = 0
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    examples: list[SampleMessage] = Field(default_factory=list)


class LoveLanguageBreakdown(BaseModel):
    """Per-sender rollup. `summary` is a short observational sentence
    composed deterministically from the primary + secondary categories so
    callers can render it without waiting on a second LLM call."""

    sender: str
    total_classified: int = 0
    distribution: list[LoveLanguageCount] = Field(default_factory=list)
    primary: LoveLanguageCategory | None = None
    secondary: LoveLanguageCategory | None = None
    summary: str = ""


class LoveLanguageReport(BaseModel):
    """Top-level response from /stats/{id}/love-language."""

    upload_id: UUID
    participants: list[LoveLanguageBreakdown] = Field(default_factory=list)
    compatibility_insight: str = Field(
        default="",
        description=(
            "Claude-written paragraph reflecting on how the two senders' "
            "expressions of love line up. Empty when no LLM was available."
        ),
    )
    used_llm: bool = Field(
        default=False,
        description=(
            "False when classification ran on a heuristic fallback because "
            "ANTHROPIC_API_KEY wasn't configured or the call failed."
        ),
    )
