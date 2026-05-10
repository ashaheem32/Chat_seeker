"""
Emotion service.

Computes the five payloads the EmotionTimeline dashboard module needs:
    - sentiment_timeline       (line chart, granularity selectable)
    - emotion_distribution     (overall donut)
    - emotion_by_participant   (per-sender donuts with sample messages)
    - emotional_peaks          (top-5 highs + bottom-5 lows w/ exemplars)
    - mood_calendar            (one entry per calendar day for the heatmap)

All are read-only and idempotent. Each is cached in Redis under
`emotion:<sub>:<upload_id>:<params>` for one hour. Cache failure is
non-fatal (see `app.core.cache._Cache.get_json`).

Computation lives in Postgres wherever an aggregation pushes down cleanly:
    - date_trunc + AVG / COUNT for time buckets
    - ROW_NUMBER() OVER (PARTITION BY ...) for "dominant emotion per bucket"
      and "top sample messages per (sender, emotion)"
    - generate_series-equivalent fill in Python for empty calendar days

Python only glues per-bucket sub-results together (e.g. zipping bucket-level
avg_sentiment with bucket-level dominant_emotion and the per-sender slices).
Each helper returns a typed Pydantic value to keep mistakes loud.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.models import ChatUpload
from app.schemas.emotion import (
    EmotionByParticipant,
    EmotionDistribution,
    EmotionLabel,
    EmotionShare,
    EmotionalPeak,
    EmotionalPeaks,
    Granularity,
    MoodCalendar,
    MoodCalendarDay,
    ParticipantEmotionDistribution,
    SampleMessage,
    SenderSentimentSlice,
    SentimentTimeline,
    SentimentTimelinePoint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TTL_SECONDS = 60 * 60  # 1h, parity with stats_service

# Canonical order for the seven emotion labels — used to fill missing entries
# with zeros so the frontend gets a stable, exhaustive list per participant.
_EMOTION_ORDER: tuple[EmotionLabel, ...] = (
    "joy", "love", "sadness", "anger", "fear", "surprise", "disgust",
)
_VALID_EMOTIONS: frozenset[str] = frozenset(_EMOTION_ORDER)

# How many sample messages to include per (sender, emotion) for the donut
# tooltips. 2 keeps the JSON payload small while giving the UI options.
_PER_EMOTION_SAMPLES = 2

# Peaks/valleys: bucket size + how many of each to surface.
_PEAK_GRANULARITY: Granularity = "week"
_PEAK_TOP_N = 5
_PEAK_MIN_MESSAGES = 5  # require some volume to call something a peak/valley
_PEAK_TOP_MESSAGES = 3   # exemplars per peak/valley


def _gran_sql(gran: Granularity) -> str:
    """Map our Literal to a Postgres date_trunc unit, with a whitelist guard."""
    if gran not in {"day", "week", "month"}:
        raise ValueError(f"Invalid granularity: {gran!r}")
    return gran


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EmotionService:
    """Stateless. Use the module-level `emotion_service` singleton."""

    # ====================================================================
    # Public API
    # ====================================================================

    async def get_sentiment_timeline(
        self,
        upload_id: UUID,
        db: AsyncSession,
        granularity: Granularity = "day",
        force_refresh: bool = False,
    ) -> SentimentTimeline:
        key = f"emotion:timeline:{upload_id}:{granularity}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return SentimentTimeline.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_timeline(upload_id, db, granularity)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_emotion_distribution(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> EmotionDistribution:
        key = f"emotion:distribution:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return EmotionDistribution.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_distribution(upload_id, db)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_emotion_by_participant(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> EmotionByParticipant:
        key = f"emotion:by_participant:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return EmotionByParticipant.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_by_participant(upload_id, db)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_emotional_peaks(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> EmotionalPeaks:
        key = f"emotion:peaks:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return EmotionalPeaks.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_peaks(upload_id, db)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_mood_calendar(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> MoodCalendar:
        key = f"emotion:calendar:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return MoodCalendar.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_calendar(upload_id, db)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def invalidate(self, upload_id: UUID) -> None:
        """Drop every cached emotion product for an upload."""
        await cache.delete_pattern(f"emotion:*:{upload_id}*")

    # ====================================================================
    # Sentiment timeline
    # ====================================================================

    async def _compute_timeline(
        self,
        upload_id: UUID,
        db: AsyncSession,
        granularity: Granularity,
    ) -> SentimentTimeline:
        gran = _gran_sql(granularity)

        # Bucket-level avg sentiment + total count.
        bucket_sql = text(
            f"""
            SELECT
                date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket,
                AVG(sentiment_score) AS avg_sentiment,
                COUNT(*) AS message_count
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY bucket
            ORDER BY bucket
            """
        )
        bucket_rows = (
            await db.execute(bucket_sql, {"upload_id": str(upload_id)})
        ).all()
        if not bucket_rows:
            return SentimentTimeline(
                upload_id=upload_id, granularity=granularity, senders=[], points=[]
            )

        # Per-bucket dominant emotion via window-function "argmax".
        dominant_sql = text(
            f"""
            WITH bucketed AS (
                SELECT
                    date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket,
                    emotion_label,
                    COUNT(*) AS n
                FROM messages
                WHERE upload_id = :upload_id AND emotion_label IS NOT NULL
                GROUP BY bucket, emotion_label
            ),
            ranked AS (
                SELECT bucket, emotion_label, n,
                       ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY n DESC, emotion_label) AS rk
                FROM bucketed
            )
            SELECT bucket, emotion_label
            FROM ranked
            WHERE rk = 1
            """
        )
        dominant_rows = (
            await db.execute(dominant_sql, {"upload_id": str(upload_id)})
        ).all()
        dominant_by_bucket: dict[date, str] = {
            r.bucket: r.emotion_label for r in dominant_rows
        }

        # Per-sender per-bucket slices.
        per_sender_sql = text(
            f"""
            SELECT
                date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket,
                sender,
                AVG(sentiment_score) AS avg_sentiment,
                COUNT(*) AS message_count
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY bucket, sender
            ORDER BY bucket, sender
            """
        )
        per_sender_rows = (
            await db.execute(per_sender_sql, {"upload_id": str(upload_id)})
        ).all()
        slices_by_bucket: dict[date, list[SenderSentimentSlice]] = defaultdict(list)
        sender_set: set[str] = set()
        for r in per_sender_rows:
            sender_set.add(r.sender)
            slices_by_bucket[r.bucket].append(
                SenderSentimentSlice(
                    sender=r.sender,
                    avg_sentiment=(
                        round(float(r.avg_sentiment), 4)
                        if r.avg_sentiment is not None
                        else None
                    ),
                    message_count=int(r.message_count or 0),
                )
            )

        points: list[SentimentTimelinePoint] = []
        for r in bucket_rows:
            dom = dominant_by_bucket.get(r.bucket)
            points.append(
                SentimentTimelinePoint(
                    date=r.bucket,
                    avg_sentiment=(
                        round(float(r.avg_sentiment), 4)
                        if r.avg_sentiment is not None
                        else None
                    ),
                    message_count=int(r.message_count or 0),
                    dominant_emotion=(
                        dom if dom in _VALID_EMOTIONS else None  # type: ignore[arg-type]
                    ),
                    per_sender=slices_by_bucket.get(r.bucket, []),
                )
            )

        return SentimentTimeline(
            upload_id=upload_id,
            granularity=granularity,
            senders=sorted(sender_set),
            points=points,
        )

    # ====================================================================
    # Emotion distribution (overall)
    # ====================================================================

    async def _compute_distribution(
        self, upload_id: UUID, db: AsyncSession
    ) -> EmotionDistribution:
        # Counts per emotion (NULL excluded).
        counts_sql = text(
            """
            SELECT emotion_label, COUNT(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND emotion_label IS NOT NULL
            GROUP BY emotion_label
            """
        )
        rows = (await db.execute(counts_sql, {"upload_id": str(upload_id)})).all()
        counts: dict[str, int] = {r.emotion_label: int(r.n) for r in rows}
        # Drop unknown labels defensively (in case the model outputs something
        # outside the canonical seven).
        counts = {k: v for k, v in counts.items() if k in _VALID_EMOTIONS}
        total = sum(counts.values())

        # 2 sample messages per emotion, ranked by emotion_score DESC.
        samples_sql = text(
            """
            WITH ranked AS (
                SELECT
                    msg_id, sender, timestamp, content, emotion_label, emotion_score,
                    sentiment_score, sentiment_label,
                    ROW_NUMBER() OVER (
                        PARTITION BY emotion_label
                        ORDER BY emotion_score DESC NULLS LAST, timestamp
                    ) AS rk
                FROM messages
                WHERE upload_id = :upload_id
                  AND emotion_label IS NOT NULL
                  AND content <> ''
                  AND is_deleted = false
            )
            SELECT msg_id, sender, timestamp, content, emotion_label, emotion_score,
                   sentiment_score, sentiment_label
            FROM ranked
            WHERE rk <= :limit
            """
        )
        sample_rows = (
            await db.execute(
                samples_sql,
                {"upload_id": str(upload_id), "limit": _PER_EMOTION_SAMPLES},
            )
        ).all()
        samples_by_emotion: dict[str, list[SampleMessage]] = defaultdict(list)
        for r in sample_rows:
            samples_by_emotion[r.emotion_label].append(_to_sample(r))

        distribution: list[EmotionShare] = []
        for emo in _EMOTION_ORDER:
            n = counts.get(emo, 0)
            distribution.append(
                EmotionShare(
                    emotion=emo,
                    count=n,
                    share=(n / total) if total > 0 else 0.0,
                    sample_messages=samples_by_emotion.get(emo, []),
                )
            )

        return EmotionDistribution(
            upload_id=upload_id, total_classified=total, distribution=distribution
        )

    # ====================================================================
    # Emotion distribution (per-participant)
    # ====================================================================

    async def _compute_by_participant(
        self, upload_id: UUID, db: AsyncSession
    ) -> EmotionByParticipant:
        # Per-(sender, emotion) counts.
        counts_sql = text(
            """
            SELECT sender, emotion_label, COUNT(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND emotion_label IS NOT NULL
            GROUP BY sender, emotion_label
            """
        )
        count_rows = (
            await db.execute(counts_sql, {"upload_id": str(upload_id)})
        ).all()
        per_sender_counts: dict[str, dict[str, int]] = defaultdict(dict)
        for r in count_rows:
            if r.emotion_label not in _VALID_EMOTIONS:
                continue
            per_sender_counts[r.sender][r.emotion_label] = int(r.n)

        # 2 sample messages per (sender, emotion).
        samples_sql = text(
            """
            WITH ranked AS (
                SELECT
                    sender, msg_id, timestamp, content, emotion_label, emotion_score,
                    sentiment_score, sentiment_label,
                    ROW_NUMBER() OVER (
                        PARTITION BY sender, emotion_label
                        ORDER BY emotion_score DESC NULLS LAST, timestamp
                    ) AS rk
                FROM messages
                WHERE upload_id = :upload_id
                  AND emotion_label IS NOT NULL
                  AND content <> ''
                  AND is_deleted = false
            )
            SELECT sender, msg_id, timestamp, content, emotion_label, emotion_score,
                   sentiment_score, sentiment_label
            FROM ranked
            WHERE rk <= :limit
            """
        )
        sample_rows = (
            await db.execute(
                samples_sql,
                {"upload_id": str(upload_id), "limit": _PER_EMOTION_SAMPLES},
            )
        ).all()
        samples_by_sender_emotion: dict[tuple[str, str], list[SampleMessage]] = defaultdict(list)
        for r in sample_rows:
            samples_by_sender_emotion[(r.sender, r.emotion_label)].append(_to_sample(r))

        # Compose per-sender distributions in deterministic order.
        out: list[ParticipantEmotionDistribution] = []
        for sender in sorted(per_sender_counts.keys()):
            counts = per_sender_counts[sender]
            total = sum(counts.values())
            distribution = [
                EmotionShare(
                    emotion=emo,
                    count=counts.get(emo, 0),
                    share=(counts.get(emo, 0) / total) if total > 0 else 0.0,
                    sample_messages=samples_by_sender_emotion.get((sender, emo), []),
                )
                for emo in _EMOTION_ORDER
            ]
            out.append(
                ParticipantEmotionDistribution(
                    sender=sender,
                    total_classified=total,
                    distribution=distribution,
                )
            )

        return EmotionByParticipant(upload_id=upload_id, participants=out)

    # ====================================================================
    # Emotional peaks
    # ====================================================================

    async def _compute_peaks(
        self, upload_id: UUID, db: AsyncSession
    ) -> EmotionalPeaks:
        gran = _gran_sql(_PEAK_GRANULARITY)

        # Bucketed sentiment + counts. Require min volume to qualify.
        bucket_sql = text(
            f"""
            SELECT
                date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket_start,
                (date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')
                    + INTERVAL '1 {gran}' - INTERVAL '1 day')::date AS bucket_end,
                AVG(sentiment_score) AS avg_sent,
                COUNT(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND sentiment_score IS NOT NULL
            GROUP BY bucket_start, bucket_end
            HAVING COUNT(*) >= :min_msgs
            """
        )
        rows = (
            await db.execute(
                bucket_sql,
                {"upload_id": str(upload_id), "min_msgs": _PEAK_MIN_MESSAGES},
            )
        ).all()
        if not rows:
            return EmotionalPeaks(upload_id=upload_id)

        # Sort desc + asc to pick top peaks/valleys.
        rows_sorted = sorted(
            rows, key=lambda r: float(r.avg_sent or 0), reverse=True
        )
        peak_rows = rows_sorted[:_PEAK_TOP_N]
        valley_rows = sorted(rows, key=lambda r: float(r.avg_sent or 0))[:_PEAK_TOP_N]

        peaks = await self._materialize_peaks(upload_id, db, peak_rows, kind="peak", gran=gran)
        valleys = await self._materialize_peaks(
            upload_id, db, valley_rows, kind="valley", gran=gran
        )

        return EmotionalPeaks(upload_id=upload_id, peaks=peaks, valleys=valleys)

    async def _materialize_peaks(
        self,
        upload_id: UUID,
        db: AsyncSession,
        rows: Iterable,
        kind: str,
        gran: str,
    ) -> list[EmotionalPeak]:
        """Hydrate peak/valley rows with dominant emotion + top messages.

        We do all the bucket-level fetches in two queries (regardless of how
        many peaks we have), then join in Python, so the cost scales with N
        rather than N × queries."""
        rows = list(rows)
        if not rows:
            return []

        bucket_starts = [r.bucket_start for r in rows]
        # Dominant emotion per bucket in the input set.
        dom_sql = text(
            f"""
            WITH bucketed AS (
                SELECT
                    date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket,
                    emotion_label,
                    COUNT(*) AS n
                FROM messages
                WHERE upload_id = :upload_id
                  AND emotion_label IS NOT NULL
                  AND date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date = ANY(:starts)
                GROUP BY bucket, emotion_label
            ),
            ranked AS (
                SELECT bucket, emotion_label,
                       ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY n DESC, emotion_label) AS rk
                FROM bucketed
            )
            SELECT bucket, emotion_label FROM ranked WHERE rk = 1
            """
        )
        dom_rows = (
            await db.execute(
                dom_sql,
                {"upload_id": str(upload_id), "starts": bucket_starts},
            )
        ).all()
        dominant_map: dict[date, str] = {r.bucket: r.emotion_label for r in dom_rows}

        # Top messages per bucket. For peaks we want highest-sentiment
        # exemplars, for valleys lowest. Direction is parameterized.
        order_clause = (
            "sentiment_score DESC NULLS LAST"
            if kind == "peak"
            else "sentiment_score ASC NULLS LAST"
        )
        msgs_sql = text(
            f"""
            WITH ranked AS (
                SELECT
                    date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date AS bucket,
                    msg_id, sender, timestamp, content, emotion_label, emotion_score,
                    sentiment_score, sentiment_label,
                    ROW_NUMBER() OVER (
                        PARTITION BY date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date
                        ORDER BY {order_clause}, timestamp
                    ) AS rk
                FROM messages
                WHERE upload_id = :upload_id
                  AND date_trunc('{gran}', timestamp AT TIME ZONE 'UTC')::date = ANY(:starts)
                  AND content <> ''
                  AND is_deleted = false
                  AND sentiment_score IS NOT NULL
            )
            SELECT bucket, msg_id, sender, timestamp, content, emotion_label, emotion_score,
                   sentiment_score, sentiment_label
            FROM ranked
            WHERE rk <= :limit
            """
        )
        msg_rows = (
            await db.execute(
                msgs_sql,
                {
                    "upload_id": str(upload_id),
                    "starts": bucket_starts,
                    "limit": _PEAK_TOP_MESSAGES,
                },
            )
        ).all()
        msgs_by_bucket: dict[date, list[SampleMessage]] = defaultdict(list)
        for r in msg_rows:
            msgs_by_bucket[r.bucket].append(_to_sample(r))

        out: list[EmotionalPeak] = []
        for r in rows:
            dom = dominant_map.get(r.bucket_start)
            out.append(
                EmotionalPeak(
                    type="peak" if kind == "peak" else "valley",
                    bucket_start=r.bucket_start,
                    bucket_end=r.bucket_end,
                    score=round(float(r.avg_sent or 0), 4),
                    message_count=int(r.n or 0),
                    dominant_emotion=(
                        dom if dom in _VALID_EMOTIONS else None  # type: ignore[arg-type]
                    ),
                    top_messages=msgs_by_bucket.get(r.bucket_start, []),
                )
            )
        return out

    # ====================================================================
    # Mood calendar
    # ====================================================================

    async def _compute_calendar(
        self, upload_id: UUID, db: AsyncSession
    ) -> MoodCalendar:
        upload = await db.get(ChatUpload, upload_id)
        if upload is None:
            return MoodCalendar(upload_id=upload_id)

        # Per-day avg sentiment + count.
        day_sql = text(
            """
            SELECT
                (timestamp AT TIME ZONE 'UTC')::date AS day,
                AVG(sentiment_score) AS avg_sent,
                COUNT(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY day
            ORDER BY day
            """
        )
        day_rows = (
            await db.execute(day_sql, {"upload_id": str(upload_id)})
        ).all()
        if not day_rows:
            return MoodCalendar(upload_id=upload_id)

        first_day = day_rows[0].day
        last_day = day_rows[-1].day

        # Dominant emotion per day, same window-function trick.
        dom_sql = text(
            """
            WITH per_day AS (
                SELECT (timestamp AT TIME ZONE 'UTC')::date AS day, emotion_label,
                       COUNT(*) AS n
                FROM messages
                WHERE upload_id = :upload_id AND emotion_label IS NOT NULL
                GROUP BY day, emotion_label
            ),
            ranked AS (
                SELECT day, emotion_label,
                       ROW_NUMBER() OVER (PARTITION BY day ORDER BY n DESC, emotion_label) AS rk
                FROM per_day
            )
            SELECT day, emotion_label FROM ranked WHERE rk = 1
            """
        )
        dom_rows = (await db.execute(dom_sql, {"upload_id": str(upload_id)})).all()
        dominant_by_day: dict[date, str] = {r.day: r.emotion_label for r in dom_rows}

        # Index actual data by day.
        actual: dict[date, tuple[float | None, int]] = {
            r.day: (
                round(float(r.avg_sent), 4) if r.avg_sent is not None else None,
                int(r.n or 0),
            )
            for r in day_rows
        }

        # Build a continuous list filling empty days with zeros / null sentiment.
        days: list[MoodCalendarDay] = []
        total_days = (last_day - first_day).days
        for offset in range(total_days + 1):
            current = first_day.fromordinal(first_day.toordinal() + offset)
            avg_sent, n = actual.get(current, (None, 0))
            dom = dominant_by_day.get(current)
            days.append(
                MoodCalendarDay(
                    date=current,
                    avg_sentiment=avg_sent,
                    dominant_emotion=(
                        dom if dom in _VALID_EMOTIONS else None  # type: ignore[arg-type]
                    ),
                    message_count=n,
                )
            )

        # Pick happiest / hardest among days that actually have content.
        scored = [d for d in days if d.avg_sentiment is not None and d.message_count >= 3]
        happiest = max(scored, key=lambda x: x.avg_sentiment or 0) if scored else None
        hardest = min(scored, key=lambda x: x.avg_sentiment or 0) if scored else None

        return MoodCalendar(
            upload_id=upload_id,
            days=days,
            happiest_day=happiest,
            hardest_day=hardest,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_sample(row) -> SampleMessage:
    """Build a SampleMessage from a SQLAlchemy Row that has the standard
    set of message columns. Truncates content to keep payloads small."""
    content = (row.content or "")[:280]
    ts = row.timestamp
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return SampleMessage(
        msg_id=row.msg_id,
        sender=row.sender,
        timestamp=ts,
        content_preview=content,
        sentiment_score=(
            float(row.sentiment_score) if row.sentiment_score is not None else None
        ),
        sentiment_label=row.sentiment_label,
        emotion_label=(
            row.emotion_label if row.emotion_label in _VALID_EMOTIONS else None
        ),
        emotion_score=(
            float(row.emotion_score) if row.emotion_score is not None else None
        ),
    )


emotion_service = EmotionService()
