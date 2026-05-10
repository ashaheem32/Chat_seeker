"""
Instagram parser.

Instagram's "Download your information" produces JSON files like::

    {
      "participants": [{"name": "Alice"}, {"name": "Bob"}],
      "messages": [
        {
          "sender_name": "Bob",
          "timestamp_ms": 1705077721000,
          "content": "hey",
          "reactions": [{"reaction": "❤", "actor": "Alice"}]
        },
        ...
      ],
      "title": "Bob",
      "is_still_participant": true,
      "thread_path": "inbox/...",
      "magic_words": []
    }

Two notorious quirks:

1. **Encoding bug.** Instagram exports UTF-8 strings that have been *re-encoded
   as latin-1 bytes and then decoded as UTF-8*. The result: every multi-byte
   character looks mojibake-ish - "é" becomes "Ã©", "🙂" becomes "ðŸ™‚",
   etc. We undo this by encoding back to latin-1 and decoding as UTF-8.

2. **Reverse chronological.** Messages are listed newest-first. We reverse
   the list before assigning `msg_N` ids so chronological order matches
   every other parser.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.services.parser.base import ChatParser, UCJMessage, UCJResult

logger = logging.getLogger(__name__)


class InstagramParser(ChatParser):
    PLATFORM_NAME = "instagram"

    def parse(self, content: str) -> UCJResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Instagram: invalid JSON: %s", e)
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            logger.error("Instagram: missing 'messages' array")
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        # Pull participants list - useful as a fallback hint when the message
        # batch only contains one side of a 1-1 chat.
        participants_hint: list[str] = []
        for p in data.get("participants", []) or []:
            if isinstance(p, dict) and p.get("name"):
                # Fix encoding on participant names too.
                participants_hint.append(_fix_mojibake(str(p["name"])))

        # Reverse to chronological order (oldest first).
        ordered = list(reversed(raw_messages))

        ucj_messages: list[UCJMessage] = []
        skipped = 0

        for idx, raw in enumerate(ordered):
            try:
                ucj = self._parse_one(raw, idx)
            except Exception as e:
                logger.warning("Instagram: skipping message %d: %s", idx, e)
                skipped += 1
                continue

            if ucj is None:
                skipped += 1
                continue

            ucj_messages.append(ucj)

        if skipped:
            logger.info("Instagram parser: parsed %d, skipped %d", len(ucj_messages), skipped)

        return UCJResult(
            messages=ucj_messages,
            source_platform=self.PLATFORM_NAME,
            participants_hint=participants_hint or None,
            skipped_count=skipped,
        )

    # ------------------------------------------------------------------

    def _parse_one(self, raw: dict[str, Any], index: int) -> UCJMessage | None:
        ts_ms = raw.get("timestamp_ms")
        if ts_ms is None:
            logger.warning("Instagram: skipping message without timestamp_ms")
            return None
        try:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000)
        except (TypeError, ValueError):
            logger.warning("Instagram: skipping message with bad timestamp %r", ts_ms)
            return None

        sender = _fix_mojibake(str(raw.get("sender_name") or "unknown"))

        # Determine type and content. Order matters: a message can have BOTH
        # `share` and `content` if the user typed text alongside a shared
        # link - we treat that as text since the textual content is what
        # matters for analysis.
        content_raw = raw.get("content")
        text = _fix_mojibake(content_raw) if isinstance(content_raw, str) else ""

        ucj_type = "text"
        has_media = False

        # "Unsent" / deleted detection - Instagram replaces deleted message
        # text with a known sentinel.
        is_deleted = bool(text and "unsent a message" in text.lower())

        if isinstance(raw.get("photos"), list) and raw["photos"]:
            ucj_type = "image"
            has_media = True
        elif isinstance(raw.get("videos"), list) and raw["videos"]:
            ucj_type = "video"
            has_media = True
        elif isinstance(raw.get("audio_files"), list) and raw["audio_files"]:
            ucj_type = "audio"
            has_media = True
        elif isinstance(raw.get("share"), dict):
            # Shared link/post - represent as text with the shared URL inline
            # so URL detection picks it up.
            share = raw["share"]
            link = share.get("link") or share.get("share_text")
            if link:
                text = f"{text} {link}".strip() if text else _fix_mojibake(str(link))
            ucj_type = "text"

        if is_deleted:
            ucj_type = "deleted"
            text = ""

        metadata = self.build_metadata(text, is_deleted=is_deleted, has_media=has_media)

        return UCJMessage(
            id=self.make_id(index),
            sender=sender,
            timestamp=ts,
            content=text,
            type=ucj_type,
            reply_to_id=None,  # Instagram exports don't preserve reply ids
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Encoding fix
# ---------------------------------------------------------------------------


def _fix_mojibake(text: str) -> str:
    """
    Reverse Instagram's latin-1 / UTF-8 re-encoding bug.

    The fix is to encode the broken text back to latin-1 (which round-trips
    cleanly because we're undoing a latin-1 decode) and then decode it as
    UTF-8 (the encoding that should have been used originally). If the
    string is already clean (pure ASCII or proper UTF-8), the round-trip
    fails harmlessly and we return the original.
    """
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        # Either already clean UTF-8 (no high bytes need re-encoding) or
        # contains characters that aren't in latin-1. Leave as-is.
        return text
