"""
Build conversation-window chunks from messages.

Rule:
    Open a new chunk when EITHER
        - the current chunk has hit `target_size` messages, OR
        - the gap to the next message exceeds `time_gap_minutes`.
    Carry the last `overlap` messages into the next chunk so cross-chunk
    exchanges aren't split mid-thread (improves retrieval precision).

Why these defaults:
    target_size=15      — empirically the sweet spot for chat: long
                          enough that "lol" carries surrounding context,
                          short enough that a single chunk is still
                          tightly themed.
    time_gap_minutes=30 — natural human conversation boundary; longer
                          gaps almost always mean a topic change.
    overlap=2           — keeps Q-then-A pairs together when they
                          straddle a boundary.

Skipping:
    System messages and empty/whitespace-only content are excluded —
    they don't carry retrievable meaning and just dilute the chunk
    embedding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence
from uuid import UUID, uuid4

from app.models import Message

logger = logging.getLogger(__name__)


_TARGET_SIZE = 15
_TIME_GAP = timedelta(minutes=30)
_OVERLAP = 2


@dataclass(slots=True)
class ChunkDraft:
    """In-memory chunk before it's persisted. Mirrors MessageChunk's
    NOT NULL columns; `embedding` is filled in later by the indexer."""

    id: UUID = field(default_factory=uuid4)
    upload_id: UUID = field(default=None)  # type: ignore[assignment]
    chunk_index: int = 0
    start_msg_index: int = 0
    end_msg_index: int = 0
    start_ts: datetime = field(default=None)  # type: ignore[assignment]
    end_ts: datetime = field(default=None)  # type: ignore[assignment]
    participants: list[str] = field(default_factory=list)
    content: str = ""


def build_chunks(
    upload_id: UUID,
    messages: Sequence[Message],
    *,
    target_size: int = _TARGET_SIZE,
    time_gap: timedelta = _TIME_GAP,
    overlap: int = _OVERLAP,
) -> list[ChunkDraft]:
    """Group `messages` (already ordered by msg_index) into chunks.

    Returns chunks tagged with sequential `chunk_index` starting at 0.
    The caller is responsible for persisting them.
    """
    eligible = [m for m in messages if _is_embeddable(m)]
    if not eligible:
        return []

    chunks: list[ChunkDraft] = []
    current: list[Message] = []
    chunk_index = 0

    def flush(buf: list[Message]) -> None:
        nonlocal chunk_index
        if not buf:
            return
        draft = ChunkDraft(
            upload_id=upload_id,
            chunk_index=chunk_index,
            start_msg_index=buf[0].msg_index,
            end_msg_index=buf[-1].msg_index,
            start_ts=buf[0].timestamp,
            end_ts=buf[-1].timestamp,
            participants=sorted({m.sender for m in buf}),
            content=_join_for_embedding(buf),
        )
        chunks.append(draft)
        chunk_index += 1

    for i, msg in enumerate(eligible):
        # Decide whether to open a new chunk BEFORE appending the current msg.
        if current:
            gap = msg.timestamp - current[-1].timestamp
            size_hit = len(current) >= target_size
            gap_hit = gap > time_gap
            if size_hit or gap_hit:
                flush(current)
                # Carry-over: last `overlap` messages bridge into the next
                # chunk so a question-then-answer pair isn't split. Skip
                # carry-over when the split was caused by a long time
                # gap — that's a real topic change, no bridging warranted.
                tail = current[-overlap:] if (size_hit and not gap_hit) else []
                current = list(tail)

        current.append(msg)

    flush(current)
    logger.info(
        "Chunked upload=%s: %d eligible messages -> %d chunks",
        upload_id,
        len(eligible),
        len(chunks),
    )
    return chunks


def _is_embeddable(m: Message) -> bool:
    """Skip rows that contribute no retrievable meaning."""
    if m.msg_type != "text":
        return False
    if m.is_deleted:
        return False
    text = (m.content_english or m.content or "").strip()
    if len(text) < 2:
        return False
    return True


def _join_for_embedding(buf: Sequence[Message]) -> str:
    """Render a chunk as `Sender: text` lines. Prefer content_english so
    cross-language chats embed in a single semantic space."""
    lines: list[str] = []
    for m in buf:
        text = (m.content_english or m.content or "").strip()
        if not text:
            continue
        lines.append(f"{m.sender}: {text}")
    return "\n".join(lines)
