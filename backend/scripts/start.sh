#!/usr/bin/env bash
# ============================================================================
# Single-container entrypoint for free-tier hosts (Render free web service).
#
# Render's free plan has no background workers, so this script runs the
# Celery worker and the API side by side in one container:
#
#   1. alembic upgrade head      - schema + pgvector/pg_trgm/citext extensions
#   2. celery worker (background) - solo pool to stay inside 512 MB
#   3. uvicorn (foreground)       - single worker; $PORT is injected by Render
#
# The two processes must share a filesystem because the upload route writes
# the raw export to UPLOAD_DIR and the worker reads it back from there.
#
# Redis: Render's Key Value service exposes a single connection string with
# no database index. If CELERY_BROKER_URL / CELERY_RESULT_BACKEND are not set
# explicitly we derive them from REDIS_URL so compose-style /1 and /2 layouts
# keep working.
# ============================================================================
set -euo pipefail

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-1}"

if [[ -n "${REDIS_URL:-}" ]]; then
  base="${REDIS_URL%/}"
  # Strip a trailing /<db> if the platform already appended one.
  base="${base%/[0-9]}"
  export CELERY_BROKER_URL="${CELERY_BROKER_URL:-${base}/1}"
  export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-${base}/2}"
fi

echo "[start] running migrations"
alembic upgrade head

echo "[start] starting celery worker (concurrency=${CELERY_CONCURRENCY})"
celery -A app.workers.celery_app worker \
  --loglevel="${CELERY_LOG_LEVEL:-info}" \
  --pool=solo \
  --concurrency="${CELERY_CONCURRENCY}" \
  --without-gossip --without-mingle --without-heartbeat &
WORKER_PID=$!

# Forward termination to the worker so Render's deploys shut down cleanly.
cleanup() {
  echo "[start] shutting down worker"
  kill -TERM "$WORKER_PID" 2>/dev/null || true
  wait "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

echo "[start] starting api on :${PORT}"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --proxy-headers --forwarded-allow-ips="*"
