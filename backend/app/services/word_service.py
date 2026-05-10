"""
Word & emoji analytics service.

Powers the WordAnalytics dashboard module:
    - Word frequency (overall and per-sender) with per-sender breakdown
    - Emoji frequency + per-sender breakdown + sentiment correlation
    - Bigrams, with detection of shared "inside phrases"
    - Vocabulary richness (type-token ratio) + distinctive words per sender
    - Per-day average message length for the Section 4 line chart
    - Single-word usage trend over time
    - Late-night messaging stats (23:00 → 04:59 UTC)

Computation strategy:
    - Word, bigram and per-sender counts share one paged scan over
      `(sender, content)`. We accumulate three counters in a single pass
      to avoid scanning the messages table multiple times.
    - Emoji frequency uses the indexed `emojis text[]` column via
      `unnest()` so Postgres can stream rows without us pulling content.
    - Per-emoji sentiment correlation joins emoji rows with sentiment via
      the same `unnest()` lateral.
    - Late-night stats and word trends each run a single SQL pass.

Caching:
    Each public method has a Redis key shaped
    `words:<sub>:<upload_id>:<args>`. Failure to hit Redis is non-fatal
    (see `app.core.cache._Cache.get_json`).

Stopwords:
    A ~280-entry frozen set blending NLTK's English list with chat
    fillers (lol, omg, kk, ...). Trimmed to keep things people actually
    say in personal chats — we don't aggressively strip emotional words.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.schemas.emotion import SampleMessage
from app.schemas.words import (
    BigramItem,
    Bigrams,
    DistinctiveWord,
    EmojiFrequency,
    EmojiFrequencyItem,
    LateNightHourBucket,
    LateNightStats,
    MessageLengthPoint,
    SenderVocab,
    UniqueEmojiUse,
    UniqueWords,
    WordFrequency,
    WordFrequencyItem,
    WordTrend,
    WordTrendPoint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stopwords + tokenizer
# ---------------------------------------------------------------------------

# 280-entry stopword set. NLTK English (~180) plus the ~100 chat-specific
# fillers we see most often after running the pipeline on real exports.
# Curated to preserve emotion-bearing words ("really", "love", "miss")
# that aggressive stopword lists strip out.
STOPWORDS: frozenset[str] = frozenset(
    {
        # NLTK English (trimmed)
        "a", "an", "the", "and", "or", "but", "if", "while", "with", "without",
        "as", "of", "to", "in", "on", "at", "for", "from", "by", "into", "onto",
        "is", "am", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "doing", "done",
        "have", "has", "had", "having",
        "i", "me", "my", "mine", "myself",
        "we", "us", "our", "ours", "ourselves",
        "you", "your", "yours", "yourself", "yourselves",
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "they", "them", "their", "theirs", "themselves",
        "this", "that", "these", "those",
        "there", "here", "where", "when", "what", "who", "whom", "which",
        "why", "how", "whose",
        "not", "no", "nor", "neither", "either",
        "all", "any", "some", "few", "many", "much", "more", "most",
        "such", "only", "own", "same", "so", "than", "too", "very",
        "just", "now", "then", "ever", "never", "always", "still", "yet",
        "again", "also", "even", "though", "although", "however", "therefore",
        "into", "out", "up", "down", "off", "over", "under", "above", "below",
        "between", "through", "during", "before", "after", "until", "since",
        "about", "against", "along", "among", "around", "behind", "beside",
        "will", "would", "could", "should", "may", "might", "can", "must",
        "shall",
        "let", "get", "got", "go", "going", "went", "gone",
        "say", "said", "says", "tell", "told", "tells",
        "think", "thought", "thinks",
        "make", "made", "makes",
        "see", "saw", "seen",
        "know", "knew", "known",
        "come", "came",
        "take", "took", "taken",
        "give", "gave", "given",
        "want", "wanted", "wants",
        "need", "needed", "needs",
        "look", "looked", "looking",
        "find", "found",
        "way", "ways", "thing", "things", "stuff",
        "people", "person",
        "day", "days", "today", "tomorrow", "yesterday",
        "year", "years", "time", "times",
        "good", "great", "nice", "fine",
        "well",
        # Chat fillers + abbreviations users type frequently
        "ok", "okay", "okok", "kk", "k",
        "yeah", "yea", "ya", "yep", "yup", "yes",
        "nah", "nope",
        "hmm", "hmmm", "uhh", "uhm", "uhmm", "umm", "ummm",
        "haha", "hahah", "hahaha", "lol", "lolol", "lolz",
        "rofl", "lmao", "lmfao",
        "hehe", "hehee", "hihi",
        "omg", "omfg", "wtf", "wth", "smh",
        "u", "ur", "uve", "uhv",
        "im", "ive", "id", "ill",
        "youre", "youve", "youll", "youd",
        "thats", "whats", "whos", "wheres",
        "isnt", "arent", "wasnt", "werent", "havent", "hasnt", "hadnt",
        "dont", "doesnt", "didnt",
        "cant", "cannot", "couldnt", "wouldnt", "shouldnt",
        "wont", "shant",
        "btw", "imo", "idk", "idc", "fyi", "tbh", "ngl", "lmk", "rn",
        "asap", "atm", "irl", "tldr",
        "msg", "txt",
        # Other common chat noise
        "oh", "ah", "eh", "uh", "huh", "wow", "ohh", "ohhh",
        "thx", "thnx", "ty", "tyvm", "tysm",
        "pls", "plz", "please",
        "sorry", "soz",
        "bye", "byebye", "byee",
        "hi", "hii", "hiii", "hey", "heyy", "heyyy", "heyo", "yo",
        "hello", "helo",
    }
)

# Token regex: 2+ alpha (Unicode letters) chars including apostrophes.
# Excludes pure numeric tokens, URLs (already stripped if running through
# the preprocessor; here we strip again defensively).
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{1,}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def _tokenize(content: str) -> list[str]:
    if not content:
        return []
    cleaned = _URL_RE.sub(" ", content.lower())
    return _WORD_RE.findall(cleaned)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TTL_SECONDS = 60 * 60  # 1h, parity with stats_service
_MIN_INSIDE_USES = 3  # bigram must appear ≥3 times per sender to count as "shared"
_DISTINCTIVE_MIN_USES = 5
_DISTINCTIVE_TOP_N = 12
_LATE_NIGHT_HOURS = (23, 0, 1, 2, 3, 4)
_LATE_NIGHT_SAMPLES = 6


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class WordService:
    """Stateless. Use the module-level `word_service` singleton."""

    # ====================================================================
    # Public API
    # ====================================================================

    async def get_word_frequency(
        self,
        upload_id: UUID,
        db: AsyncSession,
        sender: str | None = None,
        top_n: int = 100,
        exclude_stopwords: bool = True,
        force_refresh: bool = False,
    ) -> WordFrequency:
        key = f"words:freq:{upload_id}:{sender or '*'}:{top_n}:{int(exclude_stopwords)}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return WordFrequency.model_validate(cached)
                except Exception:
                    pass

        corpus = await self._scan_corpus(upload_id, db)
        result = self._build_word_frequency(
            upload_id, corpus, sender, top_n, exclude_stopwords
        )
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_emoji_frequency(
        self,
        upload_id: UUID,
        db: AsyncSession,
        sender: str | None = None,
        top_n: int = 30,
        force_refresh: bool = False,
    ) -> EmojiFrequency:
        key = f"words:emoji:{upload_id}:{sender or '*'}:{top_n}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return EmojiFrequency.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_emoji_frequency(upload_id, db, sender, top_n)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_bigrams(
        self,
        upload_id: UUID,
        db: AsyncSession,
        sender: str | None = None,
        top_n: int = 20,
        force_refresh: bool = False,
    ) -> Bigrams:
        key = f"words:bigrams:{upload_id}:{sender or '*'}:{top_n}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return Bigrams.model_validate(cached)
                except Exception:
                    pass

        corpus = await self._scan_corpus(upload_id, db)
        result = self._build_bigrams(upload_id, corpus, sender, top_n)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_unique_words(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> UniqueWords:
        key = f"words:unique:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return UniqueWords.model_validate(cached)
                except Exception:
                    pass

        corpus = await self._scan_corpus(upload_id, db)
        length_points = await self._compute_length_timeline(upload_id, db)
        result = self._build_unique_words(upload_id, corpus, length_points)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_word_trends(
        self,
        upload_id: UUID,
        db: AsyncSession,
        word: str,
        force_refresh: bool = False,
    ) -> WordTrend:
        word_norm = word.strip().lower()
        if not word_norm:
            return WordTrend(upload_id=upload_id, word=word_norm)

        key = f"words:trend:{upload_id}:{word_norm}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return WordTrend.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_word_trend(upload_id, db, word_norm)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def get_late_night_messages(
        self, upload_id: UUID, db: AsyncSession, force_refresh: bool = False
    ) -> LateNightStats:
        key = f"words:latenight:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return LateNightStats.model_validate(cached)
                except Exception:
                    pass

        result = await self._compute_late_night(upload_id, db)
        await cache.set_json(key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS)
        return result

    async def invalidate(self, upload_id: UUID) -> None:
        await cache.delete_pattern(f"words:*:{upload_id}*")

    # ====================================================================
    # Shared corpus scan (words + bigrams + unique words)
    # ====================================================================

    async def _scan_corpus(
        self, upload_id: UUID, db: AsyncSession
    ) -> "_Corpus":
        """Single paged scan over (sender, content). Builds counters that
        all word/bigram/unique-words methods consume."""

        global_unigrams: Counter[str] = Counter()
        global_bigrams: Counter[str] = Counter()
        per_sender_unigrams: dict[str, Counter[str]] = defaultdict(Counter)
        per_sender_bigrams: dict[str, Counter[str]] = defaultdict(Counter)
        per_sender_total_tokens: dict[str, int] = defaultdict(int)
        per_sender_unique: dict[str, set[str]] = defaultdict(set)
        global_unique: set[str] = set()

        page_size = 2000
        last_index = -1
        while True:
            stmt = text(
                """
                SELECT msg_index, sender, content, is_deleted
                FROM messages
                WHERE upload_id = :upload_id AND msg_index > :after
                ORDER BY msg_index ASC
                LIMIT :limit
                """
            )
            rows = (
                await db.execute(
                    stmt,
                    {
                        "upload_id": str(upload_id),
                        "after": last_index,
                        "limit": page_size,
                    },
                )
            ).all()
            if not rows:
                break
            for r in rows:
                last_index = int(r.msg_index)
                if r.is_deleted:
                    continue
                tokens = _tokenize(r.content or "")
                if not tokens:
                    continue
                # Unigrams.
                for tok in tokens:
                    global_unigrams[tok] += 1
                    per_sender_unigrams[r.sender][tok] += 1
                    per_sender_total_tokens[r.sender] += 1
                    if tok not in STOPWORDS:
                        per_sender_unique[r.sender].add(tok)
                        global_unique.add(tok)
                # Bigrams. Skip pairs where either token is a stopword to
                # surface meaningful phrases ("date night", "next week")
                # rather than "i was", "is it".
                if len(tokens) >= 2:
                    for a, b in zip(tokens, tokens[1:]):
                        if a in STOPWORDS or b in STOPWORDS:
                            continue
                        phrase = f"{a} {b}"
                        global_bigrams[phrase] += 1
                        per_sender_bigrams[r.sender][phrase] += 1

        return _Corpus(
            global_unigrams=global_unigrams,
            global_bigrams=global_bigrams,
            per_sender_unigrams=per_sender_unigrams,
            per_sender_bigrams=per_sender_bigrams,
            per_sender_total_tokens=per_sender_total_tokens,
            per_sender_unique=per_sender_unique,
            global_unique=global_unique,
        )

    # ====================================================================
    # Word frequency
    # ====================================================================

    def _build_word_frequency(
        self,
        upload_id: UUID,
        corpus: "_Corpus",
        sender: str | None,
        top_n: int,
        exclude_stopwords: bool,
    ) -> WordFrequency:
        if sender:
            unigrams = corpus.per_sender_unigrams.get(sender, Counter())
            total_tokens = corpus.per_sender_total_tokens.get(sender, 0)
        else:
            unigrams = corpus.global_unigrams
            total_tokens = sum(corpus.per_sender_total_tokens.values())

        # Build a filtered + sorted list. We compute pct against
        # `total_tokens` rather than the post-filter sum so the surfaced
        # share matches "share of total content" intuition.
        items: list[WordFrequencyItem] = []
        if total_tokens > 0:
            iterable = (
                (w, c)
                for w, c in unigrams.most_common()
                if not (exclude_stopwords and w in STOPWORDS)
            )
            for word, count in self._take(iterable, top_n):
                items.append(
                    WordFrequencyItem(
                        word=word,
                        count=count,
                        pct=count / total_tokens if total_tokens else 0.0,
                        per_sender={
                            s: c.get(word, 0)
                            for s, c in corpus.per_sender_unigrams.items()
                            if c.get(word, 0) > 0
                        },
                    )
                )

        return WordFrequency(
            upload_id=upload_id,
            sender=sender,
            exclude_stopwords=exclude_stopwords,
            total_tokens=total_tokens,
            items=items,
        )

    @staticmethod
    def _take(it: Iterable[tuple[str, int]], n: int) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for item in it:
            out.append(item)
            if len(out) >= n:
                break
        return out

    # ====================================================================
    # Bigrams
    # ====================================================================

    def _build_bigrams(
        self,
        upload_id: UUID,
        corpus: "_Corpus",
        sender: str | None,
        top_n: int,
    ) -> Bigrams:
        if sender:
            counter = corpus.per_sender_bigrams.get(sender, Counter())
        else:
            counter = corpus.global_bigrams

        items: list[BigramItem] = []
        for phrase, count in counter.most_common(top_n):
            items.append(
                BigramItem(
                    phrase=phrase,
                    count=count,
                    per_sender={
                        s: c.get(phrase, 0)
                        for s, c in corpus.per_sender_bigrams.items()
                        if c.get(phrase, 0) > 0
                    },
                )
            )

        # Inside phrases — bigrams used by every participant ≥ N times.
        senders = list(corpus.per_sender_bigrams.keys())
        inside: list[BigramItem] = []
        if len(senders) >= 2:
            # Take phrases that appear ≥ MIN in EVERY sender.
            common_phrases = (
                phrase
                for phrase, total in corpus.global_bigrams.most_common()
                if all(
                    corpus.per_sender_bigrams[s].get(phrase, 0) >= _MIN_INSIDE_USES
                    for s in senders
                )
            )
            # Stop after a generous cap to keep payloads small.
            for phrase in self._take_strs(common_phrases, 16):
                inside.append(
                    BigramItem(
                        phrase=phrase,
                        count=corpus.global_bigrams[phrase],
                        per_sender={
                            s: corpus.per_sender_bigrams[s].get(phrase, 0)
                            for s in senders
                        },
                    )
                )

        return Bigrams(
            upload_id=upload_id,
            sender=sender,
            items=items,
            inside_phrases=inside,
        )

    @staticmethod
    def _take_strs(it: Iterable[str], n: int) -> list[str]:
        out: list[str] = []
        for x in it:
            out.append(x)
            if len(out) >= n:
                break
        return out

    # ====================================================================
    # Unique words / vocabulary richness
    # ====================================================================

    def _build_unique_words(
        self,
        upload_id: UUID,
        corpus: "_Corpus",
        length_points: list[MessageLengthPoint],
    ) -> UniqueWords:
        senders = list(corpus.per_sender_unigrams.keys())
        per_sender: list[SenderVocab] = []

        for s in sorted(senders):
            unigrams = corpus.per_sender_unigrams[s]
            total = corpus.per_sender_total_tokens[s]
            unique = len(corpus.per_sender_unique[s])
            richness = (unique / total) if total > 0 else 0.0
            per_sender.append(
                SenderVocab(
                    sender=s,
                    unique_count=unique,
                    total_words=total,
                    richness_score=round(richness, 4),
                    distinctive_words=self._distinctive_for_sender(s, corpus),
                )
            )

        return UniqueWords(
            upload_id=upload_id,
            total_unique=len(corpus.global_unique),
            per_sender=per_sender,
            length_over_time=length_points,
        )

    def _distinctive_for_sender(
        self, sender: str, corpus: "_Corpus"
    ) -> list[DistinctiveWord]:
        """Words this sender uses much more than the others.

        Score = freq_sender / sum(freq across all senders), filtered to
        non-stopwords with at least `_DISTINCTIVE_MIN_USES` total uses.
        Words exclusive to one sender land at 1.0; an even split lands
        at 0.5. We surface the top _DISTINCTIVE_TOP_N by score, breaking
        ties on raw frequency."""
        own = corpus.per_sender_unigrams.get(sender, Counter())
        scored: list[tuple[str, float, int]] = []
        for word, total in corpus.global_unigrams.items():
            if word in STOPWORDS:
                continue
            if total < _DISTINCTIVE_MIN_USES:
                continue
            mine = own.get(word, 0)
            if mine == 0:
                continue
            score = mine / total
            scored.append((word, score, mine))
        # Rank by score desc, then by raw count for the runner-ups.
        scored.sort(key=lambda t: (-t[1], -t[2]))
        return [
            DistinctiveWord(word=w, count=c, score=round(s, 4))
            for w, s, c in scored[:_DISTINCTIVE_TOP_N]
        ]

    # ====================================================================
    # Length timeline (per-day mean message length per sender)
    # ====================================================================

    async def _compute_length_timeline(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[MessageLengthPoint]:
        sql = text(
            """
            SELECT
                (timestamp AT TIME ZONE 'UTC')::date AS day,
                sender,
                AVG(char_count) AS avg_len
            FROM messages
            WHERE upload_id = :upload_id AND is_deleted = false AND content <> ''
            GROUP BY day, sender
            ORDER BY day, sender
            """
        )
        rows = (await db.execute(sql, {"upload_id": str(upload_id)})).all()
        if not rows:
            return []
        by_day: dict[date, dict[str, float]] = defaultdict(dict)
        for r in rows:
            by_day[r.day][r.sender] = round(float(r.avg_len or 0), 2)
        return [
            MessageLengthPoint(date=d, per_sender=ps)
            for d, ps in sorted(by_day.items())
        ]

    # ====================================================================
    # Emoji frequency
    # ====================================================================

    async def _compute_emoji_frequency(
        self,
        upload_id: UUID,
        db: AsyncSession,
        sender: str | None,
        top_n: int,
    ) -> EmojiFrequency:
        # Per-sender emoji counts via lateral unnest.
        sender_filter = "AND m.sender = :sender" if sender else ""
        counts_sql = text(
            f"""
            SELECT m.sender, e.emoji, count(*) AS n
            FROM messages m, LATERAL unnest(m.emojis) AS e(emoji)
            WHERE m.upload_id = :upload_id
              AND m.is_deleted = false
              {sender_filter}
            GROUP BY m.sender, e.emoji
            """
        )
        params: dict[str, str] = {"upload_id": str(upload_id)}
        if sender:
            params["sender"] = sender
        count_rows = (await db.execute(counts_sql, params)).all()
        if not count_rows:
            return EmojiFrequency(upload_id=upload_id, sender=sender)

        per_sender_emoji: dict[str, dict[str, int]] = defaultdict(dict)
        global_counts: Counter[str] = Counter()
        for r in count_rows:
            per_sender_emoji[r.sender][r.emoji] = int(r.n)
            global_counts[r.emoji] += int(r.n)

        # Per-emoji avg sentiment (across all senders, regardless of `sender`
        # filter — the correlation is a chat-wide phenomenon).
        sent_sql = text(
            """
            SELECT e.emoji, AVG(m.sentiment_score) AS avg_sent
            FROM messages m, LATERAL unnest(m.emojis) AS e(emoji)
            WHERE m.upload_id = :upload_id
              AND m.sentiment_score IS NOT NULL
              AND m.is_deleted = false
            GROUP BY e.emoji
            """
        )
        sent_rows = (
            await db.execute(sent_sql, {"upload_id": str(upload_id)})
        ).all()
        sentiment_by_emoji: dict[str, float] = {
            r.emoji: round(float(r.avg_sent), 4) for r in sent_rows if r.avg_sent is not None
        }

        total = sum(global_counts.values())
        items: list[EmojiFrequencyItem] = []
        for emoji_glyph, count in global_counts.most_common(top_n):
            per_sender = {
                s: per_sender_emoji[s].get(emoji_glyph, 0)
                for s in per_sender_emoji
                if per_sender_emoji[s].get(emoji_glyph, 0) > 0
            }
            items.append(
                EmojiFrequencyItem(
                    emoji=emoji_glyph,
                    count=count,
                    pct=count / total if total > 0 else 0.0,
                    unicode_name=_emoji_name(emoji_glyph),
                    per_sender=per_sender,
                    avg_sentiment=sentiment_by_emoji.get(emoji_glyph),
                )
            )

        # "Most unique to sender" — emojis where one sender accounts for
        # ≥70% of usage and the emoji has at least 5 total uses.
        unique_to_sender: list[UniqueEmojiUse] = []
        for emoji_glyph, count in global_counts.most_common():
            if count < 5:
                break
            for s, per_count in per_sender_emoji.items():
                ec = per_count.get(emoji_glyph, 0)
                share = ec / count if count else 0
                if share >= 0.7:
                    unique_to_sender.append(
                        UniqueEmojiUse(
                            emoji=emoji_glyph,
                            sender=s,
                            count=ec,
                            share=round(share, 4),
                            unicode_name=_emoji_name(emoji_glyph),
                        )
                    )
                    break  # emoji can be unique to at most one sender
            if len(unique_to_sender) >= 12:
                break

        return EmojiFrequency(
            upload_id=upload_id,
            sender=sender,
            total_emojis=total,
            items=items,
            unique_to_sender=unique_to_sender,
        )

    # ====================================================================
    # Word trends (single word over time)
    # ====================================================================

    async def _compute_word_trend(
        self, upload_id: UUID, db: AsyncSession, word: str
    ) -> WordTrend:
        # Use a regex word-boundary so "love" doesn't match "lovely".
        # We escape the word manually since it's user-supplied.
        sql = text(
            r"""
            SELECT
                (timestamp AT TIME ZONE 'UTC')::date AS day,
                count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
              AND is_deleted = false
              AND lower(content) ~ :pattern
            GROUP BY day
            ORDER BY day
            """
        )
        # Postgres POSIX regex: word boundary is `\m...\M`.
        pattern = rf"\m{re.escape(word)}\M"
        rows = (
            await db.execute(
                sql, {"upload_id": str(upload_id), "pattern": pattern}
            )
        ).all()
        points = [WordTrendPoint(date=r.day, count=int(r.n)) for r in rows]
        total = sum(p.count for p in points)
        return WordTrend(upload_id=upload_id, word=word, total_uses=total, points=points)

    # ====================================================================
    # Late-night messages (23:00 → 04:59 UTC)
    # ====================================================================

    async def _compute_late_night(
        self, upload_id: UUID, db: AsyncSession
    ) -> LateNightStats:
        # Total messages and per-sender counts in one round-trip.
        agg_sql = text(
            """
            WITH base AS (
                SELECT sender,
                       EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int AS hour
                FROM messages
                WHERE upload_id = :upload_id
            )
            SELECT
                (SELECT count(*) FROM base) AS total_all,
                (SELECT count(*) FROM base WHERE hour = ANY(:hours)) AS total_late
            """
        )
        params = {"upload_id": str(upload_id), "hours": list(_LATE_NIGHT_HOURS)}
        agg = (await db.execute(agg_sql, params)).one()
        total_all = int(agg.total_all or 0)
        total_late = int(agg.total_late or 0)
        if total_late == 0:
            return LateNightStats(upload_id=upload_id)

        # Per-sender counts.
        per_sender_sql = text(
            """
            SELECT sender, count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
              AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int = ANY(:hours)
            GROUP BY sender
            """
        )
        ps_rows = (await db.execute(per_sender_sql, params)).all()
        per_sender = {r.sender: int(r.n) for r in ps_rows}

        # Hour histogram across the late-night window.
        hour_sql = text(
            """
            SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int AS hour,
                   count(*) AS n
            FROM messages
            WHERE upload_id = :upload_id
              AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int = ANY(:hours)
            GROUP BY hour
            ORDER BY hour
            """
        )
        hr_rows = (await db.execute(hour_sql, params)).all()
        by_hour = [LateNightHourBucket(hour=int(r.hour), count=int(r.n)) for r in hr_rows]

        # Sample messages — pick the most emotionally extreme ones since
        # late-night messages are often the chat's most candid moments.
        sample_sql = text(
            """
            SELECT msg_id, sender, timestamp, content,
                   sentiment_score, sentiment_label, emotion_label, emotion_score
            FROM messages
            WHERE upload_id = :upload_id
              AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int = ANY(:hours)
              AND content <> '' AND is_deleted = false
            ORDER BY ABS(COALESCE(sentiment_score, 0)) DESC, timestamp DESC
            LIMIT :limit
            """
        )
        sample_rows = (
            await db.execute(
                sample_sql,
                {**params, "limit": _LATE_NIGHT_SAMPLES},
            )
        ).all()
        samples = [_to_sample(r) for r in sample_rows]

        return LateNightStats(
            upload_id=upload_id,
            total_late_night=total_late,
            pct_of_total=(total_late / total_all) if total_all else 0.0,
            per_sender=per_sender,
            by_hour=by_hour,
            sample_messages=samples,
        )


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class _Corpus:
    """Pre-built counters from a single corpus scan, shared between the
    word-frequency / bigrams / unique-words endpoints."""

    __slots__ = (
        "global_unigrams",
        "global_bigrams",
        "per_sender_unigrams",
        "per_sender_bigrams",
        "per_sender_total_tokens",
        "per_sender_unique",
        "global_unique",
    )

    def __init__(
        self,
        *,
        global_unigrams: Counter,
        global_bigrams: Counter,
        per_sender_unigrams: dict[str, Counter],
        per_sender_bigrams: dict[str, Counter],
        per_sender_total_tokens: dict[str, int],
        per_sender_unique: dict[str, set[str]],
        global_unique: set[str],
    ) -> None:
        self.global_unigrams = global_unigrams
        self.global_bigrams = global_bigrams
        self.per_sender_unigrams = per_sender_unigrams
        self.per_sender_bigrams = per_sender_bigrams
        self.per_sender_total_tokens = per_sender_total_tokens
        self.per_sender_unique = per_sender_unique
        self.global_unique = global_unique


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emoji_name(glyph: str) -> str | None:
    """Best-effort Unicode name. Multi-codepoint emoji (e.g. 👨‍👩‍👧) get the
    name of the first base codepoint, which is the convention emoji
    pickers use ("MAN" for the family glyph). Returns None when no
    codepoint has a name (rare; mostly private-use ranges)."""
    if not glyph:
        return None
    # Skin-tone modifiers / ZWJ joiners come after the base. The first
    # codepoint is typically the most informative.
    for ch in glyph:
        try:
            return unicodedata.name(ch)
        except ValueError:
            continue
    return None


def _to_sample(row) -> SampleMessage:
    """Convert a SQLAlchemy row with the standard columns into SampleMessage."""
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
        emotion_label=row.emotion_label,
        emotion_score=(
            float(row.emotion_score) if row.emotion_score is not None else None
        ),
    )


word_service = WordService()
