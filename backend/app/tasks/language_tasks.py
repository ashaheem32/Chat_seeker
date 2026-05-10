"""
Celery tasks for the language normalization layer.

The single task here, `normalize_language_task`, runs after the upload
endpoint persists messages to Postgres. It:
    1. Reads pending (un-normalized) messages from the DB.
    2. Hands them to LanguageNormalizer.normalize_upload, which detects
       + translates + writes content_english back to every row.
    3. On success, kicks off the existing NLP pipeline so the dashboard
       runs against translated text.

We accept an optional `messages` argument so callers can use the
spec'd signature `normalize_language_task.delay(conversation_id,
messages)` — but the task body ignores it. Messages are already
durable in Postgres by the time this runs, so re-serializing thousands
of rows through Celery's broker would be wasteful. The argument is
kept on the signature only to honor existing callers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery.exceptions import Ignore
from sqlalchemy.exc import DBAPIError, OperationalError

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="chatlens.normalize_language",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(OperationalError, DBAPIError, ConnectionError),
    retry_backoff=False,
    acks_late=True,
)
def normalize_language_task(
    self,
    upload_id: str,
    messages: Any = None,  # noqa: ARG001 — see module docstring
) -> dict:
    """
    Detect + translate every non-English message for `upload_id`, write
    `content_english` / `original_language` / `was_translated`, then chain
    the NLP pipeline.

    Argument is a string because Celery's default JSON serializer doesn't
    handle UUID — we cast at the boundary. `messages` is accepted for
    signature compatibility but ignored; we re-fetch from Postgres.
    """
    try:
        uid = UUID(upload_id)
    except ValueError:
        logger.error("Invalid upload_id passed to language task: %r", upload_id)
        raise Ignore()  # noqa: RSE102 — Celery's documented "stop retrying" signal

    logger.info("[task=%s] language normalize start upload_id=%s", self.request.id, uid)

    try:
        result = asyncio.run(_run_normalizer(uid))
    except (OperationalError, DBAPIError, ConnectionError) as e:
        logger.warning(
            "[task=%s] transient error during language normalize, retrying: %s",
            self.request.id, e,
        )
        raise self.retry(exc=e)
    except Exception as e:
        # Soft-fail: persist nothing, but DON'T retry (the underlying issue
        # is likely a bad row or LLM-side blocker; retrying won't help).
        # The downstream NLP task can still run against the original
        # `content` column thanks to the fallback in pipeline.py.
        logger.exception(
            "[task=%s] language normalize failed permanently for upload_id=%s",
            self.request.id, uid,
        )
        # Still chain the NLP task so the dashboard isn't blocked by a
        # translation failure on a single chat.
        _enqueue_nlp(uid)
        return {"upload_id": str(uid), "status": "failed", "error": str(e)}

    # On success, fire the NLP pipeline. (process_upload_task itself chains
    # generate_embeddings_task on completion, so we only kick off NLP here.)
    _enqueue_nlp(uid)

    return {
        "upload_id": str(uid),
        "status": "ok",
        "total_messages": result["total_messages"],
        "translated_count": result["translated_count"],
        "cache_hits": result["cache_hits"],
        "api_calls": result["api_calls"],
        "by_category": result["by_category"],
        "elapsed_seconds": result["elapsed_seconds"],
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _run_normalizer(upload_id: UUID) -> dict:
    """Open a fresh AsyncSession, run the normalizer, return JSON-safe stats."""
    # Local imports — keeps Celery autodiscovery cheap (the worker doesn't
    # need to load SQLAlchemy / Anthropic just to register the task).
    from app.core.database import make_worker_engine
    from app.services.language import language_normalizer

    engine, SessionLocal = make_worker_engine()
    try:
        async with SessionLocal() as session:
            result = await language_normalizer.normalize_upload(
                upload_id, session, progress=None
            )
    finally:
        await engine.dispose()

    return {
        "total_messages": result.total_messages,
        "translated_count": result.translated_count,
        "cache_hits": result.cache_hits,
        "api_calls": result.api_calls,
        "by_category": result.by_category,
        "elapsed_seconds": result.elapsed_seconds,
        "error": result.error,
    }


def _enqueue_nlp(upload_id: UUID) -> None:
    """Best-effort enqueue of the existing NLP task. Kept tolerant of
    Celery being misconfigured in tests so we don't bail the upload over
    a missing follow-on stage."""
    try:
        from app.workers.tasks import process_upload_task

        process_upload_task.delay(str(upload_id))
        logger.info("Enqueued NLP pipeline for upload_id=%s", upload_id)
    except Exception:
        # The most common cause is "no Celery broker configured" in tests.
        # Don't let it kill the language task's success path.
        logger.warning(
            "Couldn't enqueue NLP task after language normalize", exc_info=True
        )
