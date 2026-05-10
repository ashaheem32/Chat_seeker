"""
User model.

Owns ChatUpload rows. Authentication uses email + bcrypt-hashed password
(see app.core.security in M03). `storage_used_bytes` is denormalized here
because totaling sums of upload sizes on every dashboard hit would be
needlessly expensive — we update it atomically when uploads are created or
deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.chat_upload import ChatUpload


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Citext would be nicer for case-insensitive uniqueness, but plain text +
    # always-lowercase-on-write is portable across non-pg dialects (tests).
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Bytes consumed by this user's uploads. Used for quota enforcement at
    # upload time without scanning the filesystem.
    storage_used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    uploads: Mapped[list["ChatUpload"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",  # forces explicit loading; prevents accidental N+1
    )
