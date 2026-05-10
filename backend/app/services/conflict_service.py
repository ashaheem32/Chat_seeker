"""
Conflict-analysis service.

Detects "difficult-moment windows" — periods of unusually negative or
escalated communication — and characterizes them: who triggered, how it
ended, what was said, and (via Claude) what recurring themes those windows
cluster into.

Detection algorithm:
    1. Walk the chat in chronological order.
    2. For each message compute a 3-message rolling-average sentiment
       (centered on the message; missing scores are skipped, not treated
       as zero, so a single un-classified message doesn't drag the window).
    3. Mark a message as a "conflict indicator" if any of:
         - rolling-avg sentiment <  -0.3
         - emotion_label == 'anger' AND emotion_score > 0.6
         - the content matches a conflict-keyword regex
       (Configurable via `CONFLICT_NEG_THRESHOLD`, `ANGER_THRESHOLD`.)
    4. Group consecutive indicator messages into runs. Runs of ≥3
       messages are considered windows; shorter blips are dropped.
    5. For each window, materialize:
         - trigger message    (the first indicator-flagging message)
         - peak negativity    (min sentiment_score in the window)
         - top words          (non-stopword tokens, capped at 8)
         - sample messages    (5: trigger, peak, last + 2 emotion-extreme)
         - sentiment_arc      (sentiments for window±5 messages, for the chart)
         - resolution         (apology / topic_change / time_gap / unresolved)

Resolution detection:
    - apology       — within 5 messages after the window, an apology phrase
                      appears ("sorry", "my fault", "didn't mean", ...).
    - time_gap      — first message after the window is >= 12h later
                      (we treat overnight silence as a recovery signal).
    - topic_change  — within 20 messages after, rolling-avg sentiment
                      crosses back above +0.2 for ≥3 consecutive messages.
    - unresolved    — none of the above.

Theme clustering:
    Sends the trigger messages of all detected windows to Claude and asks
    it to bucket them into 3-5 recurring themes. Returns label +
    description + window-id membership. Falls back to a single
    "Unclustered" theme when Anthropic is unavailable so the UI never
    breaks. The Claude call is cached separately from `detect_conflicts`
    because it costs money and changes rarely.

Caching:
    `conflict:analysis:<id>` for the detection output (1h TTL).
    `conflict:themes:<id>` for the clustering result (24h TTL — themes
    barely move week-to-week and re-running the LLM call is the costliest
    operation in the module).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.models import Message
from app.schemas.conflict import (
    ConflictAnalysisResponse,
    ConflictLanguage,
    ConflictSummary,
    ConflictTheme,
    ConflictThemesResponse,
    ConflictWindow,
    DayOfWeekCount,
    HourCount,
    MonthCount,
    ResolutionInfo,
    ResolutionType,
    SenderRole,
    WordCount,
)
from app.schemas.emotion import SampleMessage
from app.services.word_service import STOPWORDS, _WORD_RE  # reuse tokenizer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Detection thresholds.
CONFLICT_NEG_THRESHOLD = -0.3    # rolling-avg sentiment cutoff
ANGER_THRESHOLD = 0.6            # emotion_score floor for "anger" trigger
ROLLING_WINDOW = 3               # 3-message centered rolling average
MIN_WINDOW_SIZE = 3              # consecutive indicators required to qualify

# Resolution-detection lookahead.
APOLOGY_LOOKAHEAD = 5
TOPIC_LOOKAHEAD = 20
TIME_GAP_HOURS = 12

# Output caps.
TOP_WORDS_PER_WINDOW = 8
SAMPLE_MESSAGES_PER_WINDOW = 5

# Cache TTLs.
_ANALYSIS_TTL_SECONDS = 60 * 60         # 1h
_THEMES_TTL_SECONDS = 24 * 60 * 60      # 24h

# Trigger keyword set — per spec. Word boundary matched, case-insensitive.
CONFLICT_KEYWORDS: tuple[str, ...] = (
    "fight", "angry", "upset", "hate", "stop", "leave",
    "fine", "whatever", "sorry", "fault", "blame",
    "always", "never", "serious", "enough",
)
_CONFLICT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in CONFLICT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Apology phrases that signal "resolution: apology". Multi-word patterns
# (e.g. "my fault") are listed verbatim and matched as substrings (still
# case-insensitive, still word-boundary on the outside) since "\b" alone
# doesn't span spaces.
_APOLOGY_PATTERNS: tuple[str, ...] = (
    r"\bsorry\b",
    r"\bapolog\w*\b",          # apologize / apology / apologies
    r"\bmy fault\b",
    r"\bmy bad\b",
    r"\bdidn'?t mean\b",
    r"\bregret\b",
    r"\bforgive\b",
    r"\bi was wrong\b",
)
_APOLOGY_RE = re.compile("|".join(_APOLOGY_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ConflictService:
    """Stateless. Use the module-level `conflict_service` singleton."""

    # ====================================================================
    # Public API
    # ====================================================================

    async def get_conflict_analysis(
        self,
        upload_id: UUID,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> ConflictAnalysisResponse:
        """Compute the full conflict-analysis payload (windows + summary +
        language). Themes are NOT included here — fetch them via
        `get_conflict_themes` so the dashboard doesn't block on Claude.
        Cached entries already include themes if a prior themes call
        populated the joint cache; we expose them when present."""
        key = f"conflict:analysis:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return ConflictAnalysisResponse.model_validate(cached)
                except Exception:
                    pass

        windows = await self._detect_conflicts(upload_id, db)
        summary = self._build_summary(windows)
        language = await self._compute_language(upload_id, db, windows)

        # Do we already have cached themes? Fold them in if so.
        themes: list[ConflictTheme] | None = None
        themes_cached = await cache.get_json(f"conflict:themes:{upload_id}")
        if themes_cached is not None:
            try:
                themes = [ConflictTheme.model_validate(t) for t in themes_cached]
            except Exception:
                themes = None

        result = ConflictAnalysisResponse(
            upload_id=upload_id,
            summary=summary,
            windows=windows,
            language=language,
            themes=themes,
        )
        await cache.set_json(
            key, result.model_dump(mode="json"), ttl_seconds=_ANALYSIS_TTL_SECONDS
        )
        return result

    async def get_conflict_themes(
        self,
        upload_id: UUID,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> ConflictThemesResponse:
        """Cluster the detected conflict windows into recurring themes
        via Claude. Cached for 24h since the result barely changes."""
        key = f"conflict:themes:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return ConflictThemesResponse(
                        upload_id=upload_id,
                        themes=[ConflictTheme.model_validate(t) for t in cached],
                        used_llm=True,
                    )
                except Exception:
                    pass

        windows = await self._detect_conflicts(upload_id, db)
        if not windows:
            return ConflictThemesResponse(upload_id=upload_id, themes=[], used_llm=False)

        themes, used_llm = await self._cluster_themes(windows)
        # Persist as plain list so the analysis endpoint can pick them up.
        await cache.set_json(
            key,
            [t.model_dump(mode="json") for t in themes],
            ttl_seconds=_THEMES_TTL_SECONDS,
        )
        return ConflictThemesResponse(
            upload_id=upload_id, themes=themes, used_llm=used_llm
        )

    async def invalidate(self, upload_id: UUID) -> None:
        await cache.delete_pattern(f"conflict:*:{upload_id}*")

    # ====================================================================
    # Detection
    # ====================================================================

    async def _detect_conflicts(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[ConflictWindow]:
        """Run the full detection pipeline.

        Single ordered scan over messages. We materialize the entire
        message list to compute rolling averages + sentiment_arc; this
        is O(n) memory with small per-row payloads (~200B). For the
        chat sizes ChatLens targets (≤50k messages) that's ~10MB peak."""
        rows = await self._load_messages(upload_id, db)
        if len(rows) < MIN_WINDOW_SIZE:
            return []

        # Pre-compute rolling-average sentiment, skipping None scores.
        rolling = _rolling_sentiment(rows, ROLLING_WINDOW)

        # Mark each message as an indicator.
        indicators: list[bool] = []
        for i, m in enumerate(rows):
            roll = rolling[i]
            is_neg = roll is not None and roll < CONFLICT_NEG_THRESHOLD
            is_anger = (
                m["emotion_label"] == "anger"
                and (m["emotion_score"] or 0.0) > ANGER_THRESHOLD
            )
            is_keyword = bool(_CONFLICT_RE.search(m["content"] or ""))
            indicators.append(is_neg or is_anger or is_keyword)

        # Find runs.
        windows: list[ConflictWindow] = []
        i = 0
        n = len(rows)
        while i < n:
            if not indicators[i]:
                i += 1
                continue
            start = i
            while i < n and indicators[i]:
                i += 1
            end = i - 1
            if end - start + 1 < MIN_WINDOW_SIZE:
                continue

            window = self._materialize_window(rows, rolling, start, end, indicators)
            if window is not None:
                windows.append(window)

        return windows

    def _materialize_window(
        self,
        rows: list[dict],
        rolling: list[float | None],
        start: int,
        end: int,
        indicators: list[bool],
    ) -> ConflictWindow | None:
        """Build a fully-populated ConflictWindow from a [start, end] run."""
        run = rows[start : end + 1]
        if not run:
            return None

        first = run[0]
        last = run[-1]

        # Trigger: the first message that triggered an indicator. In most
        # runs that's `first`; in edge cases (e.g. anger that started just
        # before the rolling-avg dipped) we still pick the first run member
        # since we're scoping to messages inside the window.
        trigger = first
        peak_score = min(
            (m["sentiment_score"] for m in run if m["sentiment_score"] is not None),
            default=CONFLICT_NEG_THRESHOLD,
        )

        # Sample messages: trigger + peak-negativity + last + up to 2 more
        # emotion-extreme rows. Deduped by msg_id while preserving order so
        # the UI gets a story arc, not just the lowest dips.
        peak_msg = min(
            run,
            key=lambda m: (m["sentiment_score"] if m["sentiment_score"] is not None else 1.0),
        )
        emo_sorted = sorted(
            run,
            key=lambda m: (m["emotion_score"] or 0.0),
            reverse=True,
        )
        candidates = [trigger, peak_msg, *emo_sorted[:2], last]
        seen: set[str] = set()
        sample_messages: list[SampleMessage] = []
        for m in candidates:
            if m["msg_id"] in seen:
                continue
            seen.add(m["msg_id"])
            sample_messages.append(_to_sample(m))
            if len(sample_messages) >= SAMPLE_MESSAGES_PER_WINDOW:
                break

        # Top words across the window content.
        counter: Counter[str] = Counter()
        for m in run:
            for tok in _WORD_RE.findall((m["content"] or "").lower()):
                if tok in STOPWORDS:
                    continue
                counter[tok] += 1
        top_words = [w for w, _ in counter.most_common(TOP_WORDS_PER_WINDOW)]

        # Sentiment arc — pull window±5 surrounding messages so the chart
        # shows the dip in context. None values stay as None so the chart
        # can render visible gaps.
        arc_start = max(0, start - 5)
        arc_end = min(len(rows) - 1, end + 5)
        sentiment_arc = [rows[j]["sentiment_score"] for j in range(arc_start, arc_end + 1)]

        # Resolution detection.
        resolution = self._detect_resolution(rows, end, rolling)

        duration_minutes = max(
            0,
            int((last["timestamp"] - first["timestamp"]).total_seconds() // 60),
        )

        return ConflictWindow(
            window_id=f"w_{first['msg_id']}_{last['msg_id']}",
            start_msg_id=first["msg_id"],
            end_msg_id=last["msg_id"],
            start_db_id=first["id"],
            end_db_id=last["id"],
            start_timestamp=first["timestamp"],
            end_timestamp=last["timestamp"],
            duration_minutes=duration_minutes,
            duration_messages=len(run),
            peak_negativity_score=round(float(peak_score), 4),
            trigger_sender=trigger["sender"],
            trigger_message=_to_sample(trigger),
            resolution=resolution,
            top_words=top_words,
            sample_messages=sample_messages,
            sentiment_arc=sentiment_arc,
        )

    def _detect_resolution(
        self,
        rows: list[dict],
        window_end: int,
        rolling: list[float | None],
    ) -> ResolutionInfo:
        """Look for apology / topic_change / time_gap / unresolved, in that
        priority order. We deliberately check apology FIRST because it's
        the most explicit signal — if both an apology and a 12h gap
        appear, we want the apology surfaced."""
        n = len(rows)
        if window_end + 1 >= n:
            return ResolutionInfo(type="unresolved")

        last_in_window = rows[window_end]
        end_ts: datetime = last_in_window["timestamp"]

        # Apology — within APOLOGY_LOOKAHEAD messages.
        for j in range(window_end + 1, min(n, window_end + 1 + APOLOGY_LOOKAHEAD)):
            m = rows[j]
            if _APOLOGY_RE.search(m["content"] or ""):
                delta_min = max(0, int((m["timestamp"] - end_ts).total_seconds() // 60))
                return ResolutionInfo(
                    type="apology",
                    sender=m["sender"],
                    message=_to_sample(m),
                    minutes_after_window=delta_min,
                )

        # Time gap — first message after window is >= TIME_GAP_HOURS later.
        next_msg = rows[window_end + 1]
        gap_seconds = (next_msg["timestamp"] - end_ts).total_seconds()
        if gap_seconds >= TIME_GAP_HOURS * 3600:
            return ResolutionInfo(
                type="time_gap",
                sender=next_msg["sender"],
                message=_to_sample(next_msg),
                minutes_after_window=int(gap_seconds // 60),
            )

        # Topic change — rolling avg returns positive within TOPIC_LOOKAHEAD.
        # We use the same rolling buffer we computed for detection. We
        # require ≥3 consecutive points above +0.2 to avoid false positives
        # on a single up-and-down.
        scan_end = min(n, window_end + 1 + TOPIC_LOOKAHEAD)
        consecutive = 0
        for j in range(window_end + 1, scan_end):
            roll = rolling[j]
            if roll is not None and roll > 0.2:
                consecutive += 1
                if consecutive >= 3:
                    # Resolution attribution: the first message in the
                    # 3-message run that pulled it positive.
                    target = rows[j - 2]
                    delta_min = max(0, int((target["timestamp"] - end_ts).total_seconds() // 60))
                    return ResolutionInfo(
                        type="topic_change",
                        sender=target["sender"],
                        message=_to_sample(target),
                        minutes_after_window=delta_min,
                    )
            else:
                consecutive = 0

        return ResolutionInfo(type="unresolved")

    # ====================================================================
    # Summary
    # ====================================================================

    def _build_summary(self, windows: list[ConflictWindow]) -> ConflictSummary:
        if not windows:
            return ConflictSummary()

        durations_h = [w.duration_minutes / 60.0 for w in windows]
        avg_duration = sum(durations_h) / len(durations_h)

        # Recovery time — only over conflicts that actually resolved.
        recovery = [
            w.resolution.minutes_after_window / 60.0
            for w in windows
            if w.resolution.type != "unresolved"
            and w.resolution.minutes_after_window is not None
        ]
        avg_recovery_h = sum(recovery) / len(recovery) if recovery else None

        # Most common triggers — surface the conflict-keyword that appeared
        # in each trigger message. (Falls back to the trigger's top word
        # when no keyword fired.)
        trigger_counter: Counter[str] = Counter()
        for w in windows:
            content = (
                (w.trigger_message.content_preview if w.trigger_message else "") or ""
            )
            keywords = [
                m.group(1).lower() for m in _CONFLICT_RE.finditer(content)
            ]
            if keywords:
                trigger_counter.update(keywords)
            elif w.top_words:
                trigger_counter.update([w.top_words[0]])
        most_common_triggers = [w for w, _ in trigger_counter.most_common(5)]

        # Who escalates / resolves more.
        escalators: Counter[str] = Counter()
        resolvers: Counter[str] = Counter()
        for w in windows:
            if w.trigger_sender:
                escalators[w.trigger_sender] += 1
            if (
                w.resolution.type != "unresolved"
                and w.resolution.sender is not None
            ):
                resolvers[w.resolution.sender] += 1

        who_escalates = _top_role(escalators)
        who_resolves = _top_role(resolvers)

        # Frequency by month.
        per_month: Counter[str] = Counter()
        for w in windows:
            per_month[w.start_timestamp.strftime("%Y-%m")] += 1
        freq_by_month = [
            MonthCount(month=m, count=n)
            for m, n in sorted(per_month.items())
        ]

        return ConflictSummary(
            total_conflicts_detected=len(windows),
            avg_duration_hours=round(avg_duration, 2),
            avg_recovery_time_hours=(
                round(avg_recovery_h, 2) if avg_recovery_h is not None else None
            ),
            most_common_triggers=most_common_triggers,
            who_escalates_more=who_escalates,
            who_resolves_more=who_resolves,
            conflict_frequency_by_month=freq_by_month,
        )

    # ====================================================================
    # Language + time patterns
    # ====================================================================

    async def _compute_language(
        self,
        upload_id: UUID,
        db: AsyncSession,
        windows: list[ConflictWindow],
    ) -> ConflictLanguage:
        """Word frequencies in conflict text vs resolution text, plus
        time-of-day / day-of-week distributions of when conflicts start."""
        if not windows:
            return ConflictLanguage()

        # We already have the windows + their resolutions. Re-fetch only
        # the content of the messages spanning each window + the next 5
        # messages after (the resolution lookahead). One IN-list query.
        ids: set[UUID] = set()
        for w in windows:
            ids.add(w.start_db_id)
            ids.add(w.end_db_id)
        # We need full messages by msg_index range — easier than building
        # a set of every UUID in every window. Instead pull all upload
        # messages once and index in Python.
        rows = await self._load_messages(upload_id, db)
        index_by_msg_id = {m["msg_id"]: i for i, m in enumerate(rows)}

        conflict_words: Counter[str] = Counter()
        resolution_words: Counter[str] = Counter()
        hour_counter: Counter[int] = Counter()
        dow_counter: Counter[int] = Counter()

        for w in windows:
            start_idx = index_by_msg_id.get(w.start_msg_id)
            end_idx = index_by_msg_id.get(w.end_msg_id)
            if start_idx is None or end_idx is None:
                continue

            # In-window words.
            for j in range(start_idx, end_idx + 1):
                _accumulate_words(rows[j]["content"], conflict_words)

            # Resolution words: the 5 messages right after.
            for j in range(end_idx + 1, min(len(rows), end_idx + 1 + APOLOGY_LOOKAHEAD)):
                _accumulate_words(rows[j]["content"], resolution_words)

            # Hour / DOW of the trigger.
            ts: datetime = w.start_timestamp
            hour_counter[ts.hour] += 1
            dow_counter[(ts.weekday())] += 1  # 0=Monday

        return ConflictLanguage(
            conflict_words=[
                WordCount(word=w, count=n)
                for w, n in conflict_words.most_common(40)
            ],
            resolution_words=[
                WordCount(word=w, count=n)
                for w, n in resolution_words.most_common(40)
            ],
            hour_distribution=[
                HourCount(hour=h, count=hour_counter.get(h, 0)) for h in range(24)
            ],
            dow_distribution=[
                DayOfWeekCount(dow=d, count=dow_counter.get(d, 0)) for d in range(7)
            ],
        )

    # ====================================================================
    # Theme clustering (Claude)
    # ====================================================================

    async def _cluster_themes(
        self, windows: list[ConflictWindow]
    ) -> tuple[list[ConflictTheme], bool]:
        """Ask Claude to group the windows by trigger theme. Returns
        `(themes, used_llm)`. Falls back to a single "Unclustered" theme
        when no Anthropic key is configured or the call fails."""
        if not windows:
            return [], False

        from app.services.llm import get_llm_client

        client = get_llm_client()
        if not client.is_enabled():
            return self._fallback_themes(windows), False

        # Build a numbered list of trigger messages (one per window).
        # Cap at 30 windows to keep the prompt cheap; if there are more,
        # pick a representative spread by sentiment.
        if len(windows) > 30:
            sample = sorted(windows, key=lambda w: w.peak_negativity_score)[:30]
        else:
            sample = list(windows)
        index_to_window: dict[int, ConflictWindow] = {
            i: w for i, w in enumerate(sample)
        }

        lines: list[str] = []
        for i, w in index_to_window.items():
            tm = w.trigger_message
            preview = (tm.content_preview if tm else "") or ""
            sender = tm.sender if tm else (w.trigger_sender or "?")
            lines.append(f"[{i}] {sender}: {preview[:240]}")

        system_prompt = (
            "You are analyzing trigger messages from difficult moments in a "
            "personal chat. Group them into 3–5 recurring themes that "
            "capture WHAT each conflict is about — not how anyone behaved, "
            "and never assigning blame. Use neutral, observational labels: "
            "'response time frustration', 'making plans', 'feeling unheard', "
            "'jealousy or trust', 'household logistics', etc.\n\n"
            "Return ONLY valid JSON of the form:\n"
            '{"themes":[{"label":"...","description":"...","example_indices":[0,3,7]}]}\n'
            "Indices are the [N] tokens before each message above. Every "
            "trigger message MUST belong to exactly one theme."
        )
        user_prompt = (
            f"Trigger messages from {len(sample)} difficult moments:\n\n"
            + "\n".join(lines)
        )

        try:
            raw = await client.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=900,
                temperature=0.4,
                json_mode=True,
            )
        except Exception as e:
            logger.warning("LLM theme clustering failed (%s); using fallback", e)
            return self._fallback_themes(windows), False

        themes = self._parse_themes(raw, index_to_window)
        if not themes:
            return self._fallback_themes(windows), False
        return themes, True

    def _parse_themes(
        self,
        raw: str,
        index_to_window: dict[int, ConflictWindow],
    ) -> list[ConflictTheme]:
        """Parse the JSON-shaped response into typed themes."""
        # Claude sometimes wraps JSON in code fences. Strip them.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Couldn't parse theme JSON: %s", raw[:200])
            return []
        themes_raw = payload.get("themes")
        if not isinstance(themes_raw, list):
            return []

        out: list[ConflictTheme] = []
        for entry in themes_raw:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label", "")).strip()[:80]
            if not label:
                continue
            description = str(entry.get("description", "")).strip()[:240]
            example_indices = entry.get("example_indices")
            if not isinstance(example_indices, list):
                continue
            window_ids: list[str] = []
            example_messages: list[SampleMessage] = []
            sentiments: list[float] = []
            for idx in example_indices:
                try:
                    i = int(idx)
                except (TypeError, ValueError):
                    continue
                w = index_to_window.get(i)
                if w is None:
                    continue
                window_ids.append(w.window_id)
                if w.trigger_message and len(example_messages) < 3:
                    example_messages.append(w.trigger_message)
                if w.peak_negativity_score is not None:
                    sentiments.append(w.peak_negativity_score)
            if not window_ids:
                continue
            avg_sentiment = (
                round(sum(sentiments) / len(sentiments), 4)
                if sentiments
                else None
            )
            out.append(
                ConflictTheme(
                    label=label,
                    description=description,
                    frequency=len(window_ids),
                    window_ids=window_ids,
                    avg_sentiment=avg_sentiment,
                    example_messages=example_messages,
                )
            )
        return out

    def _fallback_themes(self, windows: list[ConflictWindow]) -> list[ConflictTheme]:
        """Single bucket when Claude isn't available. Uses the most-common
        trigger keyword as the label so the UI shows *something* meaningful
        rather than "Unclustered"."""
        keyword_counter: Counter[str] = Counter()
        for w in windows:
            content = w.trigger_message.content_preview if w.trigger_message else ""
            for m in _CONFLICT_RE.finditer(content or ""):
                keyword_counter[m.group(1).lower()] += 1
        label = "Recurring tension"
        if keyword_counter:
            top_kw = keyword_counter.most_common(1)[0][0]
            label = f"Around '{top_kw}'"
        sentiments = [
            w.peak_negativity_score
            for w in windows
            if w.peak_negativity_score is not None
        ]
        avg = round(sum(sentiments) / len(sentiments), 4) if sentiments else None
        return [
            ConflictTheme(
                label=label,
                description=(
                    "AI clustering is unavailable in this environment, so all "
                    "difficult moments are grouped together. Configure a valid "
                    "OPENAI_API_KEY (or ANTHROPIC_API_KEY) to surface specific themes."
                ),
                frequency=len(windows),
                window_ids=[w.window_id for w in windows],
                avg_sentiment=avg,
                example_messages=[
                    w.trigger_message for w in windows[:3] if w.trigger_message
                ],
            )
        ]

    # ====================================================================
    # Helpers — IO
    # ====================================================================

    async def _load_messages(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[dict]:
        """Pull every message we need for the analysis as plain dicts.
        Plain dicts (not ORM objects) keep peak memory predictable and
        make the rolling-window math straightforward."""
        stmt = (
            select(
                Message.id,
                Message.msg_id,
                Message.msg_index,
                Message.sender,
                Message.timestamp,
                Message.content,
                Message.is_deleted,
                Message.sentiment_score,
                Message.sentiment_label,
                Message.emotion_label,
                Message.emotion_score,
            )
            .where(Message.upload_id == upload_id)
            .order_by(Message.msg_index.asc())
        )
        rows = (await db.execute(stmt)).all()
        out: list[dict] = []
        for r in rows:
            if r.is_deleted:
                # Skip deleted-placeholder rows; they distort sentiment averages.
                continue
            ts = r.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append(
                {
                    "id": r.id,
                    "msg_id": r.msg_id,
                    "msg_index": int(r.msg_index),
                    "sender": r.sender,
                    "timestamp": ts,
                    "content": r.content or "",
                    "sentiment_score": (
                        float(r.sentiment_score) if r.sentiment_score is not None else None
                    ),
                    "sentiment_label": r.sentiment_label,
                    "emotion_label": r.emotion_label,
                    "emotion_score": (
                        float(r.emotion_score) if r.emotion_score is not None else None
                    ),
                }
            )
        return out


# ---------------------------------------------------------------------------
# Helpers (module-level)
# ---------------------------------------------------------------------------


def _rolling_sentiment(rows: list[dict], window: int) -> list[float | None]:
    """Centered rolling average of sentiment_score, skipping None entries.

    Centered (not trailing) so the indicator at message i reflects what
    we'd intuitively call "the mood around message i" — context on both
    sides. Edge messages use whatever neighbors exist, accepting the
    truncated window."""
    half = window // 2
    out: list[float | None] = []
    n = len(rows)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n - 1, i + half)
        scores = [
            rows[j]["sentiment_score"]
            for j in range(lo, hi + 1)
            if rows[j]["sentiment_score"] is not None
        ]
        out.append(sum(scores) / len(scores) if scores else None)
    return out


def _accumulate_words(content: str | None, counter: Counter[str]) -> None:
    if not content:
        return
    for tok in _WORD_RE.findall(content.lower()):
        if tok in STOPWORDS:
            continue
        counter[tok] += 1


def _top_role(counter: Counter[str]) -> SenderRole | None:
    """Pick the leader from a sender-role counter, with the total for
    context. Returns None on an empty counter (no qualifying conflicts)."""
    if not counter:
        return None
    sender, count = counter.most_common(1)[0]
    total = sum(counter.values())
    return SenderRole(sender=sender, count=count, total=total)


def _to_sample(row: dict) -> SampleMessage:
    """Lightweight conversion from a row-dict to a SampleMessage."""
    content = (row.get("content") or "")[:280]
    return SampleMessage(
        msg_id=row["msg_id"],
        sender=row["sender"],
        timestamp=row["timestamp"],
        content_preview=content,
        sentiment_score=row.get("sentiment_score"),
        sentiment_label=row.get("sentiment_label"),
        emotion_label=row.get("emotion_label"),
        emotion_score=row.get("emotion_score"),
    )


def _extract_response_text(msg: Any) -> str:
    """Concatenate text content blocks from an Anthropic response."""
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


conflict_service = ConflictService()
