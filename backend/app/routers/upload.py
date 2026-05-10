"""
Upload router.

Endpoints:
    POST /upload                - accept a chat file, parse, persist, return id + meta
    GET  /upload/{upload_id}    - poll status
    WS   /upload/ws/{upload_id} - stream progress events while parsing/persisting

Design notes:

- We don't write the uploaded file to disk - parsing is fast enough to do
  inline and storing raw exports duplicates data we already have in the
  Message table. If you need to keep originals (e.g. for compliance),
  flip the WRITE_RAW_FILE flag and we'll save under settings.UPLOAD_DIR.

- Parsing happens inside the request to keep the API simple. For files
  >10MB or chats >50k messages this could exceed reasonable request
  budgets - if it becomes a problem, move parsing to the existing Celery
  worker (`app.workers.tasks`) and have this endpoint just enqueue.

- Progress events go through an in-process broker (ProgressBroker). This
  works for single-worker dev / small prod deployments. For multi-worker
  setups, swap the broker for Redis pub/sub - the public interface is
  identical so it's a one-file change.

- Bulk insert: SQLAlchemy's `bulk_insert_mappings` skips ORM construction
  per row, which is ~10x faster than `session.add_all()` for large
  payloads. We pay for that with no defaults / hooks - we set every
  required field explicitly below.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.schemas.upload import (
    ProcessingStage,
    UploadProgressEvent,
    UploadResponse,
    UploadStatus,
)
from app.services.parser import parse_file
from app.services.parser.detector import PlatformType

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# In-process progress broker
# ---------------------------------------------------------------------------


class ProgressBroker:
    """
    Pub/sub for upload progress events, keyed by upload_id.

    Multiple WebSocket consumers per upload are supported (e.g. a tab and a
    background poll). Producers (the parsing pipeline) call `publish()`;
    consumers `subscribe()` and iterate the returned queue.

    For a multi-worker / multi-host deployment, swap this for Redis pub/sub
    behind the same async interface.
    """

    def __init__(self) -> None:
        # upload_id -> set of subscriber queues
        self._subs: dict[UUID, set[asyncio.Queue[UploadProgressEvent]]] = defaultdict(set)
        # Last-known status per upload, for late-joining subscribers and the GET endpoint.
        self._latest: dict[UUID, UploadStatus] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: UploadProgressEvent) -> None:
        async with self._lock:
            # Update the cached status so polling endpoints see the latest.
            self._latest[event.upload_id] = UploadStatus(
                upload_id=event.upload_id,
                status=event.stage,
                progress=event.progress,
                stage_detail=event.message,
                error=None if event.stage != "failed" else event.message,
                updated_at=event.timestamp,
            )
            # Snapshot subscribers so we don't hold the lock during put().
            queues = list(self._subs.get(event.upload_id, ()))

        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Progress queue full for upload %s; dropping event", event.upload_id)

    async def subscribe(self, upload_id: UUID) -> asyncio.Queue[UploadProgressEvent]:
        q: asyncio.Queue[UploadProgressEvent] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subs[upload_id].add(q)
            # Push the latest status (if any) so newly-connected clients see
            # state immediately instead of waiting for the next event.
            cached = self._latest.get(upload_id)
        if cached is not None:
            await q.put(
                UploadProgressEvent(
                    upload_id=cached.upload_id,
                    stage=cached.status,
                    progress=cached.progress,
                    message=cached.stage_detail,
                    timestamp=cached.updated_at,
                )
            )
        return q

    async def unsubscribe(self, upload_id: UUID, q: asyncio.Queue[UploadProgressEvent]) -> None:
        async with self._lock:
            self._subs[upload_id].discard(q)
            if not self._subs[upload_id]:
                self._subs.pop(upload_id, None)

    def latest(self, upload_id: UUID) -> UploadStatus | None:
        return self._latest.get(upload_id)


# Module-level singleton. FastAPI's lifecycle will keep it alive for the
# process lifetime; tests can monkeypatch this if they need isolation.
broker = ProgressBroker()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a chat export and parse it into UCJ",
)
async def upload_chat(
    file: UploadFile = File(..., description="Chat export file (txt/json/csv, ≤50MB)"),
    platform: PlatformType | None = None,
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """
    Accept a chat export, parse it inline, persist messages in bulk, and
    return the upload id + UCJ meta block.

    Query params:
        platform: Override auto-detection. Useful when the detector can't
            confidently classify a file.
    """
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filename is required")

    # ---- 1. Read + size check ------------------------------------------
    raw = await file.read()
    if len(raw) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB",
        )
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    upload_id = uuid4()

    # Decode. Most exports are UTF-8; a minority (older WhatsApp Android) are
    # latin-1. Try UTF-8 first; on failure fall back to latin-1 with errors
    # replaced so we don't bail on a single bad byte.
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.info("Upload %s: not UTF-8, falling back to latin-1", upload_id)
        content = raw.decode("latin-1", errors="replace")

    await _publish(upload_id, "queued", 0.0, "Upload accepted")

    # ---- 2. Parse ------------------------------------------------------
    await _publish(upload_id, "parsing", 0.1, f"Parsing {file.filename}")
    try:
        ucj, detection = parse_file(content, file.filename, platform=platform)
    except ValueError as e:
        await _publish(upload_id, "failed", 0.0, str(e))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except Exception as e:
        logger.exception("Upload %s: parser crashed", upload_id)
        await _publish(upload_id, "failed", 0.0, "Parser error")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Parser error") from e

    await _publish(
        upload_id, "parsing", 0.6,
        f"Parsed {ucj.meta.total_messages:,} messages from {detection.platform.value}",
    )

    # ---- 3. Persist ----------------------------------------------------
    await _publish(upload_id, "persisting", 0.7, "Saving to database")
    try:
        await _persist_upload(db, upload_id, ucj, detection, file.filename)
    except Exception as e:
        logger.exception("Upload %s: persistence failed", upload_id)
        await _publish(upload_id, "failed", 0.0, "Database error")
        # Roll back any partial inserts.
        await db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Database error") from e

    # ---- 4. Hand off to the async pipeline -----------------------------
    # Language normalization (Layer 4) runs first; on success it chains
    # the NLP pipeline, which itself chains the embedding indexer.
    # Failures here are non-fatal — `content_english` simply stays NULL
    # and downstream services fall back to `content`. The HTTP response
    # below still returns 201 so the client can poll status.
    _enqueue_language_normalization(upload_id, ucj)

    await _publish(upload_id, "ready", 1.0, "Upload complete")

    return UploadResponse(
        upload_id=upload_id,
        filename=file.filename,
        detected_platform=detection.platform.value,
        detection_confidence=detection.confidence,
        detection_reason=detection.reason,
        status="ready",
        meta=ucj.meta,
        skipped_count=getattr(ucj, "_skipped_count", 0),  # set by builder if available
    )


@router.get(
    "/{upload_id}",
    response_model=UploadStatus,
    summary="Check the status of an upload",
)
async def get_upload_status(
    upload_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> UploadStatus:
    """Poll-friendly status endpoint - returns the same data the WS feed pushes.

    Falls back to the DB when the in-memory broker has no record (e.g. after
    a server restart) so dashboards reopened later still resolve.
    """
    s = broker.latest(upload_id)
    if s is not None:
        return s

    # In-memory broker is empty (process restarted). Reconstruct from the DB.
    from app.models import ChatUpload

    upload = await db.get(ChatUpload, upload_id)
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown upload id")
    return _status_from_upload(upload)


# ProcessingStatus (DB) -> ProcessingStage (API) mapping. The API stage vocab
# is coarser than the DB lifecycle, so several intermediate states collapse
# onto "parsing" / "persisting".
_DB_STAGE_TO_API: dict[str, ProcessingStage] = {
    "pending": "queued",
    "processing": "parsing",
    "nlp_processing": "parsing",
    "embedding": "persisting",
    "done": "ready",
    "failed": "failed",
}


def _status_from_upload(upload: Any) -> UploadStatus:
    """Build a UploadStatus from a ChatUpload row when the broker is cold."""
    stage: ProcessingStage = _DB_STAGE_TO_API.get(upload.status.value, "parsing")
    progress = 1.0 if stage == "ready" else (0.0 if stage == "failed" else 0.5)
    return UploadStatus(
        upload_id=upload.id,
        status=stage,
        progress=progress,
        stage_detail="",
        error=upload.processing_error if stage == "failed" else None,
        updated_at=upload.updated_at,
    )


@router.get(
    "/{upload_id}/ucj",
    summary="Fetch the parsed UCJ payload (preview or full download)",
)
async def get_upload_ucj(
    upload_id: UUID,
    limit: int | None = None,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Reconstruct the UCJ payload from the persisted ChatUpload + Message rows.

    Query params:
        limit: If set, return at most this many messages (oldest first). Used
            for the preview pane in the converter UI; omit for full export.
        download: If true, set Content-Disposition so browsers save the
            response as `<filename>.ucj.json`. Otherwise return inline JSON.
    """
    from fastapi.responses import JSONResponse, Response

    from app.models import ChatUpload, Message

    chat_upload = await db.get(ChatUpload, upload_id)
    if chat_upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown upload id")

    msg_query = (
        select(Message)
        .where(Message.upload_id == upload_id)
        .order_by(Message.msg_index.asc())
    )
    if limit is not None and limit > 0:
        msg_query = msg_query.limit(limit)

    result = await db.execute(msg_query)
    rows = result.scalars().all()

    # Reconstruct the UCJ message shape - the DB stores metadata flattened
    # across columns, but the wire format nests them under `metadata`.
    messages = [
        {
            "id": m.msg_id,
            "sender": m.sender,
            "timestamp": m.timestamp.isoformat(),
            "content": m.content,
            "type": m.msg_type,
            "reply_to_id": m.reply_to_id,
            "metadata": {
                "word_count": m.word_count,
                "char_count": m.char_count,
                "has_emoji": m.has_emoji,
                "emojis": m.emojis or [],
                "has_url": m.has_url,
                "is_deleted": m.is_deleted,
                "has_media": m.has_media,
            },
        }
        for m in rows
    ]

    payload = {
        "ucj_version": "1.0",
        "meta": chat_upload.ucj_data,
        "messages": messages,
    }

    if download:
        # Strip the original extension and append .ucj.json so the saved file
        # is unambiguously identifiable.
        stem = chat_upload.filename.rsplit(".", 1)[0] or "chat"
        attachment_name = f"{stem}.ucj.json"
        return Response(
            content=json.dumps(payload, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{attachment_name}"'},
        )

    return JSONResponse(content=payload)


@router.websocket("/ws/{upload_id}")
async def upload_progress_ws(websocket: WebSocket, upload_id: UUID) -> None:
    """
    WebSocket feed of progress events for an upload.

    The connection stays open until either the client disconnects or the
    upload reaches a terminal stage (ready / failed). Sending anything on
    this socket is a no-op - the protocol is server-push only.
    """
    await websocket.accept()
    queue = await broker.subscribe(upload_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
            if event.stage in {"ready", "failed"}:
                # Give the client a moment to receive, then close cleanly.
                await asyncio.sleep(0.05)
                await websocket.close()
                return
    except WebSocketDisconnect:
        logger.debug("WS client disconnected for upload %s", upload_id)
    except Exception:
        logger.exception("Unexpected WS error for upload %s", upload_id)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        await broker.unsubscribe(upload_id, queue)


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------


async def _persist_upload(
    db: AsyncSession,
    upload_id: UUID,
    ucj: Any,  # UCJFile - typed loosely to avoid a circular import at module load
    detection: Any,  # DetectionResult
    filename: str,
) -> None:
    """
    Insert one ChatUpload row + bulk-insert all Message rows.

    Field names match the post-refactor models (`app.models.chat_upload`,
    `app.models.message`). If those aren't defined yet the import will
    fail at runtime - the parser layer remains importable and testable
    independently of the model layer.
    """
    # Local imports - models are still being introduced in a parallel branch,
    # so importing them at module load would prevent the parser tests from
    # running until the model files exist.
    from app.models import ChatUpload, Message, ProcessingStatus, SourcePlatform

    meta = ucj.meta

    # Map the detector's platform string to the SourcePlatform enum. PlatformType
    # and SourcePlatform share string values today; if a future detector adds
    # a value the enum doesn't know about, fall back to `unknown`.
    try:
        platform_enum = SourcePlatform(detection.platform.value)
    except ValueError:
        platform_enum = SourcePlatform.unknown

    # Auth lands in M03; until then attach uploads to a dev user so the FK holds.
    user_id = await _get_or_create_dev_user_id(db)

    chat_upload = ChatUpload(
        id=upload_id,
        user_id=user_id,
        platform=platform_enum,
        filename=filename,
        ucj_data=meta.model_dump(mode="json"),
        total_messages=meta.total_messages,
        status=ProcessingStatus.done,
    )
    db.add(chat_upload)
    # Flush so chat_upload.id is materialized for the FK on Message rows.
    await db.flush()

    # SQLAlchemy 2.0 bulk insert: `execute(insert(Model), rows)` skips ORM
    # row construction (same speed win as the legacy bulk_insert_mappings)
    # and works directly on AsyncSession with no run_sync detour.
    rows = [
        _message_to_mapping(upload_id, idx, msg)
        for idx, msg in enumerate(ucj.messages)
    ]
    if rows:
        # Stream the publish-progress events at coarse intervals (every 10k
        # rows) instead of per-row to avoid drowning the broker.
        batch_size = 10_000
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            await db.execute(insert(Message), batch)
            done = min(start + batch_size, len(rows))
            await _publish(
                upload_id, "persisting",
                0.7 + 0.25 * (done / len(rows)),  # ramp 0.7 -> 0.95 across persistence
                f"Saved {done:,}/{len(rows):,} messages",
            )

    await db.commit()


def _message_to_mapping(upload_id: UUID, msg_index: int, msg: Any) -> dict[str, Any]:
    """Flatten a UCJ Message pydantic model into a dict for bulk insert.

    Field names mirror the Message ORM columns in `app.models.message`.
    """
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


_DEV_USER_EMAIL = "dev@local"


async def _get_or_create_dev_user_id(db: AsyncSession) -> UUID:
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


async def _publish(
    upload_id: UUID, stage: ProcessingStage, progress: float, message: str
) -> None:
    """Convenience wrapper that builds + publishes a UploadProgressEvent."""
    await broker.publish(
        UploadProgressEvent(
            upload_id=upload_id,
            stage=stage,
            progress=progress,
            message=message,
            timestamp=datetime.now(timezone.utc),
        )
    )


def _enqueue_language_normalization(upload_id: UUID, ucj: Any) -> None:
    """Fire the Layer-4 normalize task. The task itself chains the NLP
    pipeline on success.

    We pass `messages` to honor the spec'd signature, but the task body
    re-fetches from Postgres (messages are already durable by this point);
    the argument round-trip is just for compatibility with callers that
    expect the (conversation_id, messages) shape. We deliberately send a
    compact projection (id + content) rather than the full UCJ to keep
    the Celery broker payload small on big chats.
    """
    try:
        # Local import — keeps the parser-test path import-light and avoids
        # a circular dep through `app.workers.celery_app`.
        from app.tasks.language_tasks import normalize_language_task

        compact = [
            {"id": m.id, "content": m.content}
            for m in getattr(ucj, "messages", [])
        ]
        normalize_language_task.delay(str(upload_id), compact)
        logger.info(
            "Enqueued language normalization for upload_id=%s (%d messages)",
            upload_id,
            len(compact),
        )
    except Exception:
        # Soft-fail: a missing broker shouldn't tank the upload response.
        # The downstream NLP pipeline still works against `content` if
        # `content_english` is null, so the user just loses translation.
        logger.warning(
            "Couldn't enqueue language normalization task; downstream "
            "services will read `content` instead of `content_english`",
            exc_info=True,
        )
