"""Pydantic schemas for the ChatUpload model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat_upload import ProcessingStatus, SourcePlatform


class ChatUploadBase(BaseModel):
    filename: str = Field(..., max_length=512)
    platform: SourcePlatform = SourcePlatform.unknown


class ChatUploadCreate(ChatUploadBase):
    """Internal create payload — the upload route builds this after parsing."""

    user_id: UUID
    ucj_data: dict[str, Any] = Field(default_factory=dict)
    total_messages: int = 0
    participants: list[str] = Field(default_factory=list)
    date_start: datetime | None = None
    date_end: datetime | None = None
    span_days: int | None = None


class ChatUploadStatusUpdate(BaseModel):
    """Workers patch status through the pipeline; processing_error on failure."""

    status: ProcessingStatus
    processing_error: str | None = None


class ChatUploadRead(ChatUploadBase):
    """Returned from list / detail endpoints. Includes denormalized meta."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    status: ProcessingStatus
    ucj_data: dict[str, Any]
    total_messages: int
    participants: list[str]
    date_start: datetime | None
    date_end: datetime | None
    span_days: int | None
    processing_error: str | None
    created_at: datetime
    updated_at: datetime


class ChatUploadSummary(BaseModel):
    """Lightweight variant for list views — no ucj_data blob."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    platform: SourcePlatform
    status: ProcessingStatus
    total_messages: int
    participants: list[str]
    date_start: datetime | None
    date_end: datetime | None
    created_at: datetime
