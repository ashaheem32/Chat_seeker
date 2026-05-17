"""
Persistence helpers for chat uploads.

Lives outside the router so the Celery worker can import these without
pulling in FastAPI's app/router machinery. The HTTP route used to inline
this code; pulling it here unblocks an async upload path where the
worker (not the request) owns the parse + persistence step.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_DEV_USER_EMAIL = "dev@local"


async def get_or_create_dev_user_id(db: AsyncSession) -> UUID:
    """Return the id of a placeholder dev user, creating it on first use.

    Why: ChatUpload.user_id is NOT NULL, but auth (M03) hasn't landed yet.
    Removing this helper is the right cleanup once real auth is wired.
    """
    from app.models import User

    result = await db.execute(select(User).where(User.email == _DEV_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user is not None:
        return user.id

    user = User(email=_DEV_USER_EMAIL, hashed_password="!disabled")
    db.add(user)
    await db.flush()
    return user.id


async def persist_parsed_ucj(
    db: AsyncSession,
    upload_id: UUID,
    ucj: Any,
    detection: Any,
    filename: str,
    *,
    create_row: bool = True,
) -> None:
    """Insert ChatUpload + bulk-insert all Message rows.

    When `create_row=False` we assume a stub ChatUpload row already exists
    (created by the upload route before enqueuing) and update it in place
    rather than inserting. This lets the route return 201 immediately while
    the worker fills in meta + messages.
    """
    from app.models import ChatUpload, Message, ProcessingStatus, SourcePlatform

    meta = ucj.meta

    try:
        platform_enum = SourcePlatform(detection.platform.value)
    except ValueError:
        platform_enum = SourcePlatform.unknown

    if create_row:
        user_id = await get_or_create_dev_user_id(db)
        chat_upload = ChatUpload(
            id=upload_id,
            user_id=user_id,
            platform=platform_enum,
            filename=filename,
            ucj_data=meta.model_dump(mode="json"),
            total_messages=meta.total_messages,
            status=ProcessingStatus.processing,
        )
        db.add(chat_upload)
        await db.flush()
    else:
        # Update the stub row created by the route.
        existing = await db.get(ChatUpload, upload_id)
        if existing is None:
            # Fall back to inserting if the stub somehow disappeared
            # (shouldn't happen; defensive only).
            logger.warning(
                "persist_parsed_ucj: stub row missing for %s; recreating",
                upload_id,
            )
            user_id = await get_or_create_dev_user_id(db)
            existing = ChatUpload(
                id=upload_id,
                user_id=user_id,
                platform=platform_enum,
                filename=filename,
                ucj_data=meta.model_dump(mode="json"),
                total_messages=meta.total_messages,
                status=ProcessingStatus.processing,
            )
            db.add(existing)
        else:
            existing.platform = platform_enum
            existing.filename = filename
            existing.ucj_data = meta.model_dump(mode="json")
            existing.total_messages = meta.total_messages
            existing.status = ProcessingStatus.processing
            existing.processing_error = None
        await db.flush()

    rows = [
        _message_to_mapping(upload_id, idx, msg)
        for idx, msg in enumerate(ucj.messages)
    ]
    if rows:
        copied = await _bulk_copy_messages(db, rows)
        if not copied:
            # asyncpg COPY wasn't available — fall back to executemany
            # INSERT. Slower but works against any driver.
            batch_size = 10_000
            for start in range(0, len(rows), batch_size):
                batch = rows[start : start + batch_size]
                await db.execute(insert(Message), batch)

    await db.commit()


_COPY_COLUMNS: tuple[str, ...] = (
    "id",
    "upload_id",
    "msg_index",
    "msg_id",
    "sender",
    "timestamp",
    "content",
    "msg_type",
    "reply_to_id",
    "word_count",
    "char_count",
    "has_emoji",
    "emojis",
    "has_url",
    "is_deleted",
    "has_media",
    "was_translated",
)


async def _bulk_copy_messages(db: AsyncSession, rows: list[dict[str, Any]]) -> bool:
    """Insert all messages via asyncpg COPY. Returns True on success, False
    if the raw connection isn't asyncpg (caller should fall back to INSERT).

    COPY beats executemany INSERT by ~5-10× on large uploads because it
    bypasses per-row SQL parsing and binds. We pass column order
    explicitly so adding a new column to the model doesn't silently break
    the COPY.
    """
    try:
        raw_conn = await db.connection()
        asyncpg_conn = await raw_conn.get_raw_connection()
        driver = getattr(asyncpg_conn, "driver_connection", None)
        if driver is None or not hasattr(driver, "copy_records_to_table"):
            return False
    except Exception:
        return False

    records = [
        (
            r["id"],
            r["upload_id"],
            r["msg_index"],
            r["msg_id"],
            r["sender"],
            r["timestamp"],
            r["content"],
            r["msg_type"],
            r["reply_to_id"],
            r["word_count"],
            r["char_count"],
            r["has_emoji"],
            r["emojis"],
            r["has_url"],
            r["is_deleted"],
            r["has_media"],
            False,  # was_translated default — language stage overwrites later
        )
        for r in rows
    ]

    try:
        await driver.copy_records_to_table(
            "messages", records=records, columns=list(_COPY_COLUMNS)
        )
    except Exception:
        logger.exception("COPY failed; falling back to INSERT")
        return False
    return True


def _message_to_mapping(upload_id: UUID, msg_index: int, msg: Any) -> dict[str, Any]:
    """Flatten a UCJ Message pydantic model into a dict for bulk insert."""
    md = msg.metadata
    return {
        "id": uuid4(),
        "upload_id": upload_id,
        "msg_index": msg_index,
        "msg_id": msg.id,
        "sender": msg.sender,
        "timestamp": msg.timestamp,
        "content": msg.content,
        "msg_type": msg.type,
        "reply_to_id": msg.reply_to_id,
        "word_count": md.word_count,
        "char_count": md.char_count,
        "has_emoji": md.has_emoji,
        "emojis": md.emojis,
        "has_url": md.has_url,
        "is_deleted": md.is_deleted,
        "has_media": md.has_media,
    }
