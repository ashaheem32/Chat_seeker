"""
Pydantic schemas for the /upload endpoints.

These wrap the parser output (UCJ) for transport over the API. Schemas live
here (not in `schemas/ucj.py`) because they're tied to the upload flow:
the API sends back UCJ-shaped data with extra upload-level fields like
the upload id, processing progress, and detection confidence.

Aliases:
    UCJMessageSchema  -> schemas.ucj.Message
    UCJMetaSchema     -> schemas.ucj.ChatMeta
    UCJFileSchema     -> schemas.ucj.UCJFile

We expose them under the "...Schema" naming the upload router uses,
keeping `schemas.ucj` as the canonical source of truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ucj import (
    AIAnalysis,
    ChatMeta,
    ChatStats,
    DateRange,
    Message,
    MessageMetadata,
    MessageType,
    UCJFile,
)

# Re-export UCJ types under the "...Schema" names the spec calls for.
# Two parallel names keep external callers free to use whichever they like
# while the canonical model stays in schemas.ucj.
UCJMessageSchema = Message
UCJMetaSchema = ChatMeta
UCJFileSchema = UCJFile


# ---------------------------------------------------------------------------
# Upload-flow schemas
# ---------------------------------------------------------------------------

#: Stages an upload progresses through. "queued" is the initial state when a
#: file is accepted; "parsing" / "persisting" are intermediate phases that
#: the WebSocket progress feed surfaces; "ready" / "failed" are terminal.
ProcessingStage = Literal[
    "queued",
    "parsing",
    "persisting",
    "ready",
    "failed",
]


class UploadResponse(BaseModel):
    """Returned synchronously after a successful POST /api/upload.

    The full message list is intentionally omitted - large chats can be 100k+
    messages and the response would be huge. Clients fetch messages via
    paginated endpoints once the upload is `ready`.
    """

    upload_id: UUID = Field(..., description="ID for polling status and fetching the parsed chat")
    filename: str
    detected_platform: str = Field(
        ..., description="Platform the detector chose (whatsapp/telegram/instagram/facebook/csv)"
    )
    detection_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Detector confidence score 0-1"
    )
    detection_reason: str = Field(
        default="", description="Human-readable explanation of how the platform was detected"
    )
    status: ProcessingStage = "queued"

    # Upload-time aggregate hints. Cheap to compute (we already have them
    # from parsing) and useful to show "X messages parsed" while the heavy
    # AI analysis runs in the background.
    meta: UCJMetaSchema | None = Field(
        default=None,
        description="UCJ meta block (no messages). Populated once parsing completes.",
    )
    skipped_count: int = Field(
        default=0, description="Number of malformed records the parser skipped"
    )

    # Backwards compatibility alias - older clients used `chat_id`. Computed
    # at serialization time so we don't need a setter.
    @property
    def chat_id(self) -> UUID:  # pragma: no cover - shim
        return self.upload_id


class UploadStatus(BaseModel):
    """Polled by the frontend until status terminal."""

    upload_id: UUID
    status: ProcessingStage
    progress: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Fractional progress through the current stage"
    )
    stage_detail: str = Field(
        default="", description="Optional human-readable detail (e.g. 'parsing message 12500/40000')"
    )
    error: str | None = Field(
        default=None, description="Error message if status == 'failed'"
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now())


class UploadProgressEvent(BaseModel):
    """Single event pushed over the WebSocket progress feed."""

    upload_id: UUID
    stage: ProcessingStage
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


__all__ = [
    "UploadResponse",
    "UploadStatus",
    "UploadProgressEvent",
    "ProcessingStage",
    # UCJ aliases
    "UCJMessageSchema",
    "UCJMetaSchema",
    "UCJFileSchema",
    # Re-exports for convenience
    "AIAnalysis",
    "ChatMeta",
    "ChatStats",
    "DateRange",
    "Message",
    "MessageMetadata",
    "MessageType",
    "UCJFile",
]
