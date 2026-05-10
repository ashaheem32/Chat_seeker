"""
Search endpoints.

Three operations, all scoped to a single upload:
    POST /api/search/{upload_id}                     — NL Q&A
    GET  /api/search/{upload_id}/suggestions         — pre-canned queries
    GET  /api/search/{upload_id}/similar/{message_id} — vector lookup

The upload must exist and be in `done` status before search works — we
404 on missing and 409 on "still processing" so the frontend can show a
useful state instead of empty results.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import ChatUpload, Message, ProcessingStatus
from app.schemas.message import MessageRead
from app.schemas.search import (
    MessageContext,
    NLSearchRequest,
    NLSearchResponse,
    SimilarMessagesResponse,
    SuggestionsResponse,
)
from app.services.search import (
    DEFAULT_SUGGESTIONS,
    NaturalLanguageSearch,
    SemanticSearchService,
    get_nl_search,
    get_semantic_search,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies + guards
# ---------------------------------------------------------------------------


async def _load_searchable_upload(
    upload_id: UUID, db: AsyncSession
) -> ChatUpload:
    """Fetch the upload and require it to be ready for search.

    404 if it doesn't exist (or belongs to another user — we don't yet enforce
    user scoping in this router; that wiring lands with the auth middleware).
    409 if it's still processing — gives the UI a clear "come back soon"
    distinction from "nothing to find".
    """
    upload = await db.get(ChatUpload, upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload {upload_id} not found",
        )
    if upload.status == ProcessingStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload processing failed: {upload.processing_error or 'unknown error'}",
        )
    if upload.status != ProcessingStatus.done:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Upload is still processing (status={upload.status.value}). "
                "Search is available once status reaches 'done'."
            ),
        )
    return upload


# ---------------------------------------------------------------------------
# POST /api/search/{upload_id}  — NL Q&A
# ---------------------------------------------------------------------------


@router.post(
    "/{upload_id}",
    response_model=NLSearchResponse,
    summary="Ask a natural-language question about a chat",
)
async def search_chat(
    upload_id: UUID,
    body: NLSearchRequest,
    db: AsyncSession = Depends(get_db),
    nl: NaturalLanguageSearch = Depends(get_nl_search),
) -> NLSearchResponse:
    await _load_searchable_upload(upload_id, db)
    try:
        return await nl.answer_query(
            query=body.query,
            upload_id=upload_id,
            db=db,
            filters=body.filters,
            top_k=body.top_k,
        )
    except RuntimeError as e:
        # Embedding backend unavailable (invalid OpenAI key, no local fallback
        # installed). Surface as 503 with the underlying reason so the search
        # panel renders a focused message instead of a generic 500.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Search unavailable: {e}",
        ) from e


# ---------------------------------------------------------------------------
# GET /api/search/{upload_id}/suggestions
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/suggestions",
    response_model=SuggestionsResponse,
    summary="Pre-canned query suggestions for a chat",
)
async def get_suggestions(
    upload_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SuggestionsResponse:
    """Returns the curated suggestion list. We still validate the upload
    exists so the dashboard sees the same auth/availability errors here
    as on the search endpoint — even though the suggestions themselves
    are upload-independent today."""
    await _load_searchable_upload(upload_id, db)
    return SuggestionsResponse(
        upload_id=upload_id,
        suggestions=DEFAULT_SUGGESTIONS,
    )


# ---------------------------------------------------------------------------
# GET /api/search/{upload_id}/similar/{message_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/similar/{message_id}",
    response_model=SimilarMessagesResponse,
    summary="Find messages semantically similar to a given message",
)
async def find_similar_messages(
    upload_id: UUID,
    message_id: UUID,
    top_k: int = 10,
    db: AsyncSession = Depends(get_db),
    semantic: SemanticSearchService = Depends(get_semantic_search),
) -> SimilarMessagesResponse:
    await _load_searchable_upload(upload_id, db)

    source = await db.get(Message, message_id)
    if source is None or source.upload_id != upload_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found in upload {upload_id}",
        )
    if source.embedding is None:
        # Source wasn't embedded (skipped during indexing — media-only,
        # deleted, etc). Return empty rather than synthesizing a vector.
        return SimilarMessagesResponse(
            upload_id=upload_id,
            source_message_id=message_id,
            results=[],
        )

    results = await semantic.find_similar_to_message(
        message_id=message_id,
        upload_id=upload_id,
        db=db,
        top_k=max(1, min(top_k, 50)),
    )
    return SimilarMessagesResponse(
        upload_id=upload_id,
        source_message_id=message_id,
        results=results,
    )


# ---------------------------------------------------------------------------
# POST /api/search/{upload_id}/stream  — SSE streaming Q&A
# ---------------------------------------------------------------------------


@router.post(
    "/{upload_id}/stream",
    summary="Streaming variant of the NL Q&A search (Server-Sent Events)",
    response_class=StreamingResponse,
)
async def search_chat_stream(
    upload_id: UUID,
    body: NLSearchRequest,
    db: AsyncSession = Depends(get_db),
    nl: NaturalLanguageSearch = Depends(get_nl_search),
) -> StreamingResponse:
    """Server-Sent Events feed of a single search.

    Event sequence (happy path):
        event: meta    — rephrased_query, evidence list, search_method
        event: delta   — text chunks (one per Anthropic streaming delta)
        event: done    — final answer + cited_messages + confidence

    Other events:
        event: error   — message string. Stream closes after.

    The frontend uses fetch() + ReadableStream rather than EventSource
    because EventSource doesn't support POST bodies and we need filters
    + top_k in the request body. SSE wire format is the same either way.
    """
    await _load_searchable_upload(upload_id, db)

    async def event_source():
        # FastAPI streaming generator: each yielded string is a complete SSE
        # event. We keep a small heartbeat by flushing meta first so the
        # browser doesn't buffer until the LLM finishes.
        try:
            async for event in nl.answer_query_streaming(
                query=body.query,
                upload_id=upload_id,
                db=db,
                filters=body.filters,
                top_k=body.top_k,
            ):
                yield _format_sse(event["type"], event)
        except Exception as e:
            # Last-resort safety net. The streaming method handles its own
            # errors; this catches anything that escaped (e.g. DB blip
            # mid-stream) so the client always sees a terminal event.
            logger.exception("SSE stream blew up unexpectedly")
            yield _format_sse("error", {"type": "error", "message": str(e)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # Disable middlebox buffering so chunks reach the browser
            # immediately. Honored by nginx and most CDNs.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _format_sse(event_name: str, payload: object) -> str:
    """Encode a single Server-Sent Event. The leading `event:` is optional
    in the spec but lets the client `addEventListener('meta', …)` cleanly.

    Typed at `object` because the streaming generator yields TypedDicts,
    which aren't structurally assignable to plain `dict`. json.dumps
    handles them transparently."""
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


# ---------------------------------------------------------------------------
# GET /api/search/{upload_id}/context/{message_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{upload_id}/context/{message_id}",
    response_model=MessageContext,
    summary="Window of messages surrounding a target message (for the context drawer)",
)
async def get_message_context(
    upload_id: UUID,
    message_id: UUID,
    window: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Number of messages to include before and after the target.",
    ),
    db: AsyncSession = Depends(get_db),
) -> MessageContext:
    await _load_searchable_upload(upload_id, db)

    target = await db.get(Message, message_id)
    if target is None or target.upload_id != upload_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found in upload {upload_id}",
        )

    # Pull the surrounding messages in two index-friendly queries: one for
    # `before` (msg_index < target, ORDER DESC LIMIT N, then reverse), one
    # for `after` (msg_index > target, ORDER ASC LIMIT N).
    before_stmt = (
        select(Message)
        .where(Message.upload_id == upload_id)
        .where(Message.msg_index < target.msg_index)
        .order_by(Message.msg_index.desc())
        .limit(window)
    )
    after_stmt = (
        select(Message)
        .where(Message.upload_id == upload_id)
        .where(Message.msg_index > target.msg_index)
        .order_by(Message.msg_index.asc())
        .limit(window)
    )
    before_rows = list((await db.execute(before_stmt)).scalars().all())
    after_rows = list((await db.execute(after_stmt)).scalars().all())
    # Reverse `before` so the list reads chronologically (oldest first).
    before_rows.reverse()

    return MessageContext(
        upload_id=upload_id,
        target=MessageRead.model_validate(target),
        before=[MessageRead.model_validate(m) for m in before_rows],
        after=[MessageRead.model_validate(m) for m in after_rows],
    )
