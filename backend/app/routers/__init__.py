"""
API routers.

Submodule routers are aggregated into a single `api_router` that gets mounted
under `/api/v1` in main.py. Add new routers here so they're automatically
included in the API surface.
"""

from fastapi import APIRouter

from app.routers import chats, search, stats, upload

api_router = APIRouter()
# `/upload` (singular) - upload, status poll, and WebSocket progress feed.
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
# `/conversations` - the curated public-facing view over ChatUpload rows
# (list / detail / status polling). Replaces the older /chats stub.
api_router.include_router(
    chats.router, prefix="/conversations", tags=["conversations"]
)
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
