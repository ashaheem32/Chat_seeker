"""Pydantic schemas for the AnalysisCache model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalysisCacheBase(BaseModel):
    stat_type: str = Field(..., max_length=64)
    payload: dict[str, Any]
    expires_at: datetime | None = None


class AnalysisCacheCreate(AnalysisCacheBase):
    upload_id: UUID


class AnalysisCacheUpdate(BaseModel):
    """Used by the cache layer to refresh a stale entry in place."""

    payload: dict[str, Any]
    expires_at: datetime | None = None


class AnalysisCacheRead(AnalysisCacheBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    upload_id: UUID
    created_at: datetime
    updated_at: datetime
