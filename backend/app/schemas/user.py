"""Pydantic schemas for the User model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr


class UserCreate(UserBase):
    """Registration payload. Password is hashed in the service layer."""

    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """All fields optional — patches the existing record."""

    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None


class UserRead(UserBase):
    """Public-facing user representation. Never includes hashed_password."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    storage_used_bytes: int
    created_at: datetime
