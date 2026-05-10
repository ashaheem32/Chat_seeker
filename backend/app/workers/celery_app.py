"""
Celery application setup.

Tasks are autodiscovered from `app.workers.tasks`. Run a worker with:
    celery -A app.workers.celery_app worker --loglevel=info --pool=solo
"""

import os

from celery import Celery

from app.core.config import settings


# ---- Force CPU for PyTorch on macOS workers ---------------------------------
# PyTorch's MPS backend has caused SIGSEGV / SIGABRT during SentenceTransformer
# load on Apple Silicon (depends on torch + macOS build combinations). The
# pipeline runs fine on CPU at our batch sizes, so we disable MPS for worker
# processes by default. Set `CHATLENS_USE_MPS=1` to opt back in.
if os.getenv("CHATLENS_USE_MPS") != "1":
    try:  # pragma: no cover - guarded import
        import torch  # type: ignore[import-not-found]

        if hasattr(torch.backends, "mps"):
            torch.backends.mps.is_available = lambda: False  # type: ignore[method-assign]
            torch.backends.mps.is_built = lambda: False  # type: ignore[method-assign]
    except ImportError:
        # torch isn't installed; nothing to disable.
        pass


celery_app = Celery(
    "chatlens",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Module list: every file that registers @celery_app.task decorators.
    # `app.tasks.language_tasks` runs the Layer-4 language normalization
    # before the NLP pipeline kicks in — see the upload route for the
    # chain order.
    include=[
        "app.workers.tasks",
        "app.tasks.language_tasks",
    ],
)

# Production-friendly defaults.
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Prevent silent task loss: workers ack only after success, broker holds tasks if worker dies.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Retries for transient failures (LLM rate limits, DB blips).
    task_default_retry_delay=10,
    task_max_retries=3,
)
