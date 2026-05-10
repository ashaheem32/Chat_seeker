"""
Parser abstractions and dataclass schema for the UCJ intermediate format.

Why dataclasses (not pydantic) here?
    Parsing produces hundreds of thousands of message records on a single
    upload. Pydantic v2 is fast but dataclasses are still ~10x cheaper to
    construct - we get validation at the API boundary (pydantic schemas in
    `app/schemas/upload.py`) and skip the per-row overhead during parsing.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regexes shared across parsers
# ---------------------------------------------------------------------------

# URL detector. Conservative - matches http(s)/ftp + bare www., trims trailing
# punctuation that's almost always sentence punctuation, not part of the URL.
URL_RE = re.compile(
    r"\b(?:https?://|www\.)[^\s<>\"'\)]+",
    flags=re.IGNORECASE,
)

# Emoji detector. Covers the major Unicode emoji ranges:
#   - Emoticons (1F600-1F64F)
#   - Misc symbols & pictographs (1F300-1F5FF)
#   - Transport & map (1F680-1F6FF)
#   - Supplemental symbols & pictographs (1F900-1F9FF)
#   - Symbols & pictographs extended-A (1FA70-1FAFF)
#   - Misc symbols (2600-26FF) and dingbats (2700-27BF)
#   - Regional indicator pairs (flags) handled via the 1F1E6-1F1FF range
#   - Skin-tone modifiers (1F3FB-1F3FF)
#
# Variation Selector-16 (FE0F) is appended to many emojis - we strip it from
# the captured glyph for cleaner deduping.
EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\U00002600-\U000026ff"
    "\U00002700-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "]",
    flags=re.UNICODE,
)


# ---------------------------------------------------------------------------
# UCJ intermediate dataclasses
# ---------------------------------------------------------------------------

# Allowed message types - kept as a literal-ish set rather than an Enum so it
# matches the JSON-string form used in pydantic schemas without conversion.
MESSAGE_TYPES = frozenset(
    {"text", "image", "video", "audio", "sticker", "file", "deleted", "system"}
)


@dataclass
class UCJMetadata:
    word_count: int = 0
    char_count: int = 0
    has_emoji: bool = False
    emojis: list[str] = field(default_factory=list)
    has_url: bool = False
    is_deleted: bool = False
    has_media: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_count": self.word_count,
            "char_count": self.char_count,
            "has_emoji": self.has_emoji,
            "emojis": self.emojis,
            "has_url": self.has_url,
            "is_deleted": self.is_deleted,
            "has_media": self.has_media,
        }


@dataclass
class UCJMessage:
    id: str  # "msg_N"
    sender: str
    timestamp: datetime
    content: str
    type: str = "text"
    reply_to_id: str | None = None
    metadata: UCJMetadata = field(default_factory=UCJMetadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "type": self.type,
            "reply_to_id": self.reply_to_id,
            "metadata": self.metadata.to_dict(),
        }


@dataclass
class UCJResult:
    """Output of a single ChatParser.parse() call.

    The factory wraps this with `UCJBuilder.build()` to compute meta.stats
    and produce the final pydantic UCJFile.
    """

    messages: list[UCJMessage]
    source_platform: str
    # Hint for the builder when participants aren't trivially derived from
    # message senders (e.g. group chats with renamed participants).
    participants_hint: list[str] | None = None
    # Number of input lines/records that were skipped due to malformed data.
    # Surfaced in logs and (optionally) the API response so users know
    # whether their export looks healthy.
    skipped_count: int = 0


# ---------------------------------------------------------------------------
# Parser ABC
# ---------------------------------------------------------------------------


class ChatParser(ABC):
    """Abstract base for platform-specific parsers.

    Implementations MUST:
    - Be deterministic for a given input (no randomness, no I/O).
    - Never raise on malformed input - log a warning and skip the record.
    - Produce sequential `id` values "msg_0", "msg_1", ... in chronological
      order (oldest first). Callers rely on this for pagination.
    """

    #: Human-readable name used as `meta.source_platform` in the UCJ output.
    PLATFORM_NAME: str = "unknown"

    @abstractmethod
    def parse(self, content: str) -> UCJResult:
        """Parse a raw export string into UCJ messages.

        Args:
            content: The full text/JSON content of the uploaded file.

        Returns:
            UCJResult with messages in chronological order.
        """

    # --- Helpers shared by subclasses --------------------------------------

    @staticmethod
    def build_metadata(content: str, *, is_deleted: bool = False, has_media: bool = False) -> UCJMetadata:
        """Compute UCJMetadata for a message body.

        Centralized so every parser produces consistent stats - changing the
        emoji or URL regex updates all parsers at once.
        """
        if not content:
            return UCJMetadata(is_deleted=is_deleted, has_media=has_media)

        emojis = [e for e in EMOJI_RE.findall(content)]
        has_url = bool(URL_RE.search(content))

        return UCJMetadata(
            word_count=len(content.split()) if content.strip() else 0,
            char_count=len(content),
            has_emoji=bool(emojis),
            emojis=emojis,
            has_url=has_url,
            is_deleted=is_deleted,
            has_media=has_media,
        )

    @staticmethod
    def make_id(index: int) -> str:
        return f"msg_{index}"
