"""
Stats service — computes the OverviewStats payload for the dashboard.

Computation strategy:
    Most metrics are pushed into Postgres aggregates (counts, percentile_cont,
    window functions). Two things stay in Python:
        - Per-participant most-used word, because a small Python Counter
          with a tunable stopword list is more flexible than SQL regex
          aggregation, and word frequencies don't dominate runtime.
        - Per-participant favorite emoji, computed from Message.emojis
          (already a text[] column populated at ingest), summed in a
          Counter for parity with most-used-word.

Caching:
    OverviewStats is cached in Redis under `stats:overview:<upload_id>` for
    one hour. Failure to read/write Redis is non-fatal — see
    `app.core.cache._Cache.get_json`. Callers can pass `force_refresh=True`
    to bypass the cache (used after re-analysis).

Thread / async model:
    All DB queries run through a single AsyncSession passed in by the
    router. We deliberately don't open new sessions here so the calling
    request still owns the transaction lifecycle.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.models import ChatUpload, Message
from app.schemas.stats import (
    DetailedParticipantStats,
    HourlyHistogramBucket,
    MessageReference,
    OverviewStats,
    ParticipantStats,
    TopItem,
    WhoTextsFirstSlice,
)

logger = logging.getLogger(__name__)


# Cache key + TTL.
_OVERVIEW_TTL_SECONDS = 60 * 60  # 1 hour, per spec
_PARTICIPANT_TTL_SECONDS = 60 * 60


def _overview_key(upload_id: UUID) -> str:
    return f"stats:overview:{upload_id}"


def _participant_key(upload_id: UUID, sender: str) -> str:
    return f"stats:participant:{upload_id}:{sender}"


# Minimal stopword list. Intentionally small — chat language is informal
# and an aggressive list strips things ("really", "totally") that *are*
# meaningful in personal conversation analysis. This is the same list shape
# the preprocessor abbrevs target so we cover both raw and expanded forms.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "and", "or", "but", "if", "so", "as", "of", "to", "in", "on", "at",
        "for", "with", "from", "by", "is", "am", "are", "was", "were", "be",
        "been", "being", "do", "does", "did", "doing", "have", "has", "had",
        "having", "i", "me", "my", "we", "us", "our", "you", "your", "yours",
        "he", "she", "him", "her", "his", "hers", "it", "its", "they", "them",
        "their", "this", "that", "these", "those", "there", "here", "where",
        "when", "what", "who", "whom", "which", "why", "how", "not", "no",
        "yes", "yeah", "ok", "okay", "k", "kk", "lol", "haha", "hehe",
        "u", "ur", "im", "ill", "ive", "id", "youre", "youve", "youll", "thats",
        "well", "now", "just", "still", "also", "too", "even", "very",
        "really", "kinda", "like",
        "than", "then", "into", "out", "up", "down", "off", "over", "under",
        "again", "more", "most", "much", "many", "few", "any", "some", "all",
        "will", "would", "could", "should", "may", "might", "can", "must",
        "shall", "let", "got", "get", "go", "going", "went",
    }
)

# Token regex — words of two or more letters/apostrophes. Chosen to
# exclude all-numeric tokens and standalone punctuation while keeping
# contractions like "don't" intact.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{1,}")


@dataclass(slots=True)
class _TimePatterns:
    """Internal typed result for `_fetch_time_patterns`. Plain dict caused
    pyright to widen each value to `object`, which then fails the call into
    OverviewStats(...). A dataclass fixes the inference for cheap."""

    busiest_hour: int | None
    busiest_day_of_week: int | None
    most_active_month: str | None
    hourly_histogram: list[HourlyHistogramBucket]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StatsService:
    """Stateless service. The async-session-per-call pattern lets us share
    one `StatsService` across requests without locking concerns."""

    # ====================================================================
    # Public API
    # ====================================================================

    async def get_overview(
        self,
        upload_id: UUID,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> OverviewStats:
        """Return aggregate stats for an upload, hitting Redis when warm."""
        key = _overview_key(upload_id)
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return OverviewStats.model_validate(cached)
                except Exception as e:
                    logger.warning("Stale stats cache for %s; recomputing: %s", upload_id, e)

        result = await self._compute_overview(upload_id, db)
        await cache.set_json(
            key, result.model_dump(mode="json"), ttl_seconds=_OVERVIEW_TTL_SECONDS
        )
        return result

    async def get_participant(
        self,
        upload_id: UUID,
        sender: str,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> DetailedParticipantStats:
        key = _participant_key(upload_id, sender)
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return DetailedParticipantStats.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_participant(upload_id, sender, db)
        await cache.set_json(
            key, result.model_dump(mode="json"), ttl_seconds=_PARTICIPANT_TTL_SECONDS
        )
        return result

    async def invalidate(self, upload_id: UUID) -> None:
        """Drop all cached stats for an upload. Called after re-analysis."""
        await cache.delete(_overview_key(upload_id))
        await cache.delete_pattern(f"stats:participant:{upload_id}:*")

    # ====================================================================
    # Overview computation
    # ====================================================================

    async def _compute_overview(
        self, upload_id: UUID, db: AsyncSession
    ) -> OverviewStats:
        """Compose every overview metric. Keeps each sub-query in its own
        helper so the orchestration stays readable."""

        upload = await db.get(ChatUpload, upload_id)
        if upload is None:
            raise ValueError(f"Upload {upload_id} not found")

        # Hero counts in one round-trip.
        totals = await self._fetch_totals(upload_id, db)

        # Date / streak / silence (Postgres window-function magic).
        date_stats = await self._fetch_date_stats(upload_id, db)

        # Time-of-day / day-of-week / month patterns.
        time_patterns = await self._fetch_time_patterns(upload_id, db)

        # Behavioral.
        median_response = await self._fetch_median_response_minutes(upload_id, db)
        who_first = await self._fetch_who_texts_first(upload_id, db)

        # Per-participant.
        per_participant = await self._fetch_per_participant(upload_id, db)

        # Fun facts.
        longest_msg = await self._fetch_longest_message(upload_id, db)
        most_replied = await self._fetch_most_replied_to(upload_id, db)
        first_msg = await self._fetch_first_message(upload_id, db)

        # Word counter — uses the messages' content; reused for "most used
        # word overall" and per-participant most_used_word so we only walk
        # the table once.
        per_sender_word, overall_word, per_sender_emoji = await self._fetch_word_emoji_counters(
            upload_id, db
        )

        # Splice top word/emoji into per_participant rows.
        for p in per_participant:
            p.most_used_word = per_sender_word.get(p.name)
            p.favorite_emoji = per_sender_emoji.get(p.name)

        active_days = date_stats["active_days"]
        avg_per_day = (
            totals["total_messages"] / active_days if active_days > 0 else 0.0
        )

        return OverviewStats(
            upload_id=upload_id,
            total_messages=totals["total_messages"],
            total_words=totals["total_words"],
            total_emojis=totals["total_emojis"],
            total_characters=totals["total_characters"],
            conversation_days=upload.span_days or date_stats["conversation_days"],
            active_days=active_days,
            longest_streak=date_stats["longest_streak"],
            longest_silence=date_stats["longest_silence"],
            avg_messages_per_day=round(avg_per_day, 2),
            avg_response_time_minutes=median_response,
            who_texts_first=who_first,
            busiest_hour=time_patterns.busiest_hour,
            busiest_day_of_week=time_patterns.busiest_day_of_week,
            most_active_month=time_patterns.most_active_month,
            hourly_histogram=time_patterns.hourly_histogram,
            per_participant=per_participant,
            longest_message=longest_msg,
            most_replied_to=most_replied,
            first_message=first_msg,
            most_used_word_overall=overall_word,
        )

    # --------- sub-fetchers ------------------------------------------------

    async def _fetch_totals(
        self, upload_id: UUID, db: AsyncSession
    ) -> dict[str, int]:
        """Hero metrics: counts, sums. Single round-trip via aggregate select."""
        # Use a CASE-ish sum for emoji count from the array length.
        stmt = select(
            func.count(Message.id).label("total_messages"),
            func.coalesce(func.sum(Message.word_count), 0).label("total_words"),
            func.coalesce(func.sum(Message.char_count), 0).label("total_characters"),
            func.coalesce(
                func.sum(func.coalesce(func.array_length(Message.emojis, 1), 0)),
                0,
            ).label("total_emojis"),
        ).where(Message.upload_id == upload_id)

        row = (await db.execute(stmt)).one()
        return {
            "total_messages": int(row.total_messages or 0),
            "total_words": int(row.total_words or 0),
            "total_characters": int(row.total_characters or 0),
            "total_emojis": int(row.total_emojis or 0),
        }

    async def _fetch_date_stats(
        self, upload_id: UUID, db: AsyncSession
    ) -> dict[str, int]:
        """Active days, conversation span, longest streak, longest silence.

        We compute streak / silence in one CTE pass: the classic "subtract
        a row_number to find consecutive groups" trick.
        """
        sql = text(
            """
            WITH days AS (
                SELECT DISTINCT (timestamp AT TIME ZONE 'UTC')::date AS d
                FROM messages
                WHERE upload_id = :upload_id
            ),
            ordered AS (
                SELECT
                    d,
                    d - (row_number() OVER (ORDER BY d))::int AS grp,
                    LAG(d) OVER (ORDER BY d) AS prev_d
                FROM days
            ),
            streaks AS (
                SELECT count(*) AS streak FROM ordered GROUP BY grp
            )
            SELECT
                (SELECT count(*) FROM days) AS active_days,
                COALESCE((SELECT max(d) FROM days) - (SELECT min(d) FROM days) + 1, 0) AS conversation_days,
                COALESCE((SELECT max(streak) FROM streaks), 0) AS longest_streak,
                COALESCE(
                    (SELECT max(d - prev_d) FROM ordered WHERE prev_d IS NOT NULL),
                    0
                ) AS longest_silence
            """
        )
        row = (await db.execute(sql, {"upload_id": str(upload_id)})).one()
        return {
            "active_days": int(row.active_days or 0),
            "conversation_days": int(row.conversation_days or 0),
            "longest_streak": int(row.longest_streak or 0),
            "longest_silence": int(row.longest_silence or 0),
        }

    async def _fetch_time_patterns(
        self, upload_id: UUID, db: AsyncSession
    ) -> "_TimePatterns":
        """Busiest hour / DOW / month, plus the 24-bucket histogram."""

        # Hourly histogram — fill missing hours with 0 so the frontend can
        # render a 24-bar sparkline without index gymnastics.
        hourly_sql = text(
            """
            SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int AS hour,
                   count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY hour
            """
        )
        hourly_rows = (
            await db.execute(hourly_sql, {"upload_id": str(upload_id)})
        ).all()
        hour_counts: dict[int, int] = {int(r.hour): int(r.n) for r in hourly_rows}
        hourly_histogram = [
            HourlyHistogramBucket(hour=h, count=hour_counts.get(h, 0))
            for h in range(24)
        ]
        busiest_hour = (
            max(hour_counts, key=hour_counts.get)  # type: ignore[arg-type]
            if hour_counts
            else None
        )

        # Day of week — Postgres ISODOW: 1=Monday … 7=Sunday. Subtract 1
        # to match the spec's 0=Monday … 6=Sunday.
        dow_sql = text(
            """
            SELECT (EXTRACT(ISODOW FROM timestamp AT TIME ZONE 'UTC')::int - 1) AS dow,
                   count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY dow
            ORDER BY n DESC
            LIMIT 1
            """
        )
        dow_row = (await db.execute(dow_sql, {"upload_id": str(upload_id)})).first()
        busiest_day_of_week = int(dow_row.dow) if dow_row else None

        # Most active month — ISO yyyy-mm string.
        month_sql = text(
            """
            SELECT to_char(date_trunc('month', timestamp AT TIME ZONE 'UTC'), 'YYYY-MM') AS m,
                   count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY m
            ORDER BY n DESC
            LIMIT 1
            """
        )
        month_row = (await db.execute(month_sql, {"upload_id": str(upload_id)})).first()
        most_active_month = month_row.m if month_row else None

        return _TimePatterns(
            busiest_hour=busiest_hour,
            busiest_day_of_week=busiest_day_of_week,
            most_active_month=most_active_month,
            hourly_histogram=hourly_histogram,
        )

    async def _fetch_median_response_minutes(
        self, upload_id: UUID, db: AsyncSession
    ) -> float | None:
        """Median minutes between consecutive messages where the sender
        changes. Excludes gaps over 24h to keep "they slept" out of the
        response-time signal."""
        sql = text(
            """
            WITH ordered AS (
                SELECT
                    timestamp,
                    sender,
                    LAG(sender) OVER (ORDER BY timestamp) AS prev_sender,
                    LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
                FROM messages
                WHERE upload_id = :upload_id
            )
            SELECT percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (timestamp - prev_ts)) / 60.0
            ) AS median_minutes
            FROM ordered
            WHERE prev_sender IS NOT NULL
              AND prev_sender <> sender
              AND timestamp - prev_ts < INTERVAL '24 hours'
            """
        )
        row = (await db.execute(sql, {"upload_id": str(upload_id)})).first()
        if row is None or row.median_minutes is None:
            return None
        return round(float(row.median_minutes), 2)

    async def _fetch_who_texts_first(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[WhoTextsFirstSlice]:
        """Per day, who sent the first message. Tally + share across days."""
        sql = text(
            """
            WITH first_per_day AS (
                SELECT DISTINCT ON (date_trunc('day', timestamp AT TIME ZONE 'UTC'))
                    date_trunc('day', timestamp AT TIME ZONE 'UTC') AS day,
                    sender
                FROM messages
                WHERE upload_id = :upload_id
                ORDER BY day, timestamp ASC
            )
            SELECT sender, count(*) AS n
            FROM first_per_day
            GROUP BY sender
            ORDER BY n DESC
            """
        )
        rows = (await db.execute(sql, {"upload_id": str(upload_id)})).all()
        if not rows:
            return []
        total = sum(int(r.n) for r in rows)
        return [
            WhoTextsFirstSlice(
                sender=r.sender,
                days_started=int(r.n),
                share=round(int(r.n) / total, 4) if total else 0.0,
            )
            for r in rows
        ]

    async def _fetch_per_participant(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[ParticipantStats]:
        """Numeric per-sender rollup. Most-used-word and favorite-emoji
        are spliced in by the caller after the word/emoji counter pass."""
        sql = text(
            """
            SELECT
                sender,
                count(*) AS message_count,
                COALESCE(sum(word_count), 0) AS word_count,
                COALESCE(sum(char_count), 0) AS char_count,
                COALESCE(sum(COALESCE(array_length(emojis, 1), 0)), 0) AS emoji_count,
                COALESCE(sum(CASE WHEN content ~ '\\?\\s*$' THEN 1 ELSE 0 END), 0) AS question_count,
                COALESCE(sum(CASE WHEN content ~ '!\\s*$' THEN 1 ELSE 0 END), 0) AS exclamation_count
            FROM messages
            WHERE upload_id = :upload_id
            GROUP BY sender
            ORDER BY message_count DESC
            """
        )
        rows = (await db.execute(sql, {"upload_id": str(upload_id)})).all()
        out: list[ParticipantStats] = []
        for r in rows:
            mc = int(r.message_count or 0)
            wc = int(r.word_count or 0)
            avg_len = (int(r.char_count or 0) / mc) if mc > 0 else 0.0
            out.append(
                ParticipantStats(
                    name=r.sender,
                    message_count=mc,
                    word_count=wc,
                    emoji_count=int(r.emoji_count or 0),
                    avg_message_length=round(avg_len, 2),
                    question_count=int(r.question_count or 0),
                    exclamation_count=int(r.exclamation_count or 0),
                )
            )
        return out

    async def _fetch_longest_message(
        self, upload_id: UUID, db: AsyncSession
    ) -> MessageReference | None:
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .where(Message.is_deleted.is_(False))
            .order_by(Message.char_count.desc())
            .limit(1)
        )
        msg = (await db.execute(stmt)).scalars().first()
        return _to_msg_ref(msg) if msg else None

    async def _fetch_most_replied_to(
        self, upload_id: UUID, db: AsyncSession
    ) -> MessageReference | None:
        """The message that was replied-to most often. Joins back to the
        original message via reply_to_id == msg_id."""
        sql = text(
            """
            SELECT reply_to_id, count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND reply_to_id IS NOT NULL
            GROUP BY reply_to_id
            ORDER BY n DESC
            LIMIT 1
            """
        )
        row = (await db.execute(sql, {"upload_id": str(upload_id)})).first()
        if row is None or row.reply_to_id is None:
            return None
        target = (
            await db.execute(
                select(Message)
                .where(Message.upload_id == upload_id)
                .where(Message.msg_id == row.reply_to_id)
                .limit(1)
            )
        ).scalars().first()
        if target is None:
            return None
        ref = _to_msg_ref(target)
        if ref:
            ref.reply_count = int(row.n)
        return ref

    async def _fetch_first_message(
        self, upload_id: UUID, db: AsyncSession
    ) -> MessageReference | None:
        stmt = (
            select(Message)
            .where(Message.upload_id == upload_id)
            .order_by(Message.timestamp.asc(), Message.msg_index.asc())
            .limit(1)
        )
        msg = (await db.execute(stmt)).scalars().first()
        return _to_msg_ref(msg) if msg else None

    async def _fetch_word_emoji_counters(
        self, upload_id: UUID, db: AsyncSession
    ) -> tuple[dict[str, str], str | None, dict[str, str]]:
        """Single pass over (sender, content, emojis) for word + emoji freqs.

        Returns:
            per_sender_word: sender → most-used non-stopword word
            overall_word:    most-used non-stopword word across all senders
            per_sender_emoji: sender → most-used emoji glyph
        """
        # Stream rows in pages so 50k-message chats don't all sit in memory
        # at once. We don't need the ORM here; just sender + content + emojis.
        per_sender_words: dict[str, Counter[str]] = defaultdict(Counter)
        overall_words: Counter[str] = Counter()
        per_sender_emojis: dict[str, Counter[str]] = defaultdict(Counter)

        page_size = 2000
        last_index = -1
        while True:
            stmt = (
                select(
                    Message.msg_index,
                    Message.sender,
                    Message.content,
                    Message.emojis,
                    Message.is_deleted,
                )
                .where(Message.upload_id == upload_id)
                .where(Message.msg_index > last_index)
                .order_by(Message.msg_index.asc())
                .limit(page_size)
            )
            rows = (await db.execute(stmt)).all()
            if not rows:
                break
            for r in rows:
                if r.is_deleted:
                    continue
                content = (r.content or "").lower()
                if content:
                    for tok in _WORD_RE.findall(content):
                        if tok in _STOPWORDS:
                            continue
                        per_sender_words[r.sender][tok] += 1
                        overall_words[tok] += 1
                if r.emojis:
                    for em in r.emojis:
                        per_sender_emojis[r.sender][em] += 1
                last_index = int(r.msg_index)

        per_sender_word_top = {
            sender: c.most_common(1)[0][0] if c else None
            for sender, c in per_sender_words.items()
        }
        per_sender_emoji_top = {
            sender: c.most_common(1)[0][0] if c else None
            for sender, c in per_sender_emojis.items()
        }
        overall_top = (
            overall_words.most_common(1)[0][0] if overall_words else None
        )

        # Strip Nones from the maps so the spliced fields stay clean.
        return (
            {k: v for k, v in per_sender_word_top.items() if v is not None},
            overall_top,
            {k: v for k, v in per_sender_emoji_top.items() if v is not None},
        )

    # ====================================================================
    # Per-participant deep dive
    # ====================================================================

    async def _compute_participant(
        self, upload_id: UUID, sender: str, db: AsyncSession
    ) -> DetailedParticipantStats:
        """Compute the detailed-participant payload."""

        # Numeric aggregates (single SQL pass).
        agg_sql = text(
            """
            SELECT
                count(*) AS message_count,
                COALESCE(sum(word_count), 0) AS word_count,
                COALESCE(sum(char_count), 0) AS char_count,
                COALESCE(sum(COALESCE(array_length(emojis, 1), 0)), 0) AS emoji_count,
                COALESCE(sum(CASE WHEN content ~ '\\?\\s*$' THEN 1 ELSE 0 END), 0) AS question_count,
                COALESCE(sum(CASE WHEN content ~ '!\\s*$' THEN 1 ELSE 0 END), 0) AS exclamation_count,
                AVG(sentiment_score) AS avg_sentiment,
                COALESCE(sum(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END), 0) AS pos,
                COALESCE(sum(CASE WHEN sentiment_label = 'neutral'  THEN 1 ELSE 0 END), 0) AS neu,
                COALESCE(sum(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END), 0) AS neg
            FROM messages
            WHERE upload_id = :upload_id AND sender = :sender
            """
        )
        agg = (
            await db.execute(agg_sql, {"upload_id": str(upload_id), "sender": sender})
        ).one()

        message_count = int(agg.message_count or 0)
        if message_count == 0:
            # Sender has no messages — return a sparse, empty payload rather
            # than 404. Frontend can render a "no data" panel.
            return DetailedParticipantStats(upload_id=upload_id, sender=sender)

        avg_msg_len = (
            (int(agg.char_count or 0) / message_count) if message_count else 0.0
        )

        # Emotion share.
        emotion_sql = text(
            """
            SELECT emotion_label, count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND sender = :sender AND emotion_label IS NOT NULL
            GROUP BY emotion_label
            """
        )
        emotion_rows = (
            await db.execute(emotion_sql, {"upload_id": str(upload_id), "sender": sender})
        ).all()
        emotion_share = {r.emotion_label: int(r.n) for r in emotion_rows}

        # Hourly histogram for this sender.
        hourly_sql = text(
            """
            SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int AS hour,
                   count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id AND sender = :sender
            GROUP BY hour
            """
        )
        hourly_rows = (
            await db.execute(hourly_sql, {"upload_id": str(upload_id), "sender": sender})
        ).all()
        hour_counts = {int(r.hour): int(r.n) for r in hourly_rows}
        hourly_histogram = [
            HourlyHistogramBucket(hour=h, count=hour_counts.get(h, 0))
            for h in range(24)
        ]

        # Top words / emojis — Python pass over this sender's content + emojis.
        word_counter: Counter[str] = Counter()
        emoji_counter: Counter[str] = Counter()
        async for content, emojis, is_deleted in _stream_sender_content(
            upload_id, sender, db
        ):
            if is_deleted:
                continue
            if content:
                for tok in _WORD_RE.findall(content.lower()):
                    if tok in _STOPWORDS:
                        continue
                    word_counter[tok] += 1
            if emojis:
                emoji_counter.update(emojis)
        top_words = [
            TopItem(value=w, count=n) for w, n in word_counter.most_common(10)
        ]
        top_emojis = [
            TopItem(value=e, count=n) for e, n in emoji_counter.most_common(10)
        ]

        # First / last message references.
        first_msg = (
            await db.execute(
                select(Message)
                .where(Message.upload_id == upload_id)
                .where(Message.sender == sender)
                .order_by(Message.timestamp.asc())
                .limit(1)
            )
        ).scalars().first()
        last_msg = (
            await db.execute(
                select(Message)
                .where(Message.upload_id == upload_id)
                .where(Message.sender == sender)
                .order_by(Message.timestamp.desc())
                .limit(1)
            )
        ).scalars().first()

        return DetailedParticipantStats(
            upload_id=upload_id,
            sender=sender,
            message_count=message_count,
            word_count=int(agg.word_count or 0),
            emoji_count=int(agg.emoji_count or 0),
            avg_message_length=round(avg_msg_len, 2),
            question_count=int(agg.question_count or 0),
            exclamation_count=int(agg.exclamation_count or 0),
            avg_sentiment=(
                round(float(agg.avg_sentiment), 3) if agg.avg_sentiment is not None else None
            ),
            sentiment_share={
                "positive": int(agg.pos or 0),
                "neutral": int(agg.neu or 0),
                "negative": int(agg.neg or 0),
            },
            emotion_share=emotion_share,
            hourly_histogram=hourly_histogram,
            top_words=top_words,
            top_emojis=top_emojis,
            first_message=_to_msg_ref(first_msg),
            last_message=_to_msg_ref(last_msg),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _stream_sender_content(
    upload_id: UUID,
    sender: str,
    db: AsyncSession,
) -> AsyncIterator[tuple[str | None, list[str] | None, bool]]:
    """Page through a single sender's content/emojis/is_deleted columns.

    Yields tuples; using a generator keeps memory bounded for senders with
    tens of thousands of messages."""
    page_size = 2000
    last_index = -1
    while True:
        stmt = (
            select(
                Message.msg_index,
                Message.content,
                Message.emojis,
                Message.is_deleted,
            )
            .where(Message.upload_id == upload_id)
            .where(Message.sender == sender)
            .where(Message.msg_index > last_index)
            .order_by(Message.msg_index.asc())
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).all()
        if not rows:
            return
        for r in rows:
            yield (r.content, r.emojis, bool(r.is_deleted))
            last_index = int(r.msg_index)


def _to_msg_ref(msg: Message | None) -> MessageReference | None:
    if msg is None:
        return None
    preview = (msg.content or "")[:280]
    # Force timezone-aware UTC to keep JSON output deterministic.
    ts = msg.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return MessageReference(
        id=msg.id,
        msg_id=msg.msg_id,
        sender=msg.sender,
        timestamp=ts,
        content_preview=preview,
        char_count=msg.char_count or 0,
    )


# Module-level singleton so dependents don't reconstruct it per request.
stats_service = StatsService()
