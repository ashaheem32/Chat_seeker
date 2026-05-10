"""
Pydantic schemas for the search API.

Two layers:
    SearchFilters / SearchResult  — used by the raw semantic search endpoint.
    NLSearchRequest / NLSearchResponse / CitedMessage  — used by the
        Claude-backed natural language Q&A endpoint.

Both layers share `ContextWindow`, the small "messages immediately before
and after the hit" structure that the dashboard renders inline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.message import EmotionLabel, MessageRead, SentimentLabel


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class SearchFilters(BaseModel):
    """Optional post-retrieval filters. All fields are AND-combined."""

    sender: str | None = Field(
        default=None,
        max_length=255,
        description="Restrict to messages from this exact sender.",
    )
    date_from: datetime | None = None
    date_to: datetime | None = None
    emotion_label: EmotionLabel | None = None
    sentiment_label: SentimentLabel | None = None
    msg_type: str | None = Field(
        default=None,
        max_length=32,
        description="e.g. 'text' to exclude media-only matches.",
    )
    min_similarity: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Cosine-similarity floor. Hits below this are dropped.",
    )


# ---------------------------------------------------------------------------
# Raw semantic search results
# ---------------------------------------------------------------------------


class ContextWindow(BaseModel):
    """A handful of messages around a hit, for in-place context in the UI."""

    before: list[MessageRead] = Field(default_factory=list)
    after: list[MessageRead] = Field(default_factory=list)


class SearchResult(BaseModel):
    """One pgvector hit. similarity is in [0, 1] — already converted from
    cosine distance by the search service."""

    model_config = ConfigDict(from_attributes=True)

    message: MessageRead
    similarity: float = Field(..., ge=0.0, le=1.0)
    context: ContextWindow | None = None


class SearchResponse(BaseModel):
    """Wrapper for the bare semantic-search endpoint."""

    upload_id: UUID
    query: str
    rephrased_query: str | None = None
    total_hits: int
    results: list[SearchResult]


# ---------------------------------------------------------------------------
# NL Q&A
# ---------------------------------------------------------------------------


SearchMethod = Literal["semantic", "nl_qa", "hybrid"]


class NLSearchRequest(BaseModel):
    """Body of POST /api/search/{upload_id}."""

    query: str = Field(..., min_length=1, max_length=2000)
    filters: SearchFilters | None = None
    top_k: int = Field(default=20, ge=1, le=100)


class CitedMessage(BaseModel):
    """A message Claude pointed to in its answer. message_id matches
    Message.msg_id (the UCJ-level id like "msg_42") so the frontend can
    deep-link directly into the timeline view."""

    message_id: str
    sender: str
    timestamp: datetime
    content: str
    similarity: float = Field(..., ge=0.0, le=1.0)


class NLSearchResponse(BaseModel):
    """Returned from POST /api/search/{upload_id}."""

    upload_id: UUID
    query: str
    rephrased_query: str = Field(
        ...,
        description="The model's reformulation of the user's query, used for retrieval.",
    )
    answer: str = Field(
        ...,
        description="Claude's natural-language answer, citing specific messages.",
    )
    cited_messages: list[CitedMessage]
    search_method: SearchMethod = "nl_qa"
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Heuristic confidence: derived from top similarity + answer length.",
    )


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


class QuerySuggestion(BaseModel):
    """A pre-canned question the dashboard surfaces as a clickable chip.
    `category` lets the UI group them ("relationship", "content", ...)."""

    label: str
    query: str
    category: str = "general"


class SuggestionsResponse(BaseModel):
    upload_id: UUID
    suggestions: list[QuerySuggestion]


# ---------------------------------------------------------------------------
# Similar-message endpoint
# ---------------------------------------------------------------------------


class SimilarMessagesResponse(BaseModel):
    """Response from GET /api/search/{upload_id}/similar/{message_id}."""

    upload_id: UUID
    source_message_id: UUID
    results: list[SearchResult]


# ---------------------------------------------------------------------------
# Conversation context (drawer)
# ---------------------------------------------------------------------------


class MessageContext(BaseModel):
    """N messages on either side of a target message — feeds the
    "See in context" drawer launched from a search result.

    The target message is included in `target` rather than between
    `before` and `after` so the frontend can render a highlighted
    treatment regardless of how many surrounding rows came back."""

    upload_id: UUID
    target: MessageRead
    before: list[MessageRead] = Field(default_factory=list)
    after: list[MessageRead] = Field(default_factory=list)

