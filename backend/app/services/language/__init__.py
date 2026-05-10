"""
Language normalization layer.

Three-stage pipeline:
    detector   — classify each message into a language category, no API
    translator — Claude-backed translator for non-English text, batched + cached
    normalizer — orchestrator: detect → batch → translate → write back

Public surface (imported by Celery + the upload pipeline):
    detect_language               — single-message classifier
    DetectionResult               — typed detector output
    LanguageCategory              — Literal of the categories the detector emits
    TranslationItem               — one row in / out of the translator
    Translator                    — class wrapping the Claude client + Redis cache
    LanguageNormalizer            — top-level orchestrator
"""

from app.services.language.detector import (
    DetectionResult,
    LanguageCategory,
    detect_language,
)
from app.services.language.normalizer import LanguageNormalizer, language_normalizer
from app.services.language.translator import (
    TranslationItem,
    Translator,
    translator as default_translator,
)

__all__ = [
    "DetectionResult",
    "LanguageCategory",
    "LanguageNormalizer",
    "TranslationItem",
    "Translator",
    "default_translator",
    "detect_language",
    "language_normalizer",
]
