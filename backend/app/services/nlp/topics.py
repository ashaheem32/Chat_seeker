"""
Topic / keyword extraction.

Two distinct operations live here, sharing a sentence-transformer backbone:

1. KeyBERT — per-message keyword extraction (zero-shot, no training).
   Given a single message, returns the 3-5 most representative phrases.
   Stored in Message.topics.

2. BERTopic — conversation-level macro-topic clustering. Given the whole
   chat, groups semantically-similar messages into topics with human-
   readable labels ("travel plans", "work venting", ...). Stored in
   ChatUpload.ucj_data['ai_analysis']['topic_clusters'] for the dashboard.

Both pieces are cached per process; BERTopic models are also cached per
upload_id so the pipeline can re-fit on incremental adds without
regenerating the embeddings every time. The cache is process-local —
across worker restarts the model is rebuilt from scratch, which is fine
because BERTopic on 50k short messages takes ~30s on CPU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


_DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"  # 80 MB, fast, good enough for chat.


@dataclass(slots=True)
class TopicLabel:
    """One topic in a fitted conversation model."""

    topic_id: int
    label: str
    keywords: list[str]
    size: int  # number of messages assigned to this topic


class TopicExtractor:
    """Per-message KeyBERT + conversation-level BERTopic."""

    def __init__(self, embedding_model: str = _DEFAULT_EMBED_MODEL) -> None:
        self.embedding_model_name = embedding_model
        self._embedding_model: Any = None  # SentenceTransformer
        self._keybert: Any = None
        self._unavailable: bool = False

        # upload_id -> (BERTopic, topic_id -> Message msg_index list).
        # We hold the fitted model so callers can later transform new
        # messages against it (e.g. when adding to an existing chat).
        self._topic_cache: dict[UUID, dict[str, Any]] = {}

    # ---- Loading ---------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._keybert is not None or self._unavailable:
            return
        try:
            from keybert import KeyBERT
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformer %s", self.embedding_model_name)
            self._embedding_model = SentenceTransformer(self.embedding_model_name)
            self._keybert = KeyBERT(model=self._embedding_model)
        except Exception as e:  # pragma: no cover
            logger.warning(
                "KeyBERT/SentenceTransformer unavailable (%s); topic extraction disabled",
                e,
            )
            self._unavailable = True

    # ---- Per-message keywords -------------------------------------------
    def extract_keywords(self, text: str, top_n: int = 5) -> list[str]:
        """Return the top N keyphrases for a single message. Returns []
        for empty / very-short text or when the backing model is unavailable."""
        if not text or len(text.split()) < 3:
            return []

        self._ensure_loaded()
        if self._unavailable:
            return []

        try:
            # keyphrase_ngram_range=(1,2): single words and bigrams. Bigrams
            # are usually the most informative ("birthday party", "next week")
            # but allowing unigrams catches single salient nouns.
            # stop_words='english': removes "the", "is", etc. without us
            # having to maintain a list.
            results = self._keybert.extract_keywords(
                text,
                keyphrase_ngram_range=(1, 2),
                stop_words="english",
                top_n=top_n,
                use_mmr=True,        # diversity instead of redundancy
                diversity=0.5,
            )
        except Exception as e:
            # KeyBERT raises on near-empty token sets after stopword removal.
            logger.debug("KeyBERT failed for short text: %s", e)
            return []

        # KeyBERT returns [(phrase, score), ...] sorted desc.
        return [phrase for phrase, _score in results]

    def extract_keywords_batch(
        self, texts: list[str], top_n: int = 5
    ) -> list[list[str]]:
        """Bulk per-message extraction. Currently sequential; KeyBERT itself
        can't process a batch in one call without losing the per-doc result
        structure. If this becomes a bottleneck, switch to embedding all
        messages once and computing cosine sim per message manually."""
        return [self.extract_keywords(t, top_n=top_n) for t in texts]

    # ---- Conversation-level topic modelling -----------------------------
    def fit_conversation(
        self,
        upload_id: UUID,
        messages: list[str],
        min_topic_size: int | None = None,
    ) -> dict[int, str]:
        """Cluster a conversation's messages into macro-topics.

        Returns a dict {message_index → topic_label}. message_index is the
        position of the message in the input list (0-based). Topic ids of -1
        from BERTopic mean "outlier" — we map those to 'misc' rather than
        leave them unlabeled.
        """
        if not messages:
            return {}

        self._ensure_loaded()
        if self._unavailable:
            return {}

        try:
            from bertopic import BERTopic
            from bertopic.representation import KeyBERTInspired
            from sklearn.feature_extraction.text import CountVectorizer
        except ImportError:
            logger.warning("BERTopic not installed; skipping conversation-level topics")
            return {}

        # Reasonable defaults — small chats need small min_topic_size.
        # Default heuristic: 1% of messages, clamped to [5, 50].
        if min_topic_size is None:
            min_topic_size = max(5, min(50, max(1, len(messages) // 100)))

        try:
            # Pre-compute embeddings once — BERTopic accepts them and skips
            # internal re-embedding, which is the slowest step.
            embeddings = self._embedding_model.encode(
                messages,
                show_progress_bar=False,
                convert_to_numpy=True,
                batch_size=64,
            )

            # CountVectorizer with a stopword list keeps topic labels clean.
            vectorizer = CountVectorizer(stop_words="english", min_df=2)

            # KeyBERTInspired representation gives short, readable topic names.
            representation = KeyBERTInspired()

            model = BERTopic(
                embedding_model=self._embedding_model,
                vectorizer_model=vectorizer,
                representation_model=representation,
                min_topic_size=min_topic_size,
                calculate_probabilities=False,
                verbose=False,
            )

            topic_ids, _probs = model.fit_transform(messages, embeddings=embeddings)
        except Exception as e:
            logger.warning("BERTopic fit failed (%s); returning empty topics", e)
            return {}

        # Build {topic_id → readable_label} once, then expand per-message.
        topic_info = model.get_topic_info()
        id_to_label: dict[int, str] = {}
        for _, row in topic_info.iterrows():
            tid = int(row["Topic"])
            if tid == -1:
                id_to_label[tid] = "misc"
                continue
            # BERTopic's "Name" column has the form "0_word1_word2_word3";
            # split off the numeric prefix, keep the first 3 words, format
            # as a readable phrase.
            name = str(row.get("Name", ""))
            tokens = [
                tok for tok in name.split("_")[1:] if tok and not tok.isdigit()
            ]
            id_to_label[tid] = " ".join(tokens[:3]) if tokens else f"topic {tid}"

        msg_idx_to_label = {
            i: id_to_label.get(int(tid), "misc")
            for i, tid in enumerate(topic_ids)
        }

        # Cache the fitted model + label map for later .transform() calls.
        self._topic_cache[upload_id] = {
            "model": model,
            "id_to_label": id_to_label,
        }

        return msg_idx_to_label

    def get_topic_summary(self, upload_id: UUID) -> list[TopicLabel]:
        """Return the topic table for the most recent fit on this upload.
        Suitable for the dashboard's 'topics in this conversation' panel."""
        cache = self._topic_cache.get(upload_id)
        if not cache:
            return []
        model = cache["model"]
        id_to_label: dict[int, str] = cache["id_to_label"]
        info = model.get_topic_info()

        out: list[TopicLabel] = []
        for _, row in info.iterrows():
            tid = int(row["Topic"])
            label = id_to_label.get(tid, "misc")
            top_words = [w for w, _ in (model.get_topic(tid) or [])][:5]
            out.append(
                TopicLabel(
                    topic_id=tid,
                    label=label,
                    keywords=top_words,
                    size=int(row["Count"]),
                )
            )
        return out

    def clear_cache(self, upload_id: UUID | None = None) -> None:
        """Drop cached models. Call when an upload is deleted, or on memory
        pressure. With no argument, clears everything."""
        if upload_id is None:
            self._topic_cache.clear()
        else:
            self._topic_cache.pop(upload_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: TopicExtractor | None = None


def get_topic_extractor() -> TopicExtractor:
    global _singleton
    if _singleton is None:
        _singleton = TopicExtractor()
    return _singleton
