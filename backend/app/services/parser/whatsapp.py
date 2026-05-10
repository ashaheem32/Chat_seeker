"""
WhatsApp parser.

WhatsApp's "Export chat" feature produces a plain-text .txt file whose format
varies by region, OS, and locale. The same chat exported on iOS US-English
looks completely different from one exported on Android German. We tackle
this with a small set of "header" regexes - any line that matches a header
starts a new message; everything else is appended as a continuation.

System messages (e.g. "X added Y", "Messages and calls are end-to-end
encrypted") are tagged `type="system"` so the analytics layer can ignore
them.

Media placeholders ("<Media omitted>", "image omitted", ...) are tagged
with the appropriate type and `metadata.has_media=True` even though the
actual binary isn't in the export.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.services.parser.base import ChatParser, UCJMessage, UCJResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Header regexes
# ---------------------------------------------------------------------------
# We try each pattern in order and use the first one that matches the file.
# Each pattern captures: date, time, (optional am/pm), sender, body.
#
# Note: the bracketed-iOS format uses U+200E LRM and U+202F NNBSP characters
# in some locales - we strip those before matching.

_BRACKET_FORMAT = re.compile(
    r"""^\[
        (?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})
        ,?\s+
        (?P<time>\d{1,2}:\d{2}(?::\d{2})?)
        \s*
        (?P<ampm>AM|PM|am|pm)?
        \]\s*
        (?P<sender>[^:]+?):\s
        (?P<body>.*)$
    """,
    re.VERBOSE,
)

# Android-style: "DD/MM/YYYY, HH:MM - Sender: body"  or AM/PM variants.
_DASH_FORMAT = re.compile(
    r"""^
        (?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})
        ,?\s+
        (?P<time>\d{1,2}:\d{2}(?::\d{2})?)
        \s*
        (?P<ampm>AM|PM|am|pm)?
        \s*-\s*
        (?P<rest>.*)$
    """,
    re.VERBOSE,
)

# ISO-ish format used by some recent Android exports: "YYYY-MM-DD, HH:MM - ..."
_ISO_DASH_FORMAT = re.compile(
    r"""^
        (?P<date>\d{4}-\d{2}-\d{2})
        ,?\s+
        (?P<time>\d{1,2}:\d{2}(?::\d{2})?)
        \s*-\s*
        (?P<rest>.*)$
    """,
    re.VERBOSE,
)


# Date-format candidates used to parse the captured "date" string. Order
# matters: we prefer day-first (DD/MM) over month-first (MM/DD) because most
# WhatsApp exports outside the US are day-first. The first one that parses
# successfully across the full file is locked in for the whole parse.
_DATE_CANDIDATES_DAY_FIRST = (
    "%d/%m/%Y", "%d/%m/%y",
    "%d.%m.%Y", "%d.%m.%y",
    "%d-%m-%Y", "%d-%m-%y",
)
_DATE_CANDIDATES_MONTH_FIRST = (
    "%m/%d/%Y", "%m/%d/%y",
    "%m.%d.%Y", "%m.%d.%y",
    "%m-%d-%Y", "%m-%d-%y",
)
_DATE_CANDIDATES_ISO = ("%Y-%m-%d",)

_TIME_CANDIDATES_24H = ("%H:%M:%S", "%H:%M")
_TIME_CANDIDATES_12H = ("%I:%M:%S %p", "%I:%M %p")


# ---------------------------------------------------------------------------
# Media + system message phrase detection
# ---------------------------------------------------------------------------

# Any of these substrings (case-insensitive) marks a media placeholder. We
# only check this on what would otherwise be the message body.
_MEDIA_PHRASES: tuple[tuple[str, str], ...] = (
    # (phrase, message type)
    ("image omitted", "image"),
    ("video omitted", "video"),
    ("audio omitted", "audio"),
    ("voice message omitted", "audio"),
    ("ptt omitted", "audio"),
    ("sticker omitted", "sticker"),
    ("gif omitted", "image"),
    ("document omitted", "file"),
    ("contact card omitted", "file"),
    ("<media omitted>", "image"),  # generic Android catch-all
)

_DELETED_PHRASES = (
    "this message was deleted",
    "you deleted this message",
    "<this message was edited>",  # edits aren't deletions but for now treat as text
)

# Heuristic for system messages: lines without a colon-separated sender, or
# matching well-known templates.
_SYSTEM_PATTERNS = (
    re.compile(r"^Messages and calls are end-to-end encrypted", re.IGNORECASE),
    re.compile(r"\bcreated group\b", re.IGNORECASE),
    re.compile(r"\b(added|removed|left|joined)\b", re.IGNORECASE),
    re.compile(r"\bchanged the subject\b", re.IGNORECASE),
    re.compile(r"\bchanged this group's icon\b", re.IGNORECASE),
    re.compile(r"\bchanged the group description\b", re.IGNORECASE),
    re.compile(r"\bsecurity code changed\b", re.IGNORECASE),
    re.compile(r"\bmessages to this (chat|group)\b", re.IGNORECASE),
)

# Characters WhatsApp inserts that mess up regexes.
_LRM = "‎"  # LEFT-TO-RIGHT MARK
_RLM = "‏"
_NNBSP = " "  # NARROW NO-BREAK SPACE (used in iOS times)


class WhatsAppParser(ChatParser):
    PLATFORM_NAME = "whatsapp"

    def parse(self, content: str) -> UCJResult:
        # Normalize line endings + invisible chars up front. Cheaper to do
        # once than to handle them in every regex.
        text = (
            content.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace(_LRM, "")
            .replace(_RLM, "")
            .replace(_NNBSP, " ")
        )
        lines = text.split("\n")

        # Lock in date format on first successful parse so we don't flip-flop
        # between day-first and month-first mid-file.
        date_format: str | None = None
        time_format: str | None = None

        messages: list[UCJMessage] = []
        skipped = 0
        next_index = 0

        for raw_line in lines:
            line = raw_line.rstrip()
            if not line:
                continue

            header = _match_header(line)
            if header is None:
                # Continuation line - append to previous message body.
                if messages:
                    prev = messages[-1]
                    prev.content = f"{prev.content}\n{line}" if prev.content else line
                    # Recompute metadata so word/char counts stay correct.
                    prev.metadata = self.build_metadata(
                        prev.content,
                        is_deleted=prev.metadata.is_deleted,
                        has_media=prev.metadata.has_media,
                    )
                else:
                    # Orphan continuation before the first header - skip.
                    skipped += 1
                continue

            # Determine date/time format on the first parsable header.
            ts: datetime | None = None
            if date_format and time_format:
                ts = _try_parse_timestamp(header["date"], header["time"], header["ampm"], date_format, time_format)
            if ts is None:
                date_format, time_format, ts = _detect_formats(
                    header["date"], header["time"], header["ampm"]
                )

            if ts is None:
                logger.warning("WhatsApp: skipping line with unparseable timestamp: %r", line[:80])
                skipped += 1
                continue

            sender, body, msg_type, is_system = _split_sender_and_body(header)

            # Detect deleted/media on the body.
            body_lower = body.lower() if body else ""
            is_deleted = any(phrase in body_lower for phrase in _DELETED_PHRASES)
            has_media = msg_type in {"image", "video", "audio", "sticker", "file"}

            if is_deleted:
                msg_type = "deleted"

            if is_system:
                msg_type = "system"
                # System messages don't have a sender per se - use a sentinel.
                if not sender:
                    sender = "system"

            metadata = self.build_metadata(
                body, is_deleted=is_deleted, has_media=has_media
            )

            messages.append(
                UCJMessage(
                    id=self.make_id(next_index),
                    sender=sender or "unknown",
                    timestamp=ts,
                    content="" if is_deleted else body,
                    type=msg_type,
                    reply_to_id=None,  # WhatsApp text exports don't preserve reply links
                    metadata=metadata,
                )
            )
            next_index += 1

        if skipped:
            logger.info("WhatsApp parser: parsed %d messages, skipped %d malformed lines",
                        len(messages), skipped)

        return UCJResult(
            messages=messages,
            source_platform=self.PLATFORM_NAME,
            skipped_count=skipped,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _match_header(line: str) -> dict[str, str | None] | None:
    """Try each header regex; return the first match's groupdict or None."""
    for pat in (_BRACKET_FORMAT, _ISO_DASH_FORMAT, _DASH_FORMAT):
        m = pat.match(line)
        if m:
            d = m.groupdict()
            # Normalize: bracket format already splits sender/body; dash
            # variants put both into 'rest'.
            if "rest" in d:
                rest = d.pop("rest") or ""
                # Try to split sender and body. System messages have no colon.
                sender, sep, body = rest.partition(":")
                if sep:
                    d["sender"] = sender.strip()
                    d["body"] = body.lstrip()
                    d["_is_system"] = "false"
                else:
                    d["sender"] = ""
                    d["body"] = rest
                    d["_is_system"] = "true"
            else:
                d["_is_system"] = "false"
            return d
    return None


def _detect_formats(
    date_str: str, time_str: str, ampm: str | None
) -> tuple[str | None, str | None, datetime | None]:
    """Try every plausible (date_format, time_format) combo.

    Returns the first triple that parses successfully so subsequent lines
    can short-circuit. None on failure.
    """
    # Time formats depend on AM/PM presence.
    time_fmts = _TIME_CANDIDATES_12H if ampm else _TIME_CANDIDATES_24H

    # ISO date format takes precedence if the date looks ISO.
    if "-" in date_str and len(date_str.split("-")[0]) == 4:
        date_fmts: tuple[str, ...] = _DATE_CANDIDATES_ISO
    else:
        # Day-first first (matches most non-US exports).
        date_fmts = _DATE_CANDIDATES_DAY_FIRST + _DATE_CANDIDATES_MONTH_FIRST

    for date_fmt in date_fmts:
        for time_fmt in time_fmts:
            ts = _try_parse_timestamp(date_str, time_str, ampm, date_fmt, time_fmt)
            if ts is not None:
                return date_fmt, time_fmt, ts
    return None, None, None


def _try_parse_timestamp(
    date_str: str, time_str: str, ampm: str | None, date_fmt: str, time_fmt: str
) -> datetime | None:
    full_time = f"{time_str} {ampm.upper()}" if ampm else time_str
    candidate = f"{date_str} {full_time}"
    try:
        return datetime.strptime(candidate, f"{date_fmt} {time_fmt}")
    except ValueError:
        return None


def _split_sender_and_body(header: dict[str, str | None]) -> tuple[str, str, str, bool]:
    """
    Resolve sender, body, type, and system-ness from a matched header.

    Returns:
        (sender, body, message_type, is_system)
    """
    sender = (header.get("sender") or "").strip()
    body = (header.get("body") or "")
    is_system_flag = header.get("_is_system") == "true"

    # If the line was tagged as system by the matcher (no colon), or the body
    # matches a known system template, force system type.
    if is_system_flag or any(p.search(body) for p in _SYSTEM_PATTERNS):
        return sender, body, "system", True

    # Detect media phrases - check the lowercase form so locale variants
    # like "Image omitted" / "image omitted" all match.
    body_lower = body.lower()
    for phrase, mtype in _MEDIA_PHRASES:
        if phrase in body_lower:
            return sender, body, mtype, False

    return sender, body, "text", False
