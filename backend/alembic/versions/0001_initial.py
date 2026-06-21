"""initial schema

Creates the full ChatLens schema:
- pgvector / pg_trgm / citext extensions
- users
- chat_uploads (with source_platform + processing_status enums)
- messages (with pgvector embedding column; the HNSW index for semantic
  search lives on message_chunks.embedding in migration 0003, which is the
  granularity the search path actually queries)
- analysis_cache
- All supporting indexes

Revision ID: 0001
Revises:
Create Date: 2026-05-09 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Postgres enum types — created up-front so column defs reference them by
# name. `create_type=False` on the column-level enums prevents SQLAlchemy
# from re-emitting CREATE TYPE inside the table definition.
SOURCE_PLATFORM = postgresql.ENUM(
    "whatsapp",
    "telegram",
    "instagram",
    "facebook",
    "csv",
    "unknown",
    name="source_platform",
    create_type=False,
)

PROCESSING_STATUS = postgresql.ENUM(
    "pending",
    "processing",
    "nlp_processing",
    "embedding",
    "done",
    "failed",
    name="processing_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # ---- Extensions -------------------------------------------------------
    # vector: pgvector for semantic search.
    # pg_trgm: trigram indexes for fast LIKE / fuzzy search.
    # citext: case-insensitive text — handy for participant handles.
    #
    # `vector` is created here (not only in scripts/init-db.sql) so a clean
    # `alembic upgrade head` succeeds against any fresh Postgres — including a
    # local non-Docker instance or a managed DB where the init script never
    # runs. Migration 0003 (message_chunks.embedding vector(1536)) depends on
    # this extension existing.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ---- Enum types -------------------------------------------------------
    SOURCE_PLATFORM.create(bind, checkfirst=True)
    PROCESSING_STATUS.create(bind, checkfirst=True)

    # ---- users ------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "storage_used_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    # ---- chat_uploads -----------------------------------------------------
    op.create_table(
        "chat_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("platform", SOURCE_PLATFORM, nullable=False),
        sa.Column(
            "status",
            PROCESSING_STATUS,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "ucj_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "total_messages",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "participants",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("date_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("span_days", sa.Integer(), nullable=True),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_chat_uploads_user_id", "chat_uploads", ["user_id"], unique=False
    )
    op.create_index(
        "ix_chat_uploads_user_id_created_at",
        "chat_uploads",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_uploads_status", "chat_uploads", ["status"], unique=False
    )

    # ---- messages ---------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("msg_index", sa.Integer(), nullable=False),
        sa.Column("msg_id", sa.String(length=128), nullable=False),
        sa.Column("sender", sa.String(length=255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "msg_type",
            sa.String(length=32),
            nullable=False,
            server_default="text",
        ),
        sa.Column("reply_to_id", sa.String(length=128), nullable=True),
        sa.Column(
            "word_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "char_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "has_emoji",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "emojis",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "has_url",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "has_media",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("sentiment_label", sa.String(length=16), nullable=True),
        sa.Column("emotion_label", sa.String(length=16), nullable=True),
        sa.Column("emotion_score", sa.Float(), nullable=True),
        sa.Column("topics", postgresql.ARRAY(sa.Text()), nullable=True),
        # Real pgvector column to match the Message ORM model
        # (Vector(EMBEDDING_DIMENSIONS)). Was ARRAY(Float) "for local run",
        # which drifted from the model and broke vector ops. No HNSW index
        # here: the active search path embeds and queries message_chunks
        # (see 0003), so an index on this column would sit over NULLs.
        sa.Column(
            "embedding",
            Vector(settings.EMBEDDING_DIMENSIONS),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_messages_upload_id_timestamp",
        "messages",
        ["upload_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_messages_upload_id_sender",
        "messages",
        ["upload_id", "sender"],
        unique=False,
    )
    op.create_index(
        "ix_messages_upload_id_msg_index",
        "messages",
        ["upload_id", "msg_index"],
        unique=False,
    )
    op.create_index(
        "ix_messages_upload_id_sentiment_label",
        "messages",
        ["upload_id", "sentiment_label"],
        unique=False,
    )
    op.create_index(
        "ix_messages_upload_id_emotion_label",
        "messages",
        ["upload_id", "emotion_label"],
        unique=False,
    )

    # No HNSW index on messages.embedding: semantic search runs on
    # message_chunks.embedding (migration 0003), which is what gets populated
    # and queried. Indexing this column would build over an all-NULL column.

    # ---- analysis_cache ---------------------------------------------------
    op.create_table(
        "analysis_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stat_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "upload_id", "stat_type", name="uq_analysis_cache_upload_stat"
        ),
    )
    op.create_index(
        "ix_analysis_cache_expires_at",
        "analysis_cache",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse order: drop dependents before parents.
    op.drop_index("ix_analysis_cache_expires_at", table_name="analysis_cache")
    op.drop_table("analysis_cache")

    # op.drop_index("ix_messages_embedding_hnsw", table_name="messages")
    op.drop_index("ix_messages_upload_id_emotion_label", table_name="messages")
    op.drop_index("ix_messages_upload_id_sentiment_label", table_name="messages")
    op.drop_index("ix_messages_upload_id_msg_index", table_name="messages")
    op.drop_index("ix_messages_upload_id_sender", table_name="messages")
    op.drop_index("ix_messages_upload_id_timestamp", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_chat_uploads_status", table_name="chat_uploads")
    op.drop_index("ix_chat_uploads_user_id_created_at", table_name="chat_uploads")
    op.drop_index("ix_chat_uploads_user_id", table_name="chat_uploads")
    op.drop_table("chat_uploads")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    PROCESSING_STATUS.drop(bind, checkfirst=True)
    SOURCE_PLATFORM.drop(bind, checkfirst=True)

    # Extensions are intentionally NOT dropped on downgrade — they may be in
    # use by other databases on the same cluster, and they're cheap to keep.
