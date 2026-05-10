"""
Generic CSV parser.

Used as a catch-all for chat exports that don't match a known platform format.
We auto-detect column names so users can drop in CSVs from random tools
without manually mapping fields.

Detection rules (case-insensitive substring match):
- sender:    sender, from, author, user, name, participant
- timestamp: timestamp, date, time, datetime, sent_at, created_at, when
- content:   message, content, text, body, msg

If the file has a header but none of those columns are recognized, we fall
back to positional defaults (col 0 = sender, col 1 = timestamp, col 2 =
content). If there's no header at all, the same positional defaults apply.

Timestamps are parsed using a battery of common formats. ISO 8601 is tried
first because it's unambiguous; everything else is locale-ambiguous.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

from app.services.parser.base import ChatParser, UCJMessage, UCJResult

logger = logging.getLogger(__name__)


_SENDER_KEYS = ("sender", "from", "author", "user", "name", "participant")
_TIMESTAMP_KEYS = ("timestamp", "datetime", "date", "time", "sent_at", "created_at", "when")
_CONTENT_KEYS = ("message", "content", "text", "body", "msg")

# Try ISO first (unambiguous), then non-US day-first formats, then US ones.
_TIMESTAMP_FORMATS: tuple[str, ...] = (
    # ISO 8601 + variants
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    # Day-first (most of the world)
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    # Month-first (US)
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%m/%d/%y %I:%M %p",
    "%m/%d/%y %H:%M",
)


class CSVParser(ChatParser):
    PLATFORM_NAME = "csv"

    def parse(self, content: str) -> UCJResult:
        # Use csv.Sniffer to handle different delimiters (',' ';' '\t').
        # Fall back to comma if sniffing fails on small files.
        sample = content[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            dialect = csv.excel
            has_header = True  # safer assumption; we'll fall through to positional if keys don't match

        reader = csv.reader(io.StringIO(content), dialect=dialect)
        rows = list(reader)
        if not rows:
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME)

        # Resolve column indices.
        sender_idx, ts_idx, content_idx = _resolve_columns(rows[0], has_header)
        if sender_idx is None or ts_idx is None or content_idx is None:
            # If the user gave us a totally weird CSV, refuse rather than
            # producing garbage data.
            logger.error(
                "CSV: could not resolve columns. header=%r sender=%s ts=%s content=%s",
                rows[0], sender_idx, ts_idx, content_idx,
            )
            return UCJResult(messages=[], source_platform=self.PLATFORM_NAME, skipped_count=len(rows))

        data_rows = rows[1:] if has_header else rows
        ucj_messages: list[UCJMessage] = []
        skipped = 0

        for idx, row in enumerate(data_rows):
            try:
                ucj = self._parse_row(row, idx, sender_idx, ts_idx, content_idx)
            except Exception as e:
                logger.warning("CSV: skipping row %d: %s", idx, e)
                skipped += 1
                continue

            if ucj is None:
                skipped += 1
                continue

            ucj_messages.append(ucj)

        # CSVs are usually but not always chronological. Sort to be safe -
        # downstream code (UCJBuilder) relies on sorted timestamps.
        ucj_messages.sort(key=lambda m: m.timestamp)

        # Renumber ids after sorting so msg_0 is the chronologically first.
        for i, m in enumerate(ucj_messages):
            m.id = self.make_id(i)

        if skipped:
            logger.info("CSV parser: parsed %d, skipped %d", len(ucj_messages), skipped)

        return UCJResult(
            messages=ucj_messages,
            source_platform=self.PLATFORM_NAME,
            skipped_count=skipped,
        )

    # ------------------------------------------------------------------

    def _parse_row(
        self,
        row: list[str],
        index: int,
        sender_idx: int,
        ts_idx: int,
        content_idx: int,
    ) -> UCJMessage | None:
        # Defensive bounds check - some CSVs have ragged rows.
        max_idx = max(sender_idx, ts_idx, content_idx)
        if len(row) <= max_idx:
            return None

        sender = (row[sender_idx] or "").strip() or "unknown"
        ts_str = (row[ts_idx] or "").strip()
        content = (row[content_idx] or "").strip()

        ts = _parse_timestamp(ts_str)
        if ts is None:
            logger.warning("CSV: row %d has unparseable timestamp %r", index, ts_str)
            return None

        # Heuristic: empty content + plausible system message? We don't have
        # rich type info for CSV, so default to text.
        ucj_type = "text" if content else "text"

        metadata = self.build_metadata(content)

        return UCJMessage(
            id=self.make_id(index),
            sender=sender,
            timestamp=ts,
            content=content,
            type=ucj_type,
            reply_to_id=None,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_columns(
    first_row: list[str], has_header: bool
) -> tuple[int | None, int | None, int | None]:
    """Return indices for (sender, timestamp, content) columns.

    Strategy:
    1. If there's a header, match column names against our key sets.
    2. Otherwise, fall back to positional (0, 1, 2).
    3. If header exists but matching fails, also fall back to positional.
    """
    if has_header:
        normalized = [h.strip().lower() for h in first_row]
        sender_idx = _find_column(normalized, _SENDER_KEYS)
        ts_idx = _find_column(normalized, _TIMESTAMP_KEYS)
        content_idx = _find_column(normalized, _CONTENT_KEYS)

        if sender_idx is not None and ts_idx is not None and content_idx is not None:
            return sender_idx, ts_idx, content_idx

        # Partial match - log what we got and fall through to positional.
        logger.info(
            "CSV: header partial-match (sender=%s ts=%s content=%s); using positional fallback",
            sender_idx, ts_idx, content_idx,
        )

    # Positional fallback: only valid if the row has at least 3 columns.
    if len(first_row) >= 3:
        return 0, 1, 2
    return None, None, None


def _find_column(headers: list[str], keys: tuple[str, ...]) -> int | None:
    """Return the first header index whose name contains any key (substring match)."""
    for i, h in enumerate(headers):
        for key in keys:
            if key in h:
                return i
    return None


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None

    # Unix timestamp (seconds or milliseconds) - common in tool exports.
    if value.isdigit():
        as_int = int(value)
        # Anything bigger than ~year 2286 is ms; smaller is seconds.
        if as_int > 10_000_000_000:
            try:
                return datetime.fromtimestamp(as_int / 1000)
            except (OverflowError, OSError):
                return None
        try:
            return datetime.fromtimestamp(as_int)
        except (OverflowError, OSError):
            return None

    # ISO with timezone suffix (we strip and reparse since stdlib
    # fromisoformat is finicky about timezones across Python versions).
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


# Re-exported for tests
__all__ = ["CSVParser"]


# Type-hints suppression for Any unused imports
_: Any = None
