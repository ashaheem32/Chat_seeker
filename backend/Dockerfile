# ============================================================================
# ChatLens Backend - Multi-stage Dockerfile
# ----------------------------------------------------------------------------
# Stage 1 (builder): install Poetry + dependencies into a venv
# Stage 2 (runtime): slim final image with only the venv + app code
# ============================================================================

# ---- Builder stage ---------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.4 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

# Build deps: gcc/libpq for asyncpg/psycopg compilation, curl for installing Poetry
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="$POETRY_HOME/bin:$PATH"

WORKDIR /app

# Copy lock + manifest first to maximize Docker layer cache hits.
COPY pyproject.toml poetry.lock* ./

# Install only main dependencies (no dev) into the in-project venv.
RUN poetry install --only main --no-root

# ---- Runtime stage ---------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Runtime deps only: libpq for asyncpg, no compiler needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Run as non-root for security.
RUN useradd --create-home --shell /bin/bash --uid 1000 chatlens

WORKDIR /app

COPY --from=builder --chown=chatlens:chatlens /app/.venv /app/.venv
COPY --chown=chatlens:chatlens . /app

# Uploads dir must be writable by the app user.
RUN mkdir -p /app/uploads && chown chatlens:chatlens /app/uploads

USER chatlens

EXPOSE 8000

# Production default: 4 workers, no reload. Override in dev compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
