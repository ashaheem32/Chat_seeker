"""
Celery task definitions.

The NLP pipeline is async (FastAPI / SQLAlchemy AsyncSession), Celery's
worker is sync. We bridge the two by running the pipeline inside
`asyncio.run` from the task body. Each Celery task gets its own event
loop and its own DB session — no sharing across tasks.

Retry policy:
    Transient failures (DB blip, model download stalled) are worth
    retrying. Permanent failures (bad UCJ, corrupt message text) are not —
    we mark the upload as `failed` from inside the pipeline and return
    cleanly so Celery doesn't retry.

    `bind=True` lets the task call `self.retry()`. We retry up to 3 times
    with a 60-second countdown — enough time for a model registry hiccup
    or DB failover to recover.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from celery.exceptions import Ignore
from sqlalchemy.exc import DBAPIError, OperationalError

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backwards-compatibility alias
# ---------------------------------------------------------------------------


@celery_app.task(name="chatlens.analyze_chat", bind=True, max_retries=3)
def analyze_chat(self, chat_id: str) -> dict:
    """Older task name, kept so any in-flight queue items still resolve.
    Delegates to the new `process_upload_task`."""
    return process_upload_task.apply(args=[chat_id]).get()


# ---------------------------------------------------------------------------
# Main NLP task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="chatlens.process_upload",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # seconds; per-attempt countdown
    autoretry_for=(OperationalError, DBAPIError, ConnectionError),
    retry_backoff=False,  # we set countdown explicitly
    acks_late=True,
)
def process_upload_task(self, upload_id: str) -> dict:
    """
    Run the NLP pipeline against a freshly-uploaded chat.

    Argument is a string because Celery's default JSON serializer doesn't
    handle UUID — we cast at the boundary. Returns a small JSON-friendly
    summary used by Flower for at-a-glance status.

    Retries are scoped to *infrastructure* errors. Logical failures (bad
    upload, missing rows) are written to ChatUpload.processing_error inside
    the pipeline and we return without retrying — see `Ignore` below.
    """
    try:
        uid = UUID(upload_id)
    except ValueError:
        logger.error("Invalid upload_id passed to task: %r", upload_id)
        raise Ignore()  # noqa: RSE102 — Celery's documented "stop retrying" signal

    logger.info("[task=%s] NLP pipeline start upload_id=%s", self.request.id, uid)

    try:
        stats = asyncio.run(_run_pipeline(uid))
    except (OperationalError, DBAPIError, ConnectionError) as e:
        # The autoretry_for tuple should already catch these, but be
        # explicit so log lines say "retrying" not "failed".
        logger.warning(
            "[task=%s] transient error, retrying: %s", self.request.id, e
        )
        raise self.retry(exc=e)
    except Exception as e:
        # Unknown errors: the pipeline already wrote `failed` status to the
        # DB before re-raising. Don't retry — let the user see the error.
        logger.exception(
            "[task=%s] NLP pipeline failed permanently for upload_id=%s",
            self.request.id,
            uid,
        )
        return {
            "upload_id": str(uid),
            "status": "failed",
            "error": str(e),
        }

    # NLP succeeded. Hand off to the embedding stage. We use .delay() rather
    # than chord/chain so the embedding task gets its own retry budget and a
    # crash here doesn't cascade — the upload's `embedding` status is already
    # persisted by the NLP pipeline, so an operator can also kick this task
    # off manually if needed.
    generate_embeddings_task.delay(str(uid))
    logger.info("[task=%s] queued generate_embeddings_task for upload_id=%s", self.request.id, uid)

    return {
        "upload_id": str(uid),
        "status": "ok",
        "total_messages": stats["total_messages"],
        "processed_messages": stats["processed_messages"],
        "stage_seconds": stats["stage_seconds"],
    }


# ---------------------------------------------------------------------------
# Embedding task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="chatlens.generate_embeddings",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(OperationalError, DBAPIError, ConnectionError),
    retry_backoff=False,
    acks_late=True,
)
def generate_embeddings_task(self, upload_id: str) -> dict:
    """
    Generate vector embeddings for every message in `upload_id`.

    Triggered automatically by `process_upload_task` once the NLP pipeline
    completes. Can also be re-run manually if the embedding stage fails or
    if the embedding model is changed (delete the embedding column and
    re-run; the indexer is idempotent on `embedding IS NULL`).

    Returns the same shape regardless of whether OpenAI or the local model
    handled the work — provider is included so observers can see the path.
    """
    try:
        uid = UUID(upload_id)
    except ValueError:
        logger.error("Invalid upload_id passed to task: %r", upload_id)
        raise Ignore()  # noqa: RSE102

    logger.info("[task=%s] embedding indexer start upload_id=%s", self.request.id, uid)

    try:
        result = asyncio.run(_run_indexer(uid))
    except (OperationalError, DBAPIError, ConnectionError) as e:
        logger.warning("[task=%s] transient error, retrying: %s", self.request.id, e)
        raise self.retry(exc=e)
    except Exception as e:
        # Indexer already persisted `failed` status before re-raising.
        logger.exception(
            "[task=%s] embedding indexer failed permanently for upload_id=%s",
            self.request.id,
            uid,
        )
        return {
            "upload_id": str(uid),
            "status": "failed",
            "error": str(e),
        }

    return {
        "upload_id": str(uid),
        "status": "ok",
        **result,
    }


# ---------------------------------------------------------------------------
# Async runners
# ---------------------------------------------------------------------------


async def _run_pipeline(upload_id: UUID) -> dict:
    """Open a fresh AsyncSession, run the pipeline, return JSON-safe stats."""
    # Imports here (not at module top) so Celery autodiscovery doesn't pay
    # the torch / transformers import cost just to register the task.
    from app.core.database import make_worker_engine
    from app.services.nlp import NLPPipeline

    pipeline = NLPPipeline()

    engine, SessionLocal = make_worker_engine()
    try:
        async with SessionLocal() as session:
            stats = await pipeline.process_upload(upload_id, session, progress=None)
    finally:
        await engine.dispose()

    return {
        "total_messages": stats.total_messages,
        "processed_messages": stats.processed_messages,
        "stage_seconds": {s.stage: round(s.seconds, 2) for s in stats.stages},
    }


async def _run_indexer(upload_id: UUID) -> dict:
    """Open a fresh AsyncSession and run the embedding indexer."""
    from app.core.database import make_worker_engine
    from app.services.embeddings import EmbeddingIndexer

    indexer = EmbeddingIndexer()

    engine, SessionLocal = make_worker_engine()
    try:
        async with SessionLocal() as session:
            result = await indexer.index_upload(upload_id, session, progress=None)
    finally:
        await engine.dispose()

    return {
        "indexed_count": result.indexed_count,
        "skipped_count": result.skipped_count,
        "total_tokens_used": result.total_tokens_used,
        "estimated_cost_usd": round(result.estimated_cost_usd, 6),
        "provider": result.provider.value,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
    }
