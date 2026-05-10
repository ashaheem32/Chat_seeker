"""
Search services.

    SemanticSearchService     — pgvector cosine similarity search over Message
                                 embeddings, with optional post-filters.
    NaturalLanguageSearch     — wraps SemanticSearchService with a Claude-
                                 powered rephrase + answer step.
    DEFAULT_SUGGESTIONS       — the curated query suggestion list returned
                                 from the /suggestions endpoint.
"""

from app.services.search.nl_search import (
    DEFAULT_SUGGESTIONS,
    NaturalLanguageSearch,
    get_nl_search,
)
from app.services.search.semantic_search import (
    SemanticSearchService,
    get_semantic_search,
)

__all__ = [
    "DEFAULT_SUGGESTIONS",
    "NaturalLanguageSearch",
    "SemanticSearchService",
    "get_nl_search",
    "get_semantic_search",
]
