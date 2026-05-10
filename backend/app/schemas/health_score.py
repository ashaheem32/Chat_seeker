"""Pydantic schemas for the relationship health score endpoint.

The product framing is "communication health" — a soft, observational
score derived from messaging patterns. The schemas are deliberately
neutral; the disclaimer is hard-coded in the service so we never ship a
report without it."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# Stable factor keys. Frontend keys icons + labels off these.
FactorKey = Literal[
    "communication_balance",
    "response_consistency",
    "sentiment_trend",
    "conflict_recovery",
    "affection_frequency",
    "engagement_depth",
    "shared_activities",
]

ScoreBand = Literal["red", "amber", "green"]


class HealthScoreFactor(BaseModel):
    """One factor in the composite score."""

    key: FactorKey
    label: str
    score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    weighted_score: float = Field(..., ge=0.0, le=1.0)
    raw_value: str = Field(
        default="",
        description=(
            "Display-ready string for the source signal — e.g. '52% / 48%' "
            "or '12.4 min median'. Not used for math; the UI just shows it."
        ),
    )
    insight: str = Field(
        default="",
        description=(
            "Short observational sentence. Claude-written when possible, "
            "templated fallback otherwise."
        ),
    )


class HealthScoreReport(BaseModel):
    upload_id: UUID
    overall_score: int = Field(..., ge=0, le=100)
    band: ScoreBand
    factors: list[HealthScoreFactor] = Field(default_factory=list)
    narrative: str = Field(
        default="",
        description="Claude-written paragraph reflecting on the score.",
    )
    methodology: str = Field(
        default="",
        description="Plain-language explainer of what the score measures.",
    )
    disclaimer: str = Field(
        default="",
        description=(
            "Always populated. Renders in the UI under the score so users "
            "see it before any factor breakdown."
        ),
    )
    used_llm: bool = Field(
        default=False,
        description=(
            "False when the report shipped with deterministic templated "
            "insights because Anthropic was unavailable."
        ),
    )
