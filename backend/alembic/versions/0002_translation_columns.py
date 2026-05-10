"""add translation columns to messages

Adds three columns the language-normalization pipeline writes to:
    content_english     — English form of `content` (Text, nullable)
    original_language   — detector classification (String(50), nullable)
    was_translated      — True only when an LLM-backed translation ran

All three are safe to add online: every column is either nullable or
has a server_default of 'false', so existing rows accept the schema
change without backfill.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-09 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("content_english", sa.Text(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("original_language", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column(
            "was_translated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "was_translated")
    op.drop_column("messages", "original_language")
    op.drop_column("messages", "content_english")
