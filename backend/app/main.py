"""
FastAPI application entry point.

Wires up:
- Settings & logging
- CORS middleware
- Lifespan (DB engine disposal on shutdown)
- Health check endpoint
- API v1 router (mounted under /api/v1)
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine

# ---- Logging ----------------------------------------------------------------
# Configure root logger early so module-level loggers in submodules pick it up.
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---- Lifespan ---------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup + shutdown hooks. Replaces deprecated @app.on_event."""
    logger.info("Starting %s v%s in %s mode", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)

    # Verify DB connectivity at boot - fail fast rather than at first request.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as e:
        logger.error("Database connection failed at startup: %s", e)
        # Re-raise in production so the container restarts; tolerate in dev to
        # let devs fix the DB without losing the dev server.
        if settings.is_production:
            raise

    yield

    logger.info("Shutting down - disposing DB engine")
    await engine.dispose()


# ---- App --------------------------------------------------------------------
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="AI-powered chat analysis API",
    lifespan=lifespan,
    # Hide docs in production unless explicitly enabled - reduces attack surface.
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json" if not settings.is_production else None,
)

# ---- CORS -------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ---- Health checks ----------------------------------------------------------
@app.get("/health", tags=["health"], status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is up, regardless of DB state."""
    return {"status": "ok", "version": settings.VERSION}


@app.get("/health/ready", tags=["health"])
async def readiness() -> JSONResponse:
    """
    Readiness probe. Returns 200 only if dependencies (DB) are reachable.
    K8s/load balancers should target this for traffic gating.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("Readiness check failed: %s", e)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "reason": "database"},
        )
    return JSONResponse(content={"status": "ready"})


# ---- Routers ----------------------------------------------------------------
# Imported here (not at top) to avoid circular imports during testing.
from app.routers import api_router  # noqa: E402

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# ---- Root -------------------------------------------------------------------
@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs" if not settings.is_production else "disabled",
    }
