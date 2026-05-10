"""
UCJ builder.

Takes a `UCJResult` (parsed messages + platform name) and produces a
fully-populated `UCJFile` pydantic model with computed `meta.stats` and
`meta.date_range`.

The builder is the single source of truth for stat computation - parsers
focus on extracting raw fields, the builder turns those into aggregates.
This split makes parsers easier to test (no math involved) and keeps the
stat schema in one place.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from app.schemas.ucj import (
    AIAnalysis,
    ChatMeta,
    ChatStats,
    DateRange,
    Message as UCJMessageSchema,
    MessageMetadata as UCJMetadataSchema,
    UCJFile,
)
from app.services.parser.base import UCJMessage, UCJResult

logger = logging.getLogger(__name__)


class UCJBuilder:
    """Stateless aggregator. Public API is the single `build` classmethod."""

    @classmethod
    def build(
        cls,
        *,
        result: UCJResult,
        source_filename: str,
        ai_analysis: AIAnalysis | None = None,
    ) -> UCJFile:
        """Convert a UCJResult into a validated UCJFile.

        Args:
            result: Output of a ChatParser.parse() call.
            source_filename: The original uploaded filename. Surfaces in the
                UI so users can identify uploads.
            ai_analysis: Optional pre-computed analysis to embed. Usually
                None at upload time; populated by a Celery task later.
        """
        messages = result.messages
        # Defensive sort - parsers should already sort, but normalizing here
        # means downstream code can always trust ordering.
        messages_sorted = sorted(messages, key=lambda m: m.timestamp)

        participants = cls._collect_participants(messages_sorted, hint=result.participants_hint)
        date_range = cls._compute_date_range(messages_sorted)
        stats = cls._compute_stats(messages_sorted)

        meta = ChatMeta(
            source_platform=result.source_platform,
            source_file=source_filename,
            exported_at=datetime.now(timezone.utc),
            participants=participants,
            total_messages=len(messages_sorted),
            date_range=date_range,
            stats=stats,
            ai_analysis=ai_analysis,
        )

        return UCJFile(
            ucj_version="1.0",
            meta=meta,
            messages=[cls._to_pydantic(m) for m in messages_sorted],
        )

    # ------------------------------------------------------------------
    # Aggregate computations
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_participants(
        messages: list[UCJMessage], *, hint: list[str] | None = None
    ) -> list[str]:
        """Unique senders, in order of first appearance.

        Falls back to (or augments with) the hint from the parser - useful
        when participants are listed in the export header but never appear
        as senders (e.g. someone added to a group but who never spoke).
        """
        seen: dict[str, None] = {}  # dict preserves insertion order
        for m in messages:
            # System events use a sentinel sender we don't surface.
            if m.type == "system" and m.sender == "system":
                continue
            if m.sender not in seen:
                seen[m.sender] = None
        if hint:
            for p in hint:
                if p not in seen:
                    seen[p] = None
        return list(seen.keys())

    @staticmethod
    def _compute_date_range(messages: list[UCJMessage]) -> DateRange:
        """First and last message timestamps + span in days.

        For empty chats we synthesize a zero-span range at "now" so the
        schema stays well-formed.
        """
        if not messages:
            now = datetime.now(timezone.utc)
            return DateRange(start=now, end=now, span_days=0)

        start = messages[0].timestamp
        end = messages[-1].timestamp
        # Ceil-style span: a chat that spans 1.x days reads as 1 day, but
        # any nonzero gap is at least 1 day for UI display. .days gives us
        # whole-day floor which is what most users expect.
        span_days = max((end - start).days, 0)
        return DateRange(start=start, end=end, span_days=span_days)

    @classmethod
    def _compute_stats(cls, messages: list[UCJMessage]) -> ChatStats:
        if not messages:
            return ChatStats()

        total_words = 0
        total_chars = 0
        total_emojis = 0
        total_media = 0

        per_sender_messages: Counter[str] = Counter()
        per_sender_words: Counter[str] = Counter()
        per_sender_chars: Counter[str] = Counter()

        non_empty_lengths: list[int] = []

        for m in messages:
            md = m.metadata
            total_words += md.word_count
            total_chars += md.char_count
            total_emojis += len(md.emojis)
            if md.has_media:
                total_media += 1

            per_sender_messages[m.sender] += 1
            per_sender_words[m.sender] += md.word_count
            per_sender_chars[m.sender] += md.char_count

            if md.char_count > 0:
                non_empty_lengths.append(md.char_count)

        avg_message_length = (
            sum(non_empty_lengths) / len(non_empty_lengths) if non_empty_lengths else 0.0
        )

        # Build extra per-sender stats as additional fields. ChatStats has
        # extra="allow" so these pass through validation.
        extra_per_sender: dict[str, dict[str, int]] = {
            sender: {
                "messages": per_sender_messages[sender],
                "words": per_sender_words[sender],
                "chars": per_sender_chars[sender],
            }
            for sender in per_sender_messages
        }

        return ChatStats(
            total_words=total_words,
            total_chars=total_chars,
            total_emojis=total_emojis,
            total_media=total_media,
            messages_per_sender=dict(per_sender_messages),
            avg_message_length=round(avg_message_length, 2),
            per_sender_breakdown=extra_per_sender,  # extra="allow" keeps this
        )

    # ------------------------------------------------------------------
    # Pydantic conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pydantic(m: UCJMessage) -> UCJMessageSchema:
        """Convert dataclass UCJMessage -> pydantic schema with validation."""
        return UCJMessageSchema(
            id=m.id,
            sender=m.sender,
            timestamp=m.timestamp,
            content=m.content,
            type=m.type,  # type: ignore[arg-type]  # validated by Literal in schema
            reply_to_id=m.reply_to_id,
            metadata=UCJMetadataSchema(
                word_count=m.metadata.word_count,
                char_count=m.metadata.char_count,
                has_emoji=m.metadata.has_emoji,
                emojis=m.metadata.emojis,
                has_url=m.metadata.has_url,
                is_deleted=m.metadata.is_deleted,
                has_media=m.metadata.has_media,
            ),
        )


__all__ = ["UCJBuilder"]


# Helper kept here so we don't have to import it elsewhere if needed.
def messages_iter(ucj: UCJFile) -> Iterable[UCJMessageSchema]:
    """Convenience iterator - useful when downstream code only wants messages."""
    yield from ucj.messages
