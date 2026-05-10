"""
Facebook Messenger parser.

Facebook's "Download your information" tool exports per-thread JSON files.
The format is essentially the same as Instagram (both products share
infrastructure), with some differences:

- `gifs` array (separate from `photos`).
- `sticker` is an object (single), not a list.
- `is_geoblocked_for_viewer` and friends - irrelevant for analysis.
- Group conversations have multiple participants in `participants`.

Like Instagram, message order is newest-first and exports suffer the same
latin-1 / UTF-8 mojibake bug. We reuse the same fix.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.services.parser.base import ChatParser, UCJMessage, UCJResult
from app.services.parser.instagram import _fix_mojibake  # share the encoding fix

logger = logging.getLogger(__name__)


class FacebookParser(ChatParser):
    PLATFORM_NAME = "facebook"

    def parse(self, content: str) -> UCJResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Facebook: invalid JSON: %s", e)
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            logger.error("Facebook: missing 'messages' array")
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=1)

        participants_hint: list[str] = []
        for p in data.get("participants", []) or []:
            if isinstance(p, dict) and p.get("name"):
                participants_hint.append(_fix_mojibake(str(p["name"])))

        ordered = list(reversed(raw_messages))

        ucj_messages: list[UCJMessage] = []
        skipped = 0

        for idx, raw in enumerate(ordered):
            try:
                ucj = self._parse_one(raw, idx)
            except Exception as e:
                logger.warning("Facebook: skipping message %d: %s", idx, e)
                skipped += 1
                continue

            if ucj is None:
                skipped += 1
                continue

            ucj_messages.append(ucj)

        if skipped:
            logger.info("Facebook parser: parsed %d, skipped %d", len(ucj_messages), skipped)

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
            return None
        try:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000)
        except (TypeError, ValueError):
            return None

        sender = _fix_mojibake(str(raw.get("sender_name") or "unknown"))

        content_raw = raw.get("content")
        text = _fix_mojibake(content_raw) if isinstance(content_raw, str) else ""

        ucj_type = "text"
        has_media = False

        # Detect special message types. Facebook tags some events with a
        # `type` field: "Generic" (normal), "Share", "Subscribe", "Call",
        # "Plan", etc. Anything non-Generic without media is treated as
        # system unless it has text we can show.
        fb_type = raw.get("type")

        if isinstance(raw.get("photos"), list) and raw["photos"]:
            ucj_type = "image"
            has_media = True
        elif isinstance(raw.get("videos"), list) and raw["videos"]:
            ucj_type = "video"
            has_media = True
        elif isinstance(raw.get("audio_files"), list) and raw["audio_files"]:
            ucj_type = "audio"
            has_media = True
        elif isinstance(raw.get("gifs"), list) and raw["gifs"]:
            ucj_type = "image"  # gifs analyzed as images for stats purposes
            has_media = True
        elif isinstance(raw.get("sticker"), dict) and raw["sticker"]:
            ucj_type = "sticker"
            has_media = True
        elif isinstance(raw.get("share"), dict):
            share = raw["share"]
            link = share.get("link") or share.get("share_text")
            if link:
                text = f"{text} {link}".strip() if text else _fix_mojibake(str(link))
            ucj_type = "text"
        elif fb_type and fb_type not in {"Generic", "Share"} and not text:
            # Calls, plans, subscriptions - no text body, mark as system.
            ucj_type = "system"
            text = f"[{fb_type}]"

        # "is_unsent" indicates a deleted message in some recent exports.
        is_deleted = bool(raw.get("is_unsent"))
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
            reply_to_id=None,
            metadata=metadata,
        )
