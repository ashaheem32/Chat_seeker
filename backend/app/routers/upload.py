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
    Accept a chat export. The request returns as soon as the file is saved
    and a stub `ChatUpload` row exists; parsing + persistence + the NLP
    pipeline all run in a background Celery task. The client polls
    `GET /upload/{id}` (or subscribes to the WS feed) until status=ready.

    Query params:
        platform: Override auto-detection. Useful when the detector can't
            confidently classify a file.
    """
    from pathlib import Path

    from app.models import ChatUpload, ProcessingStatus, SourcePlatform
    from app.services.parser.detector import PlatformDetector
    from app.services.uploads import get_or_create_dev_user_id

    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filename is required")

    # ---- 0. Extension allow-list --------------------------------------
    # Reject unsupported file types up front so we don't read/persist bytes we
    # can't parse. Matches settings.ALLOWED_UPLOAD_EXTENSIONS (.json/.txt/.zip/.csv).
    ext = Path(file.filename).suffix.lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{ext or file.filename}'. Allowed: {allowed}",
        )

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

    # ---- 2. Persist raw bytes to disk so the worker can read them. -----
    upload_dir = Path(settings.UPLOAD_DIR) / str(upload_id)
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to /tmp if the configured UPLOAD_DIR isn't writable
        # (e.g. running outside Docker where /app/uploads doesn't exist).
        upload_dir = Path("/tmp/chatlens_uploads") / str(upload_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    file_path.write_bytes(raw)

    # ---- 3. Quick platform detection on the first 4KB ------------------
    # Full parsing happens in the worker; here we just want enough signal
    # for the response (detected_platform / confidence). PlatformDetector
    # only reads the head of the content, so passing a sample is cheap.
    try:
        sample = raw[:8192].decode("utf-8")
    except UnicodeDecodeError:
        sample = raw[:8192].decode("latin-1", errors="replace")
    detection = PlatformDetector.detect(sample, filename=file.filename)
    if platform is not None:
        # Explicit override beats sniffing.
        from app.services.parser.detector import DetectionResult

        detection = DetectionResult(
            platform=platform, confidence=1.0, reason="explicit override"
        )

    # ---- 4. Stub ChatUpload row at status=pending ----------------------
    try:
        platform_enum = SourcePlatform(detection.platform.value)
    except ValueError:
        platform_enum = SourcePlatform.unknown
    user_id = await get_or_create_dev_user_id(db)
    stub = ChatUpload(
        id=upload_id,
        user_id=user_id,
        platform=platform_enum,
        filename=file.filename,
        ucj_data={},
        total_messages=0,
        status=ProcessingStatus.pending,
    )
    db.add(stub)
    await db.commit()

    # ---- 5. Enqueue the parse task -------------------------------------
    # On success it chains language normalization → NLP → embeddings.
    try:
        from app.workers.tasks import parse_upload_task

        parse_upload_task.delay(
            str(upload_id),
            str(file_path),
            platform.value if platform is not None else None,
        )
    except Exception:
        logger.exception("Failed to enqueue parse_upload_task for %s", upload_id)
        # The row stays at `pending`. The frontend will see that and can
        # surface a retry. We don't 500 the response — the upload itself
        # succeeded.

    await _publish(upload_id, "queued", 0.05, "Queued for parsing")

    return UploadResponse(
        upload_id=upload_id,
        filename=file.filename,
        detected_platform=detection.platform.value,
        detection_confidence=detection.confidence,
        detection_reason=detection.reason,
        status="queued",
        meta=None,
        skipped_count=0,
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
    """Poll-friendly status endpoint.

    Source of truth is the `chat_uploads` row in Postgres because that's
    the only place the Celery worker can write to — the in-process broker
    lives in the FastAPI process and the worker is a separate process, so
    its events never reach this broker. We only consult the broker as a
    finer-grained progress overlay during the brief in-request stages
    (e.g. file save / queueing), and ignore it once the DB has advanced
    beyond `pending`.
    """
    from app.models import ChatUpload

    upload = await db.get(ChatUpload, upload_id)
    if upload is None:
        # Maybe the row hasn't been committed yet but the broker has a
        # cached event from the request handler — fall back to that.
        cached = broker.latest(upload_id)
        if cached is not None:
            return cached
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown upload id")

    db_status = _status_from_upload(upload)
    # If the DB row is still `pending`, the in-process broker may have a
    # finer-grained "queued"/"parsing" event with a richer message — prefer
    # it. Once the DB advances (worker started writing), the broker is
    # behind reality, so always prefer the DB.
    if upload.status.value == "pending":
        cached = broker.latest(upload_id)
        if cached is not None:
            return cached
    return db_status


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
