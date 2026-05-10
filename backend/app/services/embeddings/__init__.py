"""
Embedding generation + indexing.

Public surface:
    EmbeddingGenerator   — text → 1536-dim vector, with OpenAI primary
                            and a local sentence-transformers fallback.
    EmbeddingIndexer     — orchestrates "generate vectors for every message
                            in an upload, persist, advance status to done".
    IndexingResult       — typed return value of indexer.index_upload(...).
    EmbeddingCost        — token / cost accounting struct returned per batch.
"""

from app.services.embeddings.generator import (
    EmbeddingCost,
    EmbeddingGenerator,
    EmbeddingProvider,
    get_embedding_generator,
)
from app.services.embeddings.indexer import (
    EmbeddingIndexer,
    IndexingResult,
)

__all__ = [
    "EmbeddingCost",
    "EmbeddingGenerator",
    "EmbeddingIndexer",
    "EmbeddingProvider",
    "IndexingResult",
    "get_embedding_generator",
]
