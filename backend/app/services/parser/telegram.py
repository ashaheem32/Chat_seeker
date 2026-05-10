"""
Telegram parser.

Telegram's "Export chat history" → JSON option produces files like::

    {
      "name": "Group Name",
      "type": "private_supergroup",
      "id": 123,
      "messages": [
        {
          "id": 1,
          "type": "message",
          "date": "2024-01-12T18:42:01",
          "date_unixtime": "1705077721",
          "from": "Alice",
          "from_id": "user1234",
          "text": "Hello world",
          "text_entities": [{"type": "plain", "text": "Hello world"}]
        },
        ...
      ]
    }

The fiddly bits:

- `text` can be a string OR an array of {"type", "text"} chunks for messages
  with bold / italic / links / mentions. We flatten to plain text.
- `type` can be "message" or "service" (joins, pins, etc.). We map service
  messages to UCJ type "system".
- Media is signalled by various optional keys: photo, file, sticker_emoji,
  media_type ("voice_message" / "video_message" / "animation" / ...).
- `reply_to_message_id` is an int referring to another message's `id`. We
  translate it to UCJ's "msg_N" form by indexing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.services.parser.base import ChatParser, UCJMessage, UCJResult

logger = logging.getLogger(__name__)


# Map Telegram media_type -> UCJ message type. Anything not in the map but
# still has a media key falls back to "file".
_MEDIA_TYPE_MAP: dict[str, str] = {
    "voice_message": "audio",
    "video_message": "video",
    "video_file": "video",
    "audio_file": "audio",
    "animation": "video",  # GIFs - close enough to video for analytics
    "sticker": "sticker",
}

# Service messages we treat as system events. We don't need an exhaustive list -
# any "service" type message is system in UCJ regardless.
_SERVICE_TYPE = "service"


class TelegramParser(ChatParser):
    PLATFORM_NAME = "telegram"

    def parse(self, content: str) -> UCJResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Telegram: invalid JSON: %s", e)
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            logger.error("Telegram: expected `messages` array, got %s", type(raw_messages).__name__)
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        # First pass: build all UCJMessages and a map from telegram-id to UCJ id
        # so reply links can be resolved in a second pass.
        ucj_messages: list[UCJMessage] = []
        tg_id_to_ucj_id: dict[int, str] = {}
        # Per-record reply hint, populated alongside ucj_messages for pass 2.
        reply_to_tg_ids: list[int | None] = []
        skipped = 0

        for idx, raw in enumerate(raw_messages):
            try:
                ucj = self._parse_one(raw, idx)
            except Exception as e:  # defensive - never crash the whole parse
                logger.warning("Telegram: skipping message at index %d: %s", idx, e)
                skipped += 1
                continue

            if ucj is None:
                skipped += 1
                continue

            ucj_messages.append(ucj)
            reply_to_tg_ids.append(_safe_int(raw.get("reply_to_message_id")))

            tg_id = _safe_int(raw.get("id"))
            if tg_id is not None:
                tg_id_to_ucj_id[tg_id] = ucj.id

        # Pass 2: resolve replies now that we have the full id map.
        for ucj, reply_target in zip(ucj_messages, reply_to_tg_ids, strict=True):
            if reply_target is not None:
                ucj.reply_to_id = tg_id_to_ucj_id.get(reply_target)

        # Telegram exports are already chronological - no sort needed.
        if skipped:
            logger.info("Telegram parser: parsed %d, skipped %d", len(ucj_messages), skipped)

        return UCJResult(
            messages=ucj_messages,
            source_platform=self.PLATFORM_NAME,
            skipped_count=skipped,
        )

    # ------------------------------------------------------------------

    def _parse_one(self, raw: dict[str, Any], index: int) -> UCJMessage | None:
        """Convert a single Telegram message dict to UCJMessage, or None to skip."""
        msg_type_raw = raw.get("type", "message")

        ts = _parse_timestamp(raw)
        if ts is None:
            logger.warning("Telegram: skipping message %r (no parseable date)", raw.get("id"))
            return None

        sender = (
            raw.get("from")
            or raw.get("actor")  # service messages use 'actor' instead of 'from'
            or "unknown"
        )

        # Flatten text. Telegram uses both `text` (rich array) and
        # `text_entities` (typed chunks) - flattening either gives us plain
        # text suitable for analysis.
        content = _flatten_text(raw.get("text"))
        if not content and isinstance(raw.get("text_entities"), list):
            content = _flatten_text(raw["text_entities"])

        # Determine UCJ message type.
        ucj_type = "text"
        has_media = False

        if msg_type_raw == _SERVICE_TYPE:
            ucj_type = "system"
            # Service messages often have a human-readable `action` describing
            # what happened (e.g. "join_group_by_link"). Surface it as content
            # if there's no other text.
            if not content and raw.get("action"):
                content = str(raw["action"])
        else:
            # Detect media. Order matters - check the more specific media_type
            # field before falling back to generic photo/file presence.
            media_type = raw.get("media_type")
            if media_type and media_type in _MEDIA_TYPE_MAP:
                ucj_type = _MEDIA_TYPE_MAP[media_type]
                has_media = True
            elif raw.get("photo"):
                ucj_type = "image"
                has_media = True
            elif raw.get("sticker_emoji") or raw.get("file") and "sticker" in str(raw.get("media_type", "")).lower():
                ucj_type = "sticker"
                has_media = True
            elif raw.get("file"):
                ucj_type = "file"
                has_media = True
            elif raw.get("poll") is not None:
                ucj_type = "system"  # polls aren't meaningfully analyzable as text
                has_media = False

        is_deleted = bool(raw.get("deleted"))  # rarely present but cheap to check
        if is_deleted:
            ucj_type = "deleted"
            content = ""

        metadata = self.build_metadata(content, is_deleted=is_deleted, has_media=has_media)

        return UCJMessage(
            id=self.make_id(index),
            sender=str(sender),
            timestamp=ts,
            content=content,
            type=ucj_type,
            reply_to_id=None,  # filled in pass 2
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: dict[str, Any]) -> datetime | None:
    """Prefer `date_unixtime` (precise, timezone-correct) over `date` string."""
    unix = raw.get("date_unixtime")
    if unix is not None:
        try:
            return datetime.fromtimestamp(int(unix))
        except (TypeError, ValueError):
            pass

    date = raw.get("date")
    if isinstance(date, str):
        # Telegram exports dates as ISO without tz: "2024-01-12T18:42:01"
        try:
            return datetime.fromisoformat(date)
        except ValueError:
            return None
    return None


def _flatten_text(text: Any) -> str:
    """
    Telegram's `text` field is either a string or a list mixing strings and
    {"type": "...", "text": "..."} dicts. Flatten to plain text.
    """
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for chunk in text:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                # `text` is the displayed text; `href` is for link entities
                # but we keep just the displayed form for analysis purposes.
                value = chunk.get("text", "")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts)
    return ""


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
