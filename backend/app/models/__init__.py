"""
SQLAlchemy ORM models.

Every model must be imported here so that:
1. Alembic's --autogenerate can see them when scanning Base.metadata.
2. Relationships referenced by string name (e.g. "User") resolve correctly.
"""

from app.models.analysis_cache import AnalysisCache
from app.models.chat_upload import ChatUpload, ProcessingStatus, SourcePlatform
from app.models.message import Message
from app.models.user import User

__all__ = [
    "AnalysisCache",
    "ChatUpload",
    "Message",
    "ProcessingStatus",
    "SourcePlatform",
    "User",
]
