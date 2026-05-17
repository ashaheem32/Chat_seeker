"""add message_chunks table for window-level embeddings

Embedding every individual message in a 200k-message chat means 1000+
sequential OpenAI calls. Embedding *conversation windows* (groups of
~15 messages, split on long time gaps) collapses that to ~70 calls and
typically improves retrieval quality on chat data — a single "lol"
message means nothing out of context, but a chunk does.

The HNSW index uses the same operator class + parameters as the (now
deprecated) one on messages.embedding, so query semantics stay the
same — only the granularity changes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13 22:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "message_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_msg_index", sa.Integer(), nullable=False),
        sa.Column("end_msg_index", sa.Integer(), nullable=False),
        sa.Column("start_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "participants",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # pgvector column added via raw SQL — SQLAlchemy core can't express
    # the type without pulling in pgvector's bindings at migration time.
    op.execute("ALTER TABLE message_chunks ADD COLUMN embedding vector(1536)")

    op.create_index(
        "ix_chunks_upload_id_chunk_index",
        "message_chunks",
        ["upload_id", "chunk_index"],
        unique=True,
    )
    op.create_index(
        "ix_chunks_upload_id_start_msg",
        "message_chunks",
        ["upload_id", "start_msg_index"],
        unique=False,
    )
    # HNSW vector index — same params as the original messages.embedding
    # one so search latency profile stays the same.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw "
        "ON message_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_upload_id_start_msg", table_name="message_chunks")
    op.drop_index("ix_chunks_upload_id_chunk_index", table_name="message_chunks")
    op.drop_table("message_chunks")
