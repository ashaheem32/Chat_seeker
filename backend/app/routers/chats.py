"""
Conversations router.

Endpoints (mounted under /api/v1/conversations):
    GET /                      paginated list of the user's conversations
    GET /{conversation_id}     single-conversation read with full meta block
    GET /{conversation_id}/status   polling endpoint for the post-upload
                                    progress bar (returns coarse stage +
                                    fine-grained progress percentage)

Auth note:
    Until the auth layer ships (M03), every request is scoped to the
    same dev user via `_get_or_create_dev_user_id`. That helper lives
    on the upload router because it had the first need; we re-import
    it here so the dev-user-id lookup stays in one place.

Why this lives in routers/chats.py:
    The original 30-line stub was here under the `/chats` mount. We
    repurposed the file to host the conversations API and updated the
    prefix in routers/__init__.py to `/conversations`. Keeping the
    file name avoids a router-rename churn pass, and the surrounding
    services (search, stats, etc.) keep their independent mounts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import ChatUpload, ProcessingStatus
from app.routers.upload import broker as upload_broker
from app.routers.upload import _get_or_create_dev_user_id
from app.schemas.conversation import (
    ConversationDateRange,
    ConversationDetail,
    ConversationListItem,
    ConversationListResponse,
    ConversationStatus,
    ConversationStatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Internal mappings
# ---------------------------------------------------------------------------


# DB enum -> public status. Public values match the frontend vocabulary.
# `processing` maps to `normalizing` because, after Layer-4 wiring, the
# language normalization task is what runs at that stage; the previous
# generic "processing" label was confusing about which work was happening.
_STATUS_MAP: dict[ProcessingStatus, ConversationStatus] = {
    ProcessingStatus.pending: "queued",
    ProcessingStatus.processing: "normalizing",
    ProcessingStatus.nlp_processing: "nlp",
    ProcessingStatus.embedding: "embedding",
    ProcessingStatus.done: "done",
    ProcessingStatus.failed: "error",
}

# Coarse progress floors per stage. The /status endpoint blends these with
# any fine-grained event in the upload broker so a single request reflects
# both "we're in the embedding stage" AND "we've embedded 60% of messages".
_STAGE_PROGRESS: dict[ConversationStatus, float] = {
    "queued": 5.0,
    "normalizing": 25.0,
    "nlp": 55.0,
    "embedding": 85.0,
    "done": 100.0,
    "error": 0.0,
}

# Pagination guards.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

# Preview shape — last 3 messages, snippets capped at 200 chars.
_PREVIEW_MESSAGES = 3
_PREVIEW_CHAR_CAP = 200


# ---------------------------------------------------------------------------
# GET /conversations  — paginated list
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List the current user's conversations (newest first)",
)
async def list_conversations(
    limit: int = Query(
        default=_DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description="Page size. 1–100, default 20.",
    ),
    offset: int = Query(
        default=0, ge=0, description="Skip count for pagination."
    ),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    user_id = await _get_or_create_dev_user_id(db)

    # Count + page in two queries. We use `count(id)` rather than COUNT(*)
    # so Postgres can use the indexed PK rather than counting rows including
    # any tombstones. Cheap on this size class.
    total = (
        await db.scalar(
            select(func.count(ChatUpload.id)).where(ChatUpload.user_id == user_id)
        )
    ) or 0

    page_stmt = (
        select(ChatUpload)
        .where(ChatUpload.user_id == user_id)
        .order_by(desc(ChatUpload.created_at), desc(ChatUpload.id))
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page_stmt)).scalars().all()

    # Fetch previews for the visible page in a single query — one IN clause
    # against (upload_id, msg_index) ranges. Doing N+1 queries (one per
    # conversation) would dominate the response time on a 20-row page.
    previews_by_upload = await _fetch_previews(
        db, [row.id for row in rows]
    )

    items = [
        _to_list_item(row, previews_by_upload.get(row.id, []))
        for row in rows
    ]

    return ConversationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /conversations/{id}  — single-conversation detail
# ---------------------------------------------------------------------------


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Single-conversation read with full meta stats",
)
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    upload = await _load_owned_upload(conversation_id, db)
    previews = await _fetch_previews(db, [upload.id])
    base = _to_list_item(upload, previews.get(upload.id, []))

    return ConversationDetail(
        # Spread the list-item fields, then attach the full meta block.
        # Using model_dump preserves any tweaks _to_list_item makes (e.g.
        # the synthesized name).
        **base.model_dump(),
        meta=dict(upload.ucj_data or {}),
    )


# ---------------------------------------------------------------------------
# GET /conversations/{id}/status  — progress polling
# ---------------------------------------------------------------------------


@router.get(
    "/{conversation_id}/status",
    response_model=ConversationStatusResponse,
    summary="Live processing status + progress percentage for a conversation",
)
async def get_conversation_status(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationStatusResponse:
    upload = await _load_owned_upload(conversation_id, db)
    public_status = _STATUS_MAP.get(upload.status, "queued")

    # Blend: stage floor + any fine-grained progress from the upload broker.
    # The broker only knows about uploads that ran inside this process —
    # it returns None for older uploads, which is fine, we just fall back
    # to the stage floor.
    stage_floor = _STAGE_PROGRESS.get(public_status, 0.0)
    stage_detail = ""
    error: str | None = None

    live = upload_broker.latest(upload.id)
    if live is not None:
        # The upload broker reports progress as 0–1 within the parsing /
        # persistence window. Boost into the [stage_floor, next_stage_floor]
        # band so a fresh upload at parsing-50% reads as ~15% overall, not 50%.
        next_floor = _next_stage_floor(public_status)
        progress = stage_floor + (next_floor - stage_floor) * (live.progress or 0.0)
        if live.stage_detail:
            stage_detail = live.stage_detail
        if live.error:
            error = live.error
    else:
        progress = stage_floor

    if public_status == "done":
        progress = 100.0
    if public_status == "error":
        progress = 0.0
        error = error or upload.processing_error or "Processing failed"

    # Cap to [0, 100] in case the live blend overshoots from rounding.
    progress = max(0.0, min(100.0, progress))

    return ConversationStatusResponse(
        conversation_id=upload.id,
        processing_status=public_status,
        progress=round(progress, 1),
        stage_detail=stage_detail,
        error=error,
        # job_id capture lands with the auth/job-tracking pass; today we
        # always return None and the frontend polls this endpoint instead
        # of querying Celery directly.
        job_id=None,
        updated_at=upload.updated_at,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_owned_upload(
    conversation_id: UUID, db: AsyncSession
) -> ChatUpload:
    """404 if the upload doesn't exist or belongs to another user.

    The dev-user shim means today every caller "owns" every upload, but
    we still go through the user_id check so that the route's behavior
    doesn't change when real auth lands."""
    upload = await db.get(ChatUpload, conversation_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    user_id = await _get_or_create_dev_user_id(db)
    if upload.user_id != user_id:
        # Hide existence from non-owners — same response as not-found so we
        # don't leak that this id is taken by someone else.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return upload


def _to_list_item(
    upload: ChatUpload, preview: list[str]
) -> ConversationListItem:
    """Build a list-item from an ORM row + a pre-fetched preview list."""
    public_status = _STATUS_MAP.get(upload.status, "queued")

    date_range = None
    if upload.date_start is not None and upload.date_end is not None:
        date_range = ConversationDateRange(
            start=upload.date_start,
            end=upload.date_end,
            span_days=upload.span_days or 0,
        )

    return ConversationListItem(
        id=upload.id,
        name=_build_conversation_name(upload),
        platform=upload.platform.value,
        participants=list(upload.participants or []),
        total_messages=upload.total_messages,
        date_range=date_range,
        processing_status=public_status,
        job_id=None,
        created_at=upload.created_at,
        preview=preview,
    )


def _build_conversation_name(upload: ChatUpload) -> str:
    """Derive a friendly display name from the participant list.

    Format rules:
        0 participants  → fall back to the filename (without extension).
        1 participant   → the participant's name.
        2 participants  → "Alice & Bob"
        3-4 participants → "Alice, Bob & Carol" / "Alice, Bob, Carol & Dave"
        5+ participants → "Alice, Bob & N others"
    """
    parts = [p for p in (upload.participants or []) if p and p.strip()]
    if not parts:
        # Strip a single trailing extension — keeps "WhatsApp Chat" tidy.
        stem = upload.filename.rsplit(".", 1)[0] or upload.filename
        return stem or "Untitled conversation"

    parts = parts[:]  # don't mutate the ORM-attached list
    n = len(parts)
    if n == 1:
        return parts[0]
    if n == 2:
        return f"{parts[0]} & {parts[1]}"
    if n <= 4:
        return f"{', '.join(parts[:-1])} & {parts[-1]}"
    return f"{parts[0]}, {parts[1]} & {n - 2} others"


async def _fetch_previews(
    db: AsyncSession, upload_ids: list[UUID]
) -> dict[UUID, list[str]]:
    """Last-N message previews for a batch of uploads, in chronological order.

    Strategy:
        1. Per upload, find the cutoff `msg_index` so the top _PREVIEW_MESSAGES
           rows by recency are kept. Row-number window function does this
           in one round-trip across all uploads.
        2. Join back to the message text (preferring `content_english` so
           non-English chats preview in the dashboard's primary language).
        3. Skip deleted / system rows so the preview reflects readable content.

    Returns a dict keyed by upload_id; missing keys mean "no readable
    preview" (the caller passes [] to the schema)."""
    if not upload_ids:
        return {}

    # We use a single SQL pass with ROW_NUMBER() over (upload_id ORDER BY
    # msg_index DESC). Filtering on the row number after the window keeps
    # the index scan tight even on multi-million-row tables.
    from sqlalchemy import text

    sql = text(
        """
        WITH ranked AS (
            SELECT
                m.upload_id,
                m.msg_index,
                m.content,
                m.content_english,
                m.is_deleted,
                m.msg_type,
                ROW_NUMBER() OVER (
                    PARTITION BY m.upload_id
                    ORDER BY m.msg_index DESC
                ) AS rn
            FROM messages m
            WHERE m.upload_id = ANY(:upload_ids)
              AND m.is_deleted = false
              AND m.msg_type NOT IN ('deleted', 'system')
              AND COALESCE(m.content_english, m.content) <> ''
        )
        SELECT upload_id, msg_index, content, content_english
        FROM ranked
        WHERE rn <= :limit
        ORDER BY upload_id, msg_index ASC
        """
    )
    rows = (
        await db.execute(
            sql,
            {
                "upload_ids": [str(u) for u in upload_ids],
                "limit": _PREVIEW_MESSAGES,
            },
        )
    ).all()

    previews: dict[UUID, list[str]] = {}
    for r in rows:
        # Cast back from str — asyncpg returns UUIDs as UUID objects when the
        # column is a uuid type, but we passed a str array via the ANY clause
        # which can come back as the underlying server cast. Force UUID for
        # dict-key consistency.
        uid = r.upload_id if isinstance(r.upload_id, UUID) else UUID(str(r.upload_id))
        text_value = (r.content_english or r.content or "").strip()
        if not text_value:
            continue
        if len(text_value) > _PREVIEW_CHAR_CAP:
            text_value = text_value[:_PREVIEW_CHAR_CAP].rstrip() + "…"
        previews.setdefault(uid, []).append(text_value)

    return previews


def _next_stage_floor(stage: ConversationStatus) -> float:
    """Look up the floor of the stage that comes after `stage`. Used to bound
    the broker-driven progress percentage so it doesn't climb past the next
    stage's official floor before that stage has actually started."""
    order: list[ConversationStatus] = [
        "queued", "normalizing", "nlp", "embedding", "done"
    ]
    if stage in {"done", "error"}:
        return 100.0
    try:
        idx = order.index(stage)
    except ValueError:
        return 100.0
    nxt = order[idx + 1] if idx + 1 < len(order) else "done"
    return _STAGE_PROGRESS[nxt]


# Module-level fallback timestamp for cases where the ORM row's updated_at
# is somehow None (legacy rows from before the column was non-nullable).
# Reads as "right now" so the frontend doesn't render a 1970 epoch date.
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
