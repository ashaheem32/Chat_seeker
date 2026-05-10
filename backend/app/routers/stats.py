"""
Stats endpoints.

Routes (all under /api/v1/stats):
    GET /{upload_id}/overview                  — OverviewStats
    GET /{upload_id}/participant/{name}        — DetailedParticipantStats
    GET /{upload_id}/sentiment-timeline        — SentimentTimeline (granularity)
    GET /{upload_id}/emotion-distribution      — EmotionDistribution (overall)
    GET /{upload_id}/emotion-by-participant    — EmotionByParticipant
    GET /{upload_id}/emotional-peaks           — EmotionalPeaks
    GET /{upload_id}/mood-calendar             — MoodCalendar (per-day)

All are read-only and idempotent. The service layer caches in Redis for
1h; clients can pass `?refresh=1` to bypass the cache (used after re-analysis).

Availability gate:
    Stats are useful even before NLP/embeddings finish — they only depend on
    the messages table being populated, which happens at the end of parsing.
    We therefore require status >= `nlp_processing` rather than `done`. The
    caller still 409s on `pending` / `parsing` / `persisting` so the
    dashboard can show "still parsing" instead of empty data.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import ChatUpload, ProcessingStatus
from app.schemas.conflict import (
    ConflictAnalysisResponse,
    ConflictThemesResponse,
)
from app.schemas.emotion import (
    EmotionByParticipant,
    EmotionDistribution,
    EmotionalPeaks,
    Granularity,
    MoodCalendar,
    SentimentTimeline,
)
from app.schemas.health_score import HealthScoreReport
from app.schemas.love_language import LoveLanguageReport
from app.schemas.stats import DetailedParticipantStats, OverviewStats
from app.schemas.words import (
    Bigrams,
    EmojiFrequency,
    LateNightStats,
    UniqueWords,
    WordFrequency,
    WordTrend,
)
from app.services.conflict_service import conflict_service
from app.services.emotion_service import emotion_service
from app.services.health_score_service import health_score_service
from app.services.love_language_service import love_language_service
from app.services.stats_service import stats_service
from app.services.word_service import word_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Statuses for which the messages table is fully populated. Stats endpoints
# can run on any of these; earlier statuses 409 with a "still parsing" hint.
_STATS_READY_STATUSES = {
    ProcessingStatus.nlp_processing,
    ProcessingStatus.embedding,
    ProcessingStatus.done,
}


async def _require_stats_ready(upload_id: UUID, db: AsyncSession) -> ChatUpload:
    upload = await db.get(ChatUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload {upload_id} not found",
        )
    if upload.status == ProcessingStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload processing failed: {upload.processing_error or 'unknown error'}",
        )
    if upload.status not in _STATS_READY_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Stats are not ready yet (status={upload.status.value}). "
                "They become available once parsing completes."
            ),
        )
    return upload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/overview",
    response_model=OverviewStats,
    summary="Aggregate stats rendered by the dashboard's overview module",
)
async def get_overview(
    upload_id: UUID,
    refresh: bool = Query(
        default=False,
        description="Bypass the 1h Redis cache and recompute from Postgres.",
    ),
    db: AsyncSession = Depends(get_db),
) -> OverviewStats:
    await _require_stats_ready(upload_id, db)
    return await stats_service.get_overview(upload_id, db, force_refresh=refresh)


@router.get(
    "/{upload_id}/participant/{sender}",
    response_model=DetailedParticipantStats,
    summary="Detailed stats for a single participant",
)
async def get_participant(
    upload_id: UUID,
    sender: str,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> DetailedParticipantStats:
    await _require_stats_ready(upload_id, db)
    return await stats_service.get_participant(
        upload_id, sender, db, force_refresh=refresh
    )


# ---------------------------------------------------------------------------
# Emotion / sentiment routes — back the EmotionTimeline dashboard module.
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/sentiment-timeline",
    response_model=SentimentTimeline,
    summary="Sentiment scores bucketed by day / week / month, with per-sender slices",
)
async def get_sentiment_timeline(
    upload_id: UUID,
    granularity: Granularity = Query(
        default="day",
        description="Bucket size: 'day' (default), 'week', or 'month'.",
    ),
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> SentimentTimeline:
    await _require_stats_ready(upload_id, db)
    return await emotion_service.get_sentiment_timeline(
        upload_id, db, granularity=granularity, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/emotion-distribution",
    response_model=EmotionDistribution,
    summary="Overall emotion shares (counts + sample messages per emotion)",
)
async def get_emotion_distribution(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> EmotionDistribution:
    await _require_stats_ready(upload_id, db)
    return await emotion_service.get_emotion_distribution(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/emotion-by-participant",
    response_model=EmotionByParticipant,
    summary="Per-participant emotion distribution with sample messages per emotion",
)
async def get_emotion_by_participant(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> EmotionByParticipant:
    await _require_stats_ready(upload_id, db)
    return await emotion_service.get_emotion_by_participant(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/emotional-peaks",
    response_model=EmotionalPeaks,
    summary="Top-5 highest-sentiment + top-5 lowest-sentiment week buckets, with exemplars",
)
async def get_emotional_peaks(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> EmotionalPeaks:
    await _require_stats_ready(upload_id, db)
    return await emotion_service.get_emotional_peaks(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/mood-calendar",
    response_model=MoodCalendar,
    summary="Daily avg sentiment + dominant emotion for the GitHub-style heatmap",
)
async def get_mood_calendar(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> MoodCalendar:
    await _require_stats_ready(upload_id, db)
    return await emotion_service.get_mood_calendar(
        upload_id, db, force_refresh=refresh
    )


# ---------------------------------------------------------------------------
# Words / emoji routes — back the WordAnalytics dashboard module.
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/word-frequency",
    response_model=WordFrequency,
    summary="Top words with per-sender contribution (for stacked bars + word cloud)",
)
async def get_word_frequency(
    upload_id: UUID,
    sender: str | None = Query(
        default=None,
        description="Restrict to a single sender. Omit for the chat-wide ranking.",
    ),
    top_n: int = Query(default=100, ge=1, le=500),
    exclude_stopwords: bool = Query(default=True),
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> WordFrequency:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_word_frequency(
        upload_id,
        db,
        sender=sender,
        top_n=top_n,
        exclude_stopwords=exclude_stopwords,
        force_refresh=refresh,
    )


@router.get(
    "/{upload_id}/emoji-frequency",
    response_model=EmojiFrequency,
    summary="Top emojis with per-sender breakdown + sentiment correlation",
)
async def get_emoji_frequency(
    upload_id: UUID,
    sender: str | None = Query(default=None),
    top_n: int = Query(default=30, ge=1, le=200),
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> EmojiFrequency:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_emoji_frequency(
        upload_id,
        db,
        sender=sender,
        top_n=top_n,
        force_refresh=refresh,
    )


@router.get(
    "/{upload_id}/bigrams",
    response_model=Bigrams,
    summary="Top 2-word phrases, with detection of bigrams used by every participant",
)
async def get_bigrams(
    upload_id: UUID,
    sender: str | None = Query(default=None),
    top_n: int = Query(default=20, ge=1, le=100),
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> Bigrams:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_bigrams(
        upload_id,
        db,
        sender=sender,
        top_n=top_n,
        force_refresh=refresh,
    )


@router.get(
    "/{upload_id}/unique-words",
    response_model=UniqueWords,
    summary="Vocabulary richness, distinctive words per sender, length over time",
)
async def get_unique_words(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> UniqueWords:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_unique_words(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/word-trend",
    response_model=WordTrend,
    summary="Usage of a specific word over time (case-insensitive, word-boundary)",
)
async def get_word_trend(
    upload_id: UUID,
    word: str = Query(..., min_length=1, max_length=64),
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> WordTrend:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_word_trends(
        upload_id, db, word=word, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/late-night",
    response_model=LateNightStats,
    summary="Stats on messages sent 23:00 → 04:59 UTC, with the most-extreme samples",
)
async def get_late_night(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> LateNightStats:
    await _require_stats_ready(upload_id, db)
    return await word_service.get_late_night_messages(
        upload_id, db, force_refresh=refresh
    )


# ---------------------------------------------------------------------------
# Conflict analysis routes — back the ConflictAnalysis dashboard module.
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/conflicts",
    response_model=ConflictAnalysisResponse,
    summary=(
        "Detected difficult-moment windows + summary stats + language patterns. "
        "Themes are populated only when previously requested via /conflict-themes."
    ),
)
async def get_conflicts(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> ConflictAnalysisResponse:
    await _require_stats_ready(upload_id, db)
    return await conflict_service.get_conflict_analysis(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/conflict-themes",
    response_model=ConflictThemesResponse,
    summary=(
        "Cluster the detected conflict windows into recurring themes via Claude. "
        "Falls back to a single 'Recurring tension' bucket when ANTHROPIC_API_KEY is unset."
    ),
)
async def get_conflict_themes(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> ConflictThemesResponse:
    await _require_stats_ready(upload_id, db)
    return await conflict_service.get_conflict_themes(
        upload_id, db, force_refresh=refresh
    )


# ---------------------------------------------------------------------------
# Love-language + health-score routes — back the LoveLanguages and
# HealthScore dashboard modules.
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/love-language",
    response_model=LoveLanguageReport,
    summary=(
        "Per-sender love-language distributions classified via Claude. "
        "Falls back to a keyword heuristic when ANTHROPIC_API_KEY is unset."
    ),
)
async def get_love_language(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> LoveLanguageReport:
    await _require_stats_ready(upload_id, db)
    return await love_language_service.get_report(
        upload_id, db, force_refresh=refresh
    )


@router.get(
    "/{upload_id}/health-score",
    response_model=HealthScoreReport,
    summary=(
        "Composite communication-health score (0-100) with per-factor "
        "breakdown and a Claude-written narrative + insights."
    ),
)
async def get_health_score(
    upload_id: UUID,
    refresh: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> HealthScoreReport:
    await _require_stats_ready(upload_id, db)
    return await health_score_service.get_report(
        upload_id, db, force_refresh=refresh
    )
