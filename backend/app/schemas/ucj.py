"""
Pydantic models mirroring the Universal Chat JSON (UCJ) schema.

These are the canonical Python representations of UCJ. Frontend types in
`frontend/lib/types.ts` MUST stay in sync with these. When changing one,
change both.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageType = Literal[
    "text", "image", "video", "audio", "sticker", "file", "deleted", "system"
]


class MessageMetadata(BaseModel):
    word_count: int = 0
    char_count: int = 0
    has_emoji: bool = False
    emojis: list[str] = Field(default_factory=list)
    has_url: bool = False
    is_deleted: bool = False
    has_media: bool = False


class Message(BaseModel):
    id: str
    sender: str
    timestamp: datetime
    content: str = ""
    type: MessageType = "text"
    reply_to_id: str | None = None
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)


class DateRange(BaseModel):
    start: datetime
    end: datetime
    span_days: int


class ChatStats(BaseModel):
    """Per-chat aggregate statistics. All counts are platform-agnostic."""

    model_config = ConfigDict(extra="allow")  # platforms may add extra stats

    total_words: int = 0
    total_chars: int = 0
    total_emojis: int = 0
    total_media: int = 0
    messages_per_sender: dict[str, int] = Field(default_factory=dict)
    avg_message_length: float = 0.0


class AIAnalysis(BaseModel):
    """Optional AI-generated analysis attached to a chat."""

    model_config = ConfigDict(extra="allow")

    summary: str | None = None
    topics: list[str] = Field(default_factory=list)
    sentiment_overall: float | None = None  # -1..1
    sentiment_per_sender: dict[str, float] = Field(default_factory=dict)
    relationship_dynamic: str | None = None
    generated_at: datetime | None = None
    model: str | None = None


class ChatMeta(BaseModel):
    source_platform: str
    source_file: str
    exported_at: datetime
    participants: list[str]
    total_messages: int
    date_range: DateRange
    stats: ChatStats
    ai_analysis: AIAnalysis | None = None


class UCJFile(BaseModel):
    """Top-level UCJ document."""

    ucj_version: str = "1.0"
    meta: ChatMeta
    messages: list[Message]
