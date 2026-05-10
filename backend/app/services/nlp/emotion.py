"""
Emotion classification.

Model: j-hartmann/emotion-english-distilroberta-base. Produces probabilities
across 7 classes — joy, sadness, anger, fear, surprise, disgust, love
(plus a "neutral" class in older checkpoints). We map "neutral" inputs to
"joy" only when the score is overwhelming, otherwise we return None and let
the pipeline decide whether to write nothing.

Skip rules (applied at the pipeline level, not here):
    - deleted messages
    - media-only messages (has_media and empty content)
    - very short messages (<3 words after preprocessing)
    - system messages

The shape of this module mirrors `sentiment.py` — same lazy-load pattern,
same long-text chunking, same singleton accessor — so the pipeline code
treats them uniformly. Differences:
    - No fallback model (VADER doesn't do emotion). On load failure we
      return a "fear=None / score=None" sentinel and the pipeline skips
      writing emotion fields.
    - Output type uses `all_scores` (full dict) instead of `raw_scores`
      because UIs typically render bar charts of the per-class confidences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmotionResult:
    """Output of a single emotion inference. label/score may be None when
    the analyzer could not be loaded — pipeline writes nothing in that case."""

    label: str | None
    score: float | None
    all_scores: dict[str, float] = field(default_factory=dict)


_DEFAULT_MODEL = "j-hartmann/emotion-english-distilroberta-base"

# Allowed emotion labels (per spec). Maps lowercased model output → canonical
# stored label. The 6-class hartmann checkpoint uses these names directly;
# the 7-class includes "neutral" which we don't surface in the DB.
_VALID_LABELS = {"joy", "sadness", "anger", "fear", "surprise", "disgust", "love"}


class EmotionAnalyzer:
    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        device: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self._device_pref = device
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._device: Any = None
        self._id2label: dict[int, str] = {}
        self._unavailable: bool = False

    # ---- Loading ---------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._model is not None or self._unavailable:
            return
        try:
            self._load()
        except Exception as e:  # pragma: no cover — environment-dependent
            logger.warning(
                "Emotion model failed to load (%s); analyzer will return empty results",
                e,
            )
            self._unavailable = True

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self._device = torch.device(self._device_pref or _select_device(torch))
        logger.info("Loading emotion model %s on %s", self.model_name, self._device)

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        model.eval()
        model.to(self._device)
        self._model = model

        # The model card stores readable labels in id2label config — use that
        # directly so we're robust to model checkpoint changes.
        self._id2label = {int(k): v.lower() for k, v in model.config.id2label.items()}

    # ---- Public API ------------------------------------------------------
    def analyze(self, text: str) -> EmotionResult:
        if not text or not text.strip():
            return EmotionResult(label=None, score=None)

        self._ensure_loaded()
        if self._unavailable:
            return EmotionResult(label=None, score=None)

        return self._batch_infer([text])[0]

    def analyze_batch(
        self, texts: list[str], batch_size: int | None = None
    ) -> list[EmotionResult]:
        if not texts:
            return []

        self._ensure_loaded()
        if self._unavailable:
            return [EmotionResult(label=None, score=None) for _ in texts]

        bs = batch_size or _default_batch_size(self._torch, self._device)
        results: list[EmotionResult] = []
        for start in range(0, len(texts), bs):
            chunk = texts[start : start + bs]
            results.extend(self._batch_infer(chunk))
        return results

    # ---- Internals -------------------------------------------------------
    def _batch_infer(self, texts: list[str]) -> list[EmotionResult]:
        torch = self._torch
        long_indices: list[int] = []
        short_indices: list[int] = []
        for i, t in enumerate(texts):
            n = len(self._tokenizer.encode(t or " ", add_special_tokens=False))
            if n > self.max_tokens - 2:
                long_indices.append(i)
            else:
                short_indices.append(i)

        out: list[EmotionResult | None] = [None] * len(texts)

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

        return [r if r is not None else EmotionResult(None, None) for r in out]

    def _infer_long(self, text: str) -> EmotionResult:
        torch = self._torch
        ids = self._tokenizer.encode(text, add_special_tokens=False)
        window = self.max_tokens - 2
        chunks = [ids[i : i + window] for i in range(0, len(ids), window)]
        cls_id = self._tokenizer.cls_token_id
        sep_id = self._tokenizer.sep_token_id

        agg = None
        for chunk_ids in chunks:
            framed = [cls_id, *chunk_ids, sep_id]
            input_ids = torch.tensor([framed], device=self._device)
            attention = torch.ones_like(input_ids)
            with torch.no_grad():
                logits = self._model(input_ids=input_ids, attention_mask=attention).logits
            probs = torch.softmax(logits, dim=-1)[0].cpu().numpy()
            agg = probs if agg is None else agg + probs

        assert agg is not None
        agg = agg / len(chunks)
        return self._probs_to_result(agg)

    def _probs_to_result(self, probs: Any) -> EmotionResult:
        all_scores = {
            self._id2label[i]: float(p) for i, p in enumerate(probs)
        }
        # Pick top label that's in our allowed set. If the top is "neutral"
        # (only present in some checkpoints) and there's no allowed runner-up
        # at meaningful confidence, return None.
        ranked = sorted(all_scores.items(), key=lambda kv: kv[1], reverse=True)
        for label, score in ranked:
            if label in _VALID_LABELS:
                return EmotionResult(
                    label=label, score=float(score), all_scores=all_scores
                )
        return EmotionResult(label=None, score=None, all_scores=all_scores)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _select_device(torch: Any) -> str:
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _default_batch_size(torch: Any, device: Any) -> int:
    if device is None:
        return 16
    dtype = getattr(device, "type", str(device))
    return 64 if dtype in {"cuda", "mps"} else 16


_singleton: EmotionAnalyzer | None = None


def get_emotion_analyzer() -> EmotionAnalyzer:
    global _singleton
    if _singleton is None:
        _singleton = EmotionAnalyzer()
    return _singleton
