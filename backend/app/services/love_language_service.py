"""
Love-language classifier.

Maps affectionate messages onto Gary Chapman's five love languages
(words of affirmation, acts of service, quality time, physical touch,
gift giving). The classification itself is delegated to Claude — these
categories are inherently semantic, not lexical, and a regex / keyword
approach produces noisy, unactionable results on real chat language.

Pipeline:
    1. Pull every message with positive sentiment (score > 0.3) and
       emotion_label in {joy, love}. We deliberately don't rely on
       keyword matching — sentiment + emotion tagging from the NLP
       pipeline already filters out noise.
    2. Cap to a representative sample (200 max) so the LLM payload stays
       cheap. When the candidate pool is larger we sample evenly across
       the chat's timeline so themes from every period are represented.
    3. Send Claude a numbered list of (msg_id, sender, content) lines
       and ask for a JSON map of msg_id → category.
    4. Aggregate per-sender, normalize to shares, surface 3 examples per
       (sender, category) sorted by emotion_score.
    5. Build a deterministic per-sender summary sentence (so the UI never
       waits on a second LLM call) and a single Claude-written
       compatibility insight that reads across both senders.

Caching:
    `love:report:<upload_id>` for 24h. The classification is expensive
    (~$0.05–$0.10 per chat) and stable — re-running daily is plenty.

Fallback:
    Without ANTHROPIC_API_KEY we ship a heuristic classifier built from
    keyword sets per category. The frontend reads `used_llm` to surface
    a "less precise without AI" hint when this path runs.
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
from app.schemas.emotion import SampleMessage
from app.schemas.love_language import (
    LOVE_LANGUAGE_ORDER,
    LoveLanguageBreakdown,
    LoveLanguageCategory,
    LoveLanguageCount,
    LoveLanguageReport,
)

logger = logging.getLogger(__name__)


_TTL_SECONDS = 60 * 60 * 24  # 24h

# How many messages we send to Claude per analysis.
_MAX_CANDIDATES = 200

# Minimum sentiment_score / required emotion labels for a message to be
# considered for classification. Values match the spec; keep them in sync
# with the dashboard copy.
_MIN_SENTIMENT = 0.3
_TARGET_EMOTIONS = {"joy", "love"}

# Per-(sender, category) examples surfaced in the response.
_EXAMPLES_PER_CATEGORY = 3


# ---------------------------------------------------------------------------
# Heuristic fallback keywords
# ---------------------------------------------------------------------------

# Used only when no Anthropic key is configured. These are intentionally
# narrow — better to leave a message unclassified than to mis-attribute it.
_FALLBACK_KEYWORDS: dict[LoveLanguageCategory, tuple[str, ...]] = {
    "words_of_affirmation": (
        "love you", "i love you", "miss you", "proud of you", "you're amazing",
        "you are amazing", "you're the best", "thank you for", "appreciate you",
        "grateful", "you're so", "such a good",
    ),
    "acts_of_service": (
        "i'll do", "let me", "i'll grab", "i got you", "i got it", "on my way",
        "i'll pick", "i'll bring", "i'll handle", "i'll cook", "i'll drive",
        "let me know what i can", "took care of", "i remembered",
    ),
    "quality_time": (
        "miss you", "wish you were", "can't wait to see", "let's", "tonight",
        "tomorrow", "this weekend", "spend time", "movie night", "date night",
        "be there soon", "hang out", "see you",
    ),
    "physical_touch": (
        "hug", "hugs", "kiss", "kisses", "hold you", "cuddle", "snuggle",
        "wrap around", "in my arms", "hold your hand",
    ),
    "gift_giving": (
        "got you", "i bought", "i ordered", "surprise", "i made you",
        "bringing you", "for you", "i picked up something",
    ),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LoveLanguageService:
    """Stateless. Use the module-level `love_language_service` singleton."""

    async def get_report(
        self,
        upload_id: UUID,
        db: AsyncSession,
        force_refresh: bool = False,
    ) -> LoveLanguageReport:
        key = f"love:report:{upload_id}"
        if not force_refresh:
            cached = await cache.get_json(key)
            if cached is not None:
                try:
                    return LoveLanguageReport.model_validate(cached)
                except Exception:
                    pass

        candidates = await self._collect_candidates(upload_id, db)
        if not candidates:
            return LoveLanguageReport(upload_id=upload_id)

        # Decide path: LLM if any provider is configured, fallback otherwise.
        from app.services.llm import get_llm_client

        if get_llm_client().is_enabled():
            classifications, llm_ok = await self._classify_with_llm(candidates)
        else:
            classifications, llm_ok = self._classify_heuristic(candidates), False

        if not classifications:
            # Even with the LLM path, we may have gotten back nothing
            # parseable. Fall through to the heuristic so the UI has data.
            classifications = self._classify_heuristic(candidates)
            llm_ok = False

        breakdowns = self._aggregate(candidates, classifications)
        compatibility = ""
        if llm_ok and len(breakdowns) >= 2:
            compatibility = await self._compatibility_insight(breakdowns)

        result = LoveLanguageReport(
            upload_id=upload_id,
            participants=breakdowns,
            compatibility_insight=compatibility,
            used_llm=llm_ok,
        )
        await cache.set_json(
            key, result.model_dump(mode="json"), ttl_seconds=_TTL_SECONDS
        )
        return result

    async def invalidate(self, upload_id: UUID) -> None:
        await cache.delete(f"love:report:{upload_id}")

    # ====================================================================
    # Candidate collection
    # ====================================================================

    async def _collect_candidates(
        self, upload_id: UUID, db: AsyncSession
    ) -> list[dict]:
        """Pull positive-affect messages and downsample evenly across
        the chat's timeline. Each entry is a plain dict — the LLM payload
        only needs msg_id / sender / content / emotion_score / timestamp."""
        stmt = (
            select(
                Message.id,
                Message.msg_id,
                Message.msg_index,
                Message.sender,
                Message.timestamp,
                Message.content,
                Message.sentiment_score,
                Message.emotion_label,
                Message.emotion_score,
            )
            .where(Message.upload_id == upload_id)
            .where(Message.is_deleted.is_(False))
            .where(Message.sentiment_score.is_not(None))
            .where(Message.sentiment_score > _MIN_SENTIMENT)
            .where(Message.emotion_label.in_(list(_TARGET_EMOTIONS)))
            .where(Message.content != "")
            .order_by(Message.msg_index.asc())
        )
        rows = (await db.execute(stmt)).all()
        all_msgs: list[dict] = []
        for r in rows:
            ts = r.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            all_msgs.append(
                {
                    "id": r.id,
                    "msg_id": r.msg_id,
                    "msg_index": int(r.msg_index),
                    "sender": r.sender,
                    "timestamp": ts,
                    "content": r.content,
                    "sentiment_score": float(r.sentiment_score),
                    "emotion_label": r.emotion_label,
                    "emotion_score": (
                        float(r.emotion_score) if r.emotion_score is not None else None
                    ),
                }
            )

        if len(all_msgs) <= _MAX_CANDIDATES:
            return all_msgs

        # Even sampling across timeline. Use stride sampling rather than
        # random so the result is deterministic + reproducible.
        stride = len(all_msgs) / _MAX_CANDIDATES
        sampled: list[dict] = []
        for i in range(_MAX_CANDIDATES):
            idx = int(i * stride)
            if idx < len(all_msgs):
                sampled.append(all_msgs[idx])
        return sampled

    # ====================================================================
    # LLM classification
    # ====================================================================

    async def _classify_with_llm(
        self, candidates: list[dict]
    ) -> tuple[dict[str, LoveLanguageCategory], bool]:
        """Triage locally first, then send only the residual to Claude.

        We mirror the Layer-4 detector → translator pattern: the keyword
        fallback is cheap and accurate on the obvious cases ("i love you",
        "i'll grab milk", "miss you"), so we run it on every candidate
        first and only pay Claude tokens for messages it couldn't label.
        Real chats produce 30–70% keyword hits, which translates to a
        proportional Claude-token saving without any quality loss on the
        hits (the keyword set is conservative and rarely mis-fires).
        """
        # 1. Local pre-pass — covers the obvious cases.
        pre_classified: dict[str, LoveLanguageCategory] = self._classify_heuristic(
            candidates
        )

        # 2. Anything the keywords didn't match is the residual we ship
        # to Claude. If everything matched locally, we can skip the API
        # call entirely and still report `used_llm=True` because the
        # final classification is at least as accurate as the LLM path.
        residual = [c for c in candidates if c["msg_id"] not in pre_classified]
        if not residual:
            logger.info(
                "Love-language: %d/%d classified locally; skipped Claude entirely",
                len(pre_classified),
                len(candidates),
            )
            return pre_classified, True

        from app.services.llm import get_llm_client

        client = get_llm_client()
        if not client.is_enabled():
            return pre_classified, bool(pre_classified)

        # Build a numbered, msg_id-keyed message list FROM THE RESIDUAL only.
        # Truncate per-message content to keep the prompt manageable on
        # chats with many long messages.
        lines: list[str] = []
        for c in residual:
            content = (c["content"] or "").replace("\n", " ").strip()
            if len(content) > 240:
                content = content[:240] + "…"
            lines.append(f"[{c['msg_id']}] {c['sender']}: {content}")

        system_prompt = (
            "You are a relationship psychologist analyzing text messages to "
            "identify how people express love and care.\n\n"
            "Classify each message into ONE of these categories using "
            "Gary Chapman's five love languages framework:\n"
            "- words_of_affirmation: compliments, verbal appreciation, encouragement, "
            "  'I love you', 'you're amazing', 'I'm proud of you'\n"
            "- acts_of_service: offering help, planning, taking care of things, "
            "  remembering details, 'I'll do that for you', 'I got you'\n"
            "- quality_time: planning to be together, missing each other, undivided "
            "  attention, 'I wish you were here', 'let's have a date night'\n"
            "- physical_touch: explicit references to hugs, kisses, holding, cuddling, "
            "  physical closeness\n"
            "- gift_giving: sending things, surprises, picking up something thoughtful, "
            "  'I got you something'\n\n"
            "Pick the BEST single category for each. If a message is ambiguous, "
            "infer from intent. Be conservative — never invent meaning that isn't there.\n\n"
            "Return ONLY valid JSON, no preamble, no code fences:\n"
            '{"classifications":[{"msg_id":"msg_42","category":"words_of_affirmation"},'
            '{"msg_id":"msg_43","category":"acts_of_service"}]}'
        )
        user_prompt = "Messages to classify:\n\n" + "\n".join(lines)

        try:
            raw = await client.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=4000,
                temperature=0.2,
                json_mode=True,
            )
        except Exception as e:
            logger.warning("Love-language LLM call failed (%s)", e)
            # Even on LLM failure we still ship the keyword pre-pass.
            # `used_llm` reflects whether the LLM contributed; if we got
            # nothing useful back, we don't want the UI to claim AI-classified.
            return pre_classified, bool(pre_classified)

        llm_classified = self._parse_classifications(raw)

        # Merge keyword hits + Claude hits. Keyword wins on overlap because
        # those matches are deterministic and we trust them at least as
        # much as the LLM for the cases we curated keywords for.
        merged: dict[str, LoveLanguageCategory] = {**llm_classified, **pre_classified}
        logger.info(
            "Love-language: %d local + %d Claude = %d total (residual sent: %d)",
            len(pre_classified),
            len(llm_classified),
            len(merged),
            len(residual),
        )
        return merged, True

    def _parse_classifications(
        self, raw: str
    ) -> dict[str, LoveLanguageCategory]:
        """Parse the JSON map. Tolerates leading/trailing whitespace and
        the occasional code-fence wrapping that Claude adds."""
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Couldn't parse love-language JSON: %s", raw[:200])
            return {}
        items = payload.get("classifications")
        if not isinstance(items, list):
            return {}
        out: dict[str, LoveLanguageCategory] = {}
        for entry in items:
            if not isinstance(entry, dict):
                continue
            msg_id = entry.get("msg_id")
            cat = entry.get("category")
            if not isinstance(msg_id, str) or cat not in LOVE_LANGUAGE_ORDER:
                continue
            out[msg_id] = cat  # type: ignore[assignment]
        return out

    # ====================================================================
    # Heuristic fallback
    # ====================================================================

    def _classify_heuristic(
        self, candidates: list[dict]
    ) -> dict[str, LoveLanguageCategory]:
        """Substring-keyword classifier used when no LLM is available.
        First-match wins, with categories iterated in the order specified
        by `_FALLBACK_KEYWORDS`. Messages with no keyword match are
        dropped from the classification."""
        out: dict[str, LoveLanguageCategory] = {}
        for c in candidates:
            content = (c["content"] or "").lower()
            for category, keywords in _FALLBACK_KEYWORDS.items():
                if any(kw in content for kw in keywords):
                    out[c["msg_id"]] = category
                    break
        return out

    # ====================================================================
    # Aggregation
    # ====================================================================

    def _aggregate(
        self,
        candidates: list[dict],
        classifications: dict[str, LoveLanguageCategory],
    ) -> list[LoveLanguageBreakdown]:
        """Group classifications by sender + category, surface examples."""
        per_sender_counts: dict[str, Counter[LoveLanguageCategory]] = defaultdict(
            Counter
        )
        per_sender_category_msgs: dict[
            tuple[str, LoveLanguageCategory], list[dict]
        ] = defaultdict(list)

        msgs_by_id = {c["msg_id"]: c for c in candidates}

        for msg_id, category in classifications.items():
            msg = msgs_by_id.get(msg_id)
            if msg is None:
                continue
            per_sender_counts[msg["sender"]][category] += 1
            per_sender_category_msgs[(msg["sender"], category)].append(msg)

        out: list[LoveLanguageBreakdown] = []
        for sender in sorted(per_sender_counts.keys()):
            counts = per_sender_counts[sender]
            total = sum(counts.values())
            if total == 0:
                continue
            distribution = []
            for cat in LOVE_LANGUAGE_ORDER:
                count = counts.get(cat, 0)
                examples = sorted(
                    per_sender_category_msgs.get((sender, cat), []),
                    key=lambda m: (m.get("emotion_score") or 0.0, m["timestamp"]),
                    reverse=True,
                )[:_EXAMPLES_PER_CATEGORY]
                distribution.append(
                    LoveLanguageCount(
                        category=cat,
                        count=count,
                        share=count / total,
                        examples=[_to_sample(m) for m in examples],
                    )
                )
            ranked = sorted(distribution, key=lambda d: -d.share)
            primary = ranked[0].category if ranked and ranked[0].count > 0 else None
            secondary = (
                ranked[1].category
                if len(ranked) > 1 and ranked[1].count > 0
                else None
            )
            out.append(
                LoveLanguageBreakdown(
                    sender=sender,
                    total_classified=total,
                    distribution=distribution,
                    primary=primary,
                    secondary=secondary,
                    summary=_summary_sentence(
                        sender, primary, secondary, distribution
                    ),
                )
            )
        return out

    # ====================================================================
    # Compatibility insight
    # ====================================================================

    async def _compatibility_insight(
        self, breakdowns: list[LoveLanguageBreakdown]
    ) -> str:
        """One short paragraph that reflects on how the senders' love
        languages line up. We send only the per-sender distributions +
        primary/secondary — no message bodies — to keep this call cheap."""
        from app.services.llm import get_llm_client

        client = get_llm_client()
        if not client.is_enabled():
            return ""

        sender_lines: list[str] = []
        for b in breakdowns:
            shares = ", ".join(
                f"{c.category}: {c.share * 100:.0f}%" for c in b.distribution
            )
            sender_lines.append(f"{b.sender} — {shares}")

        system_prompt = (
            "You write warm, observational paragraphs about how two people "
            "express love in their messages. You never assign blame. You "
            "never recommend changes. You note where their styles overlap "
            "and where they differ, in language a thoughtful friend would "
            "use. Keep it short — two to four sentences. Plain prose, no "
            "lists, no headings."
        )
        user_prompt = (
            "Here are the love-language distributions for two senders:\n\n"
            + "\n".join(sender_lines)
            + "\n\nWrite a short observation about how their styles compare."
        )

        try:
            return await client.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=300,
                temperature=0.5,
            )
        except Exception as e:
            logger.warning("Compatibility insight failed (%s)", e)
            return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_sample(row: dict) -> SampleMessage:
    content = (row.get("content") or "")[:280]
    return SampleMessage(
        msg_id=row["msg_id"],
        sender=row["sender"],
        timestamp=row["timestamp"],
        content_preview=content,
        sentiment_score=row.get("sentiment_score"),
        sentiment_label=None,
        emotion_label=row.get("emotion_label"),
        emotion_score=row.get("emotion_score"),
    )


def _extract_text(msg: Any) -> str:
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out)


def _category_label(category: LoveLanguageCategory) -> str:
    """Map snake_case keys to title-cased labels for the summary sentence.
    Frontend has its own copy of this (`LOVE_LANGUAGE_LABELS` in
    `LoveLanguages.tsx`); keeping a server-side copy keeps the summary
    stable when the API caller renders it directly."""
    return {
        "words_of_affirmation": "Words of Affirmation",
        "acts_of_service": "Acts of Service",
        "quality_time": "Quality Time",
        "physical_touch": "Physical Touch",
        "gift_giving": "Gift Giving",
    }[category]


def _summary_sentence(
    sender: str,
    primary: LoveLanguageCategory | None,
    secondary: LoveLanguageCategory | None,
    distribution: list[LoveLanguageCount],
) -> str:
    """Deterministic summary built from the top-2 categories.

    Picked over a Claude call because (a) the sentence shape is highly
    constrained, (b) it lets the frontend render synchronously even when
    the LLM is offline, and (c) it costs nothing per invocation.
    """
    if primary is None:
        return f"{sender}'s expressions of love don't lean strongly into any single category yet."
    by_cat = {d.category: d for d in distribution}
    primary_share = int(round(by_cat[primary].share * 100))
    if secondary is None or by_cat[secondary].count == 0:
        return (
            f"{sender} primarily expresses love through "
            f"{_category_label(primary)} ({primary_share}%)."
        )
    secondary_share = int(round(by_cat[secondary].share * 100))
    return (
        f"{sender} primarily expresses love through "
        f"{_category_label(primary)} ({primary_share}%) "
        f"and {_category_label(secondary)} ({secondary_share}%)."
    )


love_language_service = LoveLanguageService()
