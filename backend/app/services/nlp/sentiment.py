"""
Sentiment analysis.

Primary model: cardiffnlp/twitter-roberta-base-sentiment-latest. Trained on
~124M tweets and labeled across {negative, neutral, positive} — better suited
to chat than the standard SST-2 / IMDB baselines.

Fallback: VADER (valence-aware lexicon). VADER is fully rule-based, ships
in pure Python, and gives reasonable results when the transformer model is
unavailable (no GPU, no network at install time, model download blocked).
We auto-fallback rather than failing the whole pipeline.

Long-text handling:
    The roberta tokenizer caps at 512 tokens (~350 English words). Chat
    messages rarely exceed this, but pasted articles / forwarded long-form
    do. We chunk over the tokenized form and average the per-chunk
    probability vectors before picking the label. Averaging probabilities
    is more honest than averaging the discrete label, since two neutral
    halves shouldn't outweigh one strong-positive half.

Memory:
    The model + tokenizer is ~500 MB on disk and ~600 MB resident. We hold
    it in a module-level singleton — re-loading per task would dominate
    runtime. Workers should be memory-budgeted accordingly (~1.5 GB
    headroom for sentiment + emotion together).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SentimentResult:
    """Output of a single sentiment inference."""

    label: str  # "positive" | "negative" | "neutral"
    # Compound score in [-1, +1]: +prob_positive - prob_negative.
    # Lets callers do arithmetic / aggregation without re-deriving from raw.
    score: float
    raw_scores: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


_DEFAULT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
# twitter-roberta uses LABEL_0/1/2 internally. We map to readable labels.
_ROBERTA_LABEL_MAP = {0: "negative", 1: "neutral", 2: "positive"}


class SentimentAnalyzer:
    """Lazy-loading sentiment analyzer with VADER fallback."""

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        device: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self._device_pref = device
        # Loaded lazily.
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._device: Any = None
        self._vader: Any = None
        self._using_fallback: bool = False

    # ---- Loading ---------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._model is not None or self._using_fallback:
            return
        try:
            self._load_transformer()
        except Exception as e:  # pragma: no cover — environment-dependent
            logger.warning(
                "Transformer sentiment model failed to load (%s); falling back to VADER",
                e,
            )
            self._load_vader()

    def _load_transformer(self) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self._device = torch.device(self._device_pref or _select_device(torch))
        logger.info("Loading sentiment model %s on %s", self.model_name, self._device)

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        model.eval()
        model.to(self._device)
        self._model = model

    def _load_vader(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._vader = SentimentIntensityAnalyzer()
        self._using_fallback = True
        logger.info("Sentiment fallback active: VADER")

    # ---- Public API ------------------------------------------------------
    def analyze(self, text: str) -> SentimentResult:
        if not text or not text.strip():
            return SentimentResult(label="neutral", score=0.0, raw_scores={})

        self._ensure_loaded()

        if self._using_fallback:
            return self._vader_analyze(text)

        return self._batch_infer([text])[0]

    def analyze_batch(
        self, texts: list[str], batch_size: int | None = None
    ) -> list[SentimentResult]:
        """Run inference on a list of texts. Empty strings produce a neutral
        result with score=0 without consuming model time."""
        if not texts:
            return []

        self._ensure_loaded()

        if self._using_fallback:
            return [self._vader_analyze(t) for t in texts]

        bs = batch_size or _default_batch_size(self._torch, self._device)
        results: list[SentimentResult] = []
        for start in range(0, len(texts), bs):
            chunk = texts[start : start + bs]
            results.extend(self._batch_infer(chunk))
        return results

    # ---- Internals -------------------------------------------------------
    def _batch_infer(self, texts: list[str]) -> list[SentimentResult]:
        """Infer a single batch through the transformer."""
        torch = self._torch

        # Identify long texts; for those we'll do the chunk-and-average dance
        # one at a time. Short texts go through as a single padded batch.
        long_indices: list[int] = []
        short_indices: list[int] = []
        encoded_lens: list[int] = []
        for i, t in enumerate(texts):
            n_tokens = len(self._tokenizer.encode(t or " ", add_special_tokens=False))
            encoded_lens.append(n_tokens)
            if n_tokens > self.max_tokens - 2:  # account for [CLS]/[SEP]
                long_indices.append(i)
            else:
                short_indices.append(i)

        out: list[SentimentResult | None] = [None] * len(texts)

        if short_indices:
            short_texts = [texts[i] or " " for i in short_indices]
            enc = self._tokenizer(
                short_texts,
                padding=True,
                truncation=True,
                max_length=self.max_tokens,
                return_tensors="pt",
            ).to(self._device)

            with torch.no_grad():
                logits = self._model(**enc).logits
            probs = torch.softmax(logits, dim=-1).cpu().numpy()

            for offset, idx in enumerate(short_indices):
                out[idx] = self._probs_to_result(probs[offset])

        for idx in long_indices:
            out[idx] = self._infer_long(texts[idx])

        # mypy / runtime guard — every slot should be filled.
        return [r if r is not None else SentimentResult("neutral", 0.0, {}) for r in out]

    def _infer_long(self, text: str) -> SentimentResult:
        """Chunk a long text into ≤max_tokens windows, infer each, average
        the probability vectors (NOT the labels — see module docstring)."""
        torch = self._torch
        # Tokenize once without truncation to get the full id sequence.
        ids = self._tokenizer.encode(text, add_special_tokens=False)

        # Reserve 2 slots for special tokens [CLS], [SEP].
        window = self.max_tokens - 2
        chunks = [ids[i : i + window] for i in range(0, len(ids), window)]

        agg_probs = None
        cls_id = self._tokenizer.cls_token_id
        sep_id = self._tokenizer.sep_token_id

        for chunk_ids in chunks:
            framed = [cls_id, *chunk_ids, sep_id]
            input_ids = torch.tensor([framed], device=self._device)
            attention = torch.ones_like(input_ids)
            with torch.no_grad():
                logits = self._model(
                    input_ids=input_ids, attention_mask=attention
                ).logits
            probs = torch.softmax(logits, dim=-1)[0].cpu().numpy()
            agg_probs = probs if agg_probs is None else agg_probs + probs

        assert agg_probs is not None
        agg_probs = agg_probs / len(chunks)
        return self._probs_to_result(agg_probs)

    def _probs_to_result(self, probs: Any) -> SentimentResult:
        """probs: np.ndarray of shape (3,) ordered [neg, neu, pos]."""
        idx = int(probs.argmax())
        label = _ROBERTA_LABEL_MAP[idx]
        # Compound score: positive prob - negative prob, range [-1, 1].
        score = float(probs[2] - probs[0])
        raw = {
            "negative": float(probs[0]),
            "neutral": float(probs[1]),
            "positive": float(probs[2]),
        }
        return SentimentResult(label=label, score=score, raw_scores=raw)

    def _vader_analyze(self, text: str) -> SentimentResult:
        scores = self._vader.polarity_scores(text)
        compound = float(scores["compound"])
        # VADER's compound thresholds (from the paper).
        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"
        return SentimentResult(
            label=label,
            score=compound,
            raw_scores={
                "positive": float(scores["pos"]),
                "negative": float(scores["neg"]),
                "neutral": float(scores["neu"]),
            },
        )


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------


def _select_device(torch: Any) -> str:
    """Pick the fastest available device. CUDA > MPS (Apple Silicon) > CPU."""
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _default_batch_size(torch: Any, device: Any) -> int:
    """Heuristic: 64 on accelerators, 16 on CPU. Override per-call if needed."""
    if device is None:
        return 16
    dtype = getattr(device, "type", str(device))
    return 64 if dtype in {"cuda", "mps"} else 16


_singleton: SentimentAnalyzer | None = None


def get_sentiment_analyzer() -> SentimentAnalyzer:
    """Return the process-wide analyzer, creating it on first use."""
    global _singleton
    if _singleton is None:
        _singleton = SentimentAnalyzer()
    return _singleton
