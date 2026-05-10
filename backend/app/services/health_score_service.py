"""
Relationship health-score service.

Composes a 0-100 "communication health" score from seven signals
extracted from the messages table. The score is intentionally a *soft*
read on messaging patterns — the disclaimer is built into every
response, and frontend copy reinforces that this is not a relationship
verdict, just one lens on text.

Factor list + weights (must sum to 1.0):
    communication_balance       0.15   how evenly do senders share airtime
    response_consistency        0.15   are responses regular or erratic
    sentiment_trend             0.20   is sentiment improving or declining
    conflict_recovery           0.15   how quickly do hard moments resolve
    affection_frequency         0.15   share of positive/loving messages
    engagement_depth            0.10   message length + question rate
    shared_activities           0.10   we/us/together/plans references

For each factor we compute a normalized score in [0, 1], multiply by
the weight, and the final overall score is sum × 100, rounded to int.

Per-factor narratives + the overall paragraph come from Claude when the
key is configured; otherwise we fall back to deterministic templated
copy so the report always renders.

Caching: `health:report:<upload_id>` for 24h.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.models import Message
from app.schemas.health_score import (
    FactorKey,
    HealthScoreFactor,
    HealthScoreReport,
    ScoreBand,
)
from app.services.conflict_service import conflict_service

logger = logging.getLogger(__name__)


_TTL_SECONDS = 60 * 60 * 24  # 24h

# Weights — must total 1.0. Keep this dict ordered so frontend rows render
# in the same order without needing a sort.
FACTOR_WEIGHTS: dict[FactorKey, float] = {
    "communication_balance": 0.15,
    "response_consistency": 0.15,
    "sentiment_trend": 0.20,
    "conflict_recovery": 0.15,
    "affection_frequency": 0.15,
    "engagement_depth": 0.10,
    "shared_activities": 0.10,
}

FACTOR_LABELS: dict[FactorKey, str] = {
    "communication_balance": "Communication balance",
    "response_consistency": "Response consistency",
    "sentiment_trend": "Sentiment trend",
    "conflict_recovery": "Conflict recovery",
    "affection_frequency": "Affection frequency",
    "engagement_depth": "Engagement depth",
    "shared_activities": "Shared activities",
}

# Built-in disclaimer surfaced in every response.
_DISCLAIMER = (
    "This score reflects messaging patterns only — text is just one channel "
    "of how people connect, and a single number can't capture the full "
    "picture of a relationship. Treat it as a starting point for noticing, "
    "not as a verdict."
)

_METHODOLOGY = (
    "We blend seven signals: how evenly the conversation is shared, how "
    "consistent reply timing is, whether sentiment is trending up or down, "
    "how quickly difficult moments soften, how often affectionate messages "
    "appear, the depth of engagement (length + questions), and how often "
    "you talk about doing things together. Each factor contributes a "
    "weighted slice of a 0-100 composite."
)

# Words that hint at shared activity / planning. Same heuristic vibe as
# the EmotionTimeline keywords; we keep the list short to avoid false
# positives on common message structure.
_SHARED_ACTIVITY_PATTERNS: tuple[str, ...] = (
    r"\bwe(?:'ll|'re| are| should| could)?\b",
    r"\bus\b",
    r"\bour\b",
    r"\blet'?s\b",
    r"\btogether\b",
    r"\btomorrow\b",
    r"\btonight\b",
    r"\bthis weekend\b",
    r"\bdate night\b",
    r"\bplan(?:ning|s)?\b",
    r"\bmovie\b",
    r"\bdinner\b",
    r"\btrip\b",
    r"\bvacation\b",
)
_SHARED_RE = re.compile("|".join(_SHARED_ACTIVITY_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HealthScoreService:
    """Stateless. Use the module-level `health_score_service` singleton."""

    async def get_report(
        self,
        upload_id: UUID,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> HealthScoreReport:
        key = f"health:report:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return HealthScoreReport.model_validate(cached)
                except Exception:
                    pass

        # Compute every factor first so we can ship a complete payload to
        # the LLM in one call rather than chatting per-factor.
        factor_inputs = await self._compute_factor_inputs(upload_id, db)

        # Each computer returns (score, raw_value). We assemble the
        # weighted_score lazily once we have all the scores.
        factors: list[HealthScoreFactor] = []
        for key_, weight in FACTOR_WEIGHTS.items():
            score, raw_value = factor_inputs[key_]
            factors.append(
                HealthScoreFactor(
                    key=key_,
                    label=FACTOR_LABELS[key_],
                    score=round(score, 4),
                    weight=weight,
                    weighted_score=round(score * weight, 4),
                    raw_value=raw_value,
                )
            )

        overall_score = int(round(sum(f.weighted_score for f in factors) * 100))
        band = _band_for(overall_score)

        # Narrative + per-factor insights.
        narrative, used_llm = await self._narrate(factors, overall_score, band)
        if not used_llm:
            for f in factors:
                f.insight = _fallback_insight(f.key, f.score, f.raw_value)

        report = HealthScoreReport(
            upload_id=upload_id,
            overall_score=overall_score,
            band=band,
            factors=factors,
            narrative=narrative,
            methodology=_METHODOLOGY,
            disclaimer=_DISCLAIMER,
            used_llm=used_llm,
        )
        await cache.set_json(
            key, report.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS
        )
        return report

    async def invalidate(self, upload_id: UUID) -> None:
        await cache.delete(f"health:report:{upload_id}")

    # ====================================================================
    # Factor computation
    # ====================================================================

    async def _compute_factor_inputs(
        self, upload_id: UUID, db: AsyncSession
    ) -> dict[FactorKey, tuple[float, str]]:
        """Compute every factor's (score, raw_value). Each factor's
        compute fn is small and self-contained so this orchestration stays
        readable."""
        balance = await self._communication_balance(upload_id, db)
        consistency = await self._response_consistency(upload_id, db)
        sentiment = await self._sentiment_trend(upload_id, db)
        recovery = await self._conflict_recovery(upload_id, db)
        affection = await self._affection_frequency(upload_id, db)
        engagement = await self._engagement_depth(upload_id, db)
        shared = await self._shared_activities(upload_id, db)

        return {
            "communication_balance": balance,
            "response_consistency": consistency,
            "sentiment_trend": sentiment,
            "conflict_recovery": recovery,
            "affection_frequency": affection,
            "engagement_depth": engagement,
            "shared_activities": shared,
        }

    # ---- 1. Communication balance ---------------------------------------
    async def _communication_balance(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """50/50 split → 1.0; 90/10 → 0.2. Uses the dominant sender's
        share so it generalizes naturally to >2 participants."""
        rows = (
            await db.execute(
                select(Message.sender, func.count(Message.id))
                .where(Message.upload_id == upload_id)
                .group_by(Message.sender)
            )
        ).all()
        if not rows:
            return 0.0, "no messages"
        counts = {r[0]: int(r[1]) for r in rows}
        total = sum(counts.values())
        if total == 0:
            return 0.0, "no messages"

        n_senders = len(counts)
        ideal_share = 1 / n_senders
        dominant = max(counts.values()) / total
        # Score: how close is the dominant share to the ideal split?
        # Distance from ideal goes 0 → 1−ideal in the worst case (one
        # person sends everything). Normalize by that worst-case range.
        excess = dominant - ideal_share
        worst = 1 - ideal_share
        score = 1.0 - (excess / worst) if worst > 0 else 1.0
        score = max(0.0, min(1.0, score))

        # Display: "Alice 52% · Bob 48%" sorted by share desc.
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        raw = " · ".join(f"{s} {round(c / total * 100)}%" for s, c in ranked)
        return score, raw

    # ---- 2. Response consistency ----------------------------------------
    async def _response_consistency(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Lower coefficient-of-variation of inter-message gaps means more
        consistent timing. Excludes gaps > 24h so overnight silences
        don't dominate the variance."""
        sql = text(
            """
            SELECT EXTRACT(EPOCH FROM (timestamp - prev_ts)) / 60.0 AS dt_min
            FROM (
                SELECT
                    timestamp,
                    sender,
                    LAG(sender) OVER (ORDER BY timestamp) AS prev_sender,
                    LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
                FROM messages
                WHERE upload_id = :upload_id
            ) t
            WHERE prev_sender IS NOT NULL
              AND prev_sender <> sender
              AND timestamp - prev_ts < INTERVAL '24 hours'
            """
        )
        rows = (await db.execute(sql, {"upload_id": str(upload_id)})).all()
        deltas = [float(r.dt_min) for r in rows if r.dt_min is not None]
        if len(deltas) < 5:
            return 0.5, "insufficient data"
        median = statistics.median(deltas)
        # Use coefficient of variation, but on log-scaled deltas — chat
        # response times are heavy-tailed; raw stddev double-counts the
        # natural day-to-day variance.
        log_deltas = [math.log1p(d) for d in deltas]
        mean = statistics.fmean(log_deltas)
        stdev = statistics.pstdev(log_deltas) if len(log_deltas) > 1 else 0
        cv = stdev / mean if mean > 0 else 0
        # Lower cv = better. Cap at 1.5.
        score = 1.0 - max(0.0, min(1.0, cv / 1.5))
        return score, f"median {median:.1f} min · variability {cv:.2f}"

    # ---- 3. Sentiment trend ---------------------------------------------
    async def _sentiment_trend(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Linear-regression slope of monthly avg sentiment, normalized.
        Slope = +0.10/month is "strongly improving"; slope = −0.10 is
        "strongly declining"."""
        sql = text(
            """
            SELECT
                date_trunc('month', timestamp AT TIME ZONE 'UTC')::date AS m,
                AVG(sentiment_score) AS s
            FROM messages
            WHERE upload_id = :upload_id AND sentiment_score IS NOT NULL
            GROUP BY m
            ORDER BY m
            """
        )
        rows = (await db.execute(sql, {"upload_id": str(upload_id)})).all()
        if len(rows) < 2:
            return 0.5, "insufficient data"
        ys = [float(r.s) for r in rows]
        xs = list(range(len(ys)))
        n = len(ys)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
        den = sum((xs[i] - mean_x) ** 2 for i in range(n))
        slope = num / den if den > 0 else 0.0
        # Normalize: ±0.1 sentiment per month maps to 0/1.
        score = 0.5 + slope * 5
        score = max(0.0, min(1.0, score))
        sign = "+" if slope >= 0 else ""
        return score, f"{sign}{slope:.3f} sentiment/month"

    # ---- 4. Conflict recovery -------------------------------------------
    async def _conflict_recovery(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Re-uses the conflict service for the avg recovery time. No
        conflicts → assume healthy (1.0); recovery in 1h → 0.96; 24h+ → 0.0."""
        analysis = await conflict_service.get_conflict_analysis(upload_id, db)
        n = analysis.summary.total_conflicts_detected
        if n == 0:
            return 1.0, "no difficult moments to measure"
        avg = analysis.summary.avg_recovery_time_hours
        if avg is None:
            return 0.4, f"{n} difficult moments, none resolved cleanly"
        score = 1.0 - max(0.0, min(1.0, avg / 24.0))
        return score, f"{avg:.1f}h avg over {n} moments"

    # ---- 5. Affection frequency -----------------------------------------
    async def _affection_frequency(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Share of messages with sentiment > 0.3 AND emotion in
        {joy, love}. 20% → score 1.0 (saturating)."""
        agg_sql = text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (
                    WHERE sentiment_score > 0.3
                      AND emotion_label IN ('joy', 'love')
                ) AS affectionate
            FROM messages
            WHERE upload_id = :upload_id AND is_deleted = false
            """
        )
        row = (await db.execute(agg_sql, {"upload_id": str(upload_id)})).one()
        total = int(row.total or 0)
        affectionate = int(row.affectionate or 0)
        if total == 0:
            return 0.0, "no messages"
        ratio = affectionate / total
        score = max(0.0, min(1.0, ratio * 5))
        return score, f"{ratio * 100:.1f}% of messages"

    # ---- 6. Engagement depth --------------------------------------------
    async def _engagement_depth(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Blend of avg word count (caps at ~15 words) and question rate
        (caps at ~12% questions). Each contributes half the factor."""
        sql = text(
            """
            SELECT
                AVG(word_count)::float AS avg_words,
                COUNT(*) FILTER (WHERE content ~ '\\?\\s*$')::float
                    / NULLIF(COUNT(*), 0) AS question_rate
            FROM messages
            WHERE upload_id = :upload_id AND is_deleted = false
            """
        )
        row = (await db.execute(sql, {"upload_id": str(upload_id)})).one()
        avg_words = float(row.avg_words or 0)
        question_rate = float(row.question_rate or 0)

        word_component = min(1.0, avg_words / 15.0)
        question_component = min(1.0, question_rate * 8.0)
        score = (word_component + question_component) / 2
        return score, f"{avg_words:.1f} words avg · {question_rate * 100:.1f}% questions"

    # ---- 7. Shared activities -------------------------------------------
    async def _shared_activities(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[float, str]:
        """Fraction of messages mentioning shared / planning language.
        25% → score 1.0 (saturating)."""
        # Stream just the content column to keep memory bounded.
        sql = (
            select(Message.content)
            .where(Message.upload_id == upload_id)
            .where(Message.is_deleted.is_(False))
        )
        rows = (await db.execute(sql)).all()
        if not rows:
            return 0.0, "no messages"
        total = 0
        hits = 0
        for r in rows:
            total += 1
            if r.content and _SHARED_RE.search(r.content):
                hits += 1
        if total == 0:
            return 0.0, "no messages"
        ratio = hits / total
        score = max(0.0, min(1.0, ratio * 4.0))
        return score, f"{ratio * 100:.1f}% of messages reference shared time"

    # ====================================================================
    # Narrative (Claude)
    # ====================================================================

    async def _narrate(
        self,
        factors: list[HealthScoreFactor],
        overall_score: int,
        band: ScoreBand,
    ) -> tuple[str, bool]:
        """Have Claude write per-factor insights + an overall paragraph,
        all in one call. Falls back to deterministic templates when no
        Anthropic key is configured."""
        from app.services.llm import get_llm_client

        client = get_llm_client()
        if not client.is_enabled():
            return _fallback_narrative(factors, overall_score, band), False

        factor_lines = "\n".join(
            f"- {f.key} (score {f.score:.2f}, weight {f.weight:.2f}): {f.raw_value}"
            for f in factors
        )

        system_prompt = (
            "You write warm, observational reflections on a couple's "
            "communication-health score. Tone: thoughtful friend, not a "
            "therapist or rater. Never moralize. Never recommend. Frame "
            "every factor as a pattern to notice, not a grade.\n\n"
            "Return ONLY valid JSON with this shape:\n"
            "{\n"
            '  "factors": {"<factor_key>": "short one-sentence observation", ...},\n'
            '  "narrative": "two to four sentence paragraph"\n'
            "}\n"
            "Every factor key listed must appear in the JSON."
        )
        user_prompt = (
            f"Overall communication score: {overall_score}/100 ({band})\n\n"
            f"Factor signals:\n{factor_lines}\n\n"
            "Write the JSON."
        )

        try:
            raw = await client.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=900,
                temperature=0.5,
                json_mode=True,
            )
        except Exception as e:
            logger.warning("Health-score narrative failed (%s); falling back", e)
            return _fallback_narrative(factors, overall_score, band), False

        parsed = _parse_narrative_json(raw)
        if parsed is None:
            return _fallback_narrative(factors, overall_score, band), False

        # Splice insights into the factor list.
        factor_insights, narrative = parsed
        for f in factors:
            insight = factor_insights.get(f.key)
            if insight:
                f.insight = insight
            else:
                # Missing keys land on the templated insight rather than
                # an empty string so the UI never shows a hollow row.
                f.insight = _fallback_insight(f.key, f.score, f.raw_value)
        return narrative, True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _band_for(score: int) -> ScoreBand:
    if score < 40:
        return "red"
    if score < 70:
        return "amber"
    return "green"


def _extract_text(msg: Any) -> str:
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out)


def _parse_narrative_json(raw: str) -> tuple[dict[str, str], str] | None:
    """Tolerant JSON parse: strips code fences, validates the expected
    shape, returns `(factor_key -> insight, narrative)` on success."""
    import json

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Couldn't parse narrative JSON: %s", raw[:200])
        return None
    factors = payload.get("factors")
    narrative = payload.get("narrative")
    if not isinstance(factors, dict) or not isinstance(narrative, str):
        return None
    insights: dict[str, str] = {
        k: str(v).strip() for k, v in factors.items() if isinstance(v, str)
    }
    return insights, narrative.strip()


def _fallback_insight(key: FactorKey, score: float, raw_value: str) -> str:
    """Deterministic per-factor sentence when no LLM is available.
    Phrased neutrally; the words "good"/"bad" never appear."""
    band = "high" if score >= 0.7 else "moderate" if score >= 0.4 else "low"
    templates: dict[FactorKey, str] = {
        "communication_balance": (
            f"Conversation airtime registered as {band} balance ({raw_value})."
        ),
        "response_consistency": (
            f"Reply timing reads as {band} consistency ({raw_value})."
        ),
        "sentiment_trend": (
            f"Sentiment drift over time registered as {band} ({raw_value})."
        ),
        "conflict_recovery": (
            f"Recovery from harder moments registered as {band} ({raw_value})."
        ),
        "affection_frequency": (
            f"Affectionate signal registered as {band} ({raw_value})."
        ),
        "engagement_depth": (
            f"Engagement depth registered as {band} ({raw_value})."
        ),
        "shared_activities": (
            f"References to shared time registered as {band} ({raw_value})."
        ),
    }
    return templates.get(key, raw_value)


def _fallback_narrative(
    factors: list[HealthScoreFactor], overall_score: int, band: ScoreBand
) -> str:
    """Single-paragraph fallback when no LLM is available."""
    if band == "green":
        opener = (
            "Your messaging shows several positive patterns side by side."
        )
    elif band == "amber":
        opener = (
            "Your messaging shows a mix of strong patterns and ones that read "
            "as more uneven."
        )
    else:
        opener = (
            "Your messaging shows patterns that suggest the harder parts of "
            "communication may be louder right now."
        )
    top = max(factors, key=lambda f: f.score)
    bottom = min(factors, key=lambda f: f.score)
    body = (
        f" {top.label} stands out as a strength at {round(top.score * 100)}%, "
        f"and {bottom.label} reads as the most uneven signal at "
        f"{round(bottom.score * 100)}%."
    )
    return opener + body + " Take this as a starting point for noticing."


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


health_score_service = HealthScoreService()
