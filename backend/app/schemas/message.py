"""Pydantic schemas for the Message model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SentimentLabel = Literal["positive", "negative", "neutral"]
EmotionLabel = Literal[
    "joy", "sadness", "anger", "fear", "love", "surprise", "disgust"
]


class MessageBase(BaseModel):
    msg_index: int
    msg_id: str = Field(..., max_length=128)
    sender: str = Field(..., max_length=255)
    timestamp: datetime
    content: str = ""
    msg_type: str = Field(default="text", max_length=32)
    reply_to_id: str | None = None

    word_count: int = 0
    char_count: int = 0
    has_emoji: bool = False
    emojis: list[str] = Field(default_factory=list)
    has_url: bool = False
    is_deleted: bool = False
    has_media: bool = False


class MessageCreate(MessageBase):
    """Bulk-insert payload — the ingest worker builds these from UCJ."""

    upload_id: UUID


class MessageNLPUpdate(BaseModel):
    """Patch applied by the NLP worker once analysis completes."""

    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    sentiment_label: SentimentLabel | None = None
    emotion_label: EmotionLabel | None = None
    emotion_score: float | None = Field(default=None, ge=0.0, le=1.0)
    topics: list[str] | None = None


class MessageEmbeddingUpdate(BaseModel):
    """Patch applied by the embedding worker. Vector dim = settings.EMBEDDING_DIMENSIONS."""

    embedding: list[float]


class MessageRead(MessageBase):
    """Full message representation. Embedding is omitted by default — it's
    1536 floats and rarely needed on the wire."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    upload_id: UUID

    sentiment_score: float | None = None
    sentiment_label: SentimentLabel | None = None
    emotion_label: EmotionLabel | None = None
    emotion_score: float | None = None
    topics: list[str] | None = None


class MessageWithEmbedding(MessageRead):
    """Variant that includes the embedding — for explicit semantic-search responses."""

    embedding: list[float] | None = None


class MessageList(BaseModel):
    """Paginated list response."""

    upload_id: UUID
    total: int
    limit: int
    offset: int
    messages: list[MessageRead]
