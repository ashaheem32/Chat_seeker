"""
Platform detector.

Decides which parser to dispatch to based on:
1. Filename hints (e.g. "WhatsApp Chat with X.txt", ".csv" extension).
2. Content signatures (regexes for WhatsApp date formats, JSON keys for
   Telegram/Instagram/Facebook).

Each signature contributes a confidence score; the detector returns the
platform with the highest score and the score itself so callers can decide
how to handle low-confidence guesses.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PlatformType(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    CSV = "csv"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DetectionResult:
    platform: PlatformType
    confidence: float  # 0.0 - 1.0
    reason: str


# ---------------------------------------------------------------------------
# Content signatures
# ---------------------------------------------------------------------------

# WhatsApp signatures: any of these regexes appearing in the first ~4KB of
# the file is strong evidence of a WhatsApp export.
_WHATSAPP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\[\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4},?\s+\d{1,2}:\d{2}", re.MULTILINE),
    re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s*-\s",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"Messages and calls are end-to-end encrypted", re.IGNORECASE),
)

# Telegram JSON has a very characteristic shape - a top-level dict with
# `messages` array and per-message `text_entities` / `from_id` / `date_unixtime`.
_TELEGRAM_KEYS = ("text_entities", "from_id", "date_unixtime")

# Instagram & Facebook share `sender_name` + `timestamp_ms` shape. We
# disambiguate via `thread_path` / `magic_words` (Instagram) and `gifs` /
# `is_unsent` / `joinable_mode` (Facebook).
_INSTAGRAM_HINTS = ("thread_path", "magic_words", "is_still_participant")
_FACEBOOK_HINTS = ("joinable_mode", "thread_type", "image", "messages_received_time")


class PlatformDetector:
    """Stateless detector. Cheap to construct; safe to call from anywhere."""

    @staticmethod
    def detect(content: str, filename: str = "") -> DetectionResult:
        """Pick the most likely platform for a given file.

        Args:
            content: Raw file contents (entire file is fine; we sample).
            filename: Optional original filename for extension/name hints.

        Returns:
            DetectionResult with platform, confidence ∈ [0,1], and a human-
            readable reason. Confidence < 0.4 means the result should be
            treated as a guess - the caller may want to surface a warning.
        """
        # Cap the sample size - we don't need to scan a 50MB file.
        sample = content[:8192] if content else ""
        filename_lower = filename.lower()

        scores: dict[PlatformType, list[tuple[float, str]]] = {
            PlatformType.WHATSAPP: [],
            PlatformType.TELEGRAM: [],
            PlatformType.INSTAGRAM: [],
            PlatformType.FACEBOOK: [],
            PlatformType.CSV: [],
        }

        # ---- Filename hints -------------------------------------------------
        if "whatsapp" in filename_lower:
            scores[PlatformType.WHATSAPP].append((0.4, "filename contains 'whatsapp'"))
        if filename_lower.endswith(".txt"):
            scores[PlatformType.WHATSAPP].append((0.1, ".txt extension"))
        if filename_lower.endswith(".csv"):
            scores[PlatformType.CSV].append((0.5, ".csv extension"))
        if filename_lower.endswith(".tsv"):
            scores[PlatformType.CSV].append((0.5, ".tsv extension"))
        if filename_lower.endswith(".json"):
            # Could be any of telegram/instagram/facebook - small generic hint.
            for p in (PlatformType.TELEGRAM, PlatformType.INSTAGRAM, PlatformType.FACEBOOK):
                scores[p].append((0.05, ".json extension"))

        # ---- Content signatures --------------------------------------------
        # JSON path - try to parse the first chunk to look at keys.
        parsed_json = _try_parse_json_prefix(sample, content)
        if parsed_json is not None:
            for plat, score, reason in _score_json(parsed_json):
                scores[plat].append((score, reason))
        else:
            # Plain text path - check WhatsApp regexes.
            for pat in _WHATSAPP_PATTERNS:
                if pat.search(sample):
                    scores[PlatformType.WHATSAPP].append(
                        (0.5, f"matched WhatsApp signature {pat.pattern[:30]}…")
                    )

            # CSV: look for a header row with comma-separated identifiers.
            # Only meaningful if filename didn't already set CSV high.
            first_line = sample.split("\n", 1)[0]
            if "," in first_line and len(first_line.split(",")) >= 3:
                # Header-y row?
                if any(k in first_line.lower() for k in
                       ("sender", "from", "author", "timestamp", "date", "message", "content")):
                    scores[PlatformType.CSV].append((0.4, "CSV header row detected"))

        # ---- Aggregate ------------------------------------------------------
        totals = {plat: sum(s for s, _ in evidence) for plat, evidence in scores.items()}
        # Cap at 1.0 so confidence stays in a familiar range.
        totals = {plat: min(score, 1.0) for plat, score in totals.items()}

        best = max(totals.items(), key=lambda kv: kv[1])
        platform, confidence = best

        if confidence <= 0.0:
            return DetectionResult(
                platform=PlatformType.UNKNOWN,
                confidence=0.0,
                reason="no signatures matched",
            )

        # Build a reason string from the top-scoring evidence.
        evidence = scores[platform]
        evidence.sort(key=lambda e: e[0], reverse=True)
        reason = "; ".join(r for _, r in evidence[:2])

        logger.debug(
            "Detector: %s (confidence=%.2f, evidence=%s)",
            platform.value, confidence, reason,
        )
        return DetectionResult(platform=platform, confidence=confidence, reason=reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_parse_json_prefix(sample: str, full: str) -> dict[str, Any] | None:
    """Parse the file as JSON if it looks JSON-shaped.

    We try the full content because partial JSON won't parse. For huge
    files this could be expensive, but JSON exports tend to fit in memory.
    """
    stripped = sample.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return None
    try:
        obj = json.loads(full)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _score_json(data: dict[str, Any]) -> list[tuple[PlatformType, float, str]]:
    """Score a parsed JSON object against each platform's signature."""
    out: list[tuple[PlatformType, float, str]] = []

    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return out

    # Sample first message only - more than enough.
    sample_msg = messages[0] if isinstance(messages[0], dict) else {}

    # Telegram signature
    if any(k in sample_msg for k in _TELEGRAM_KEYS):
        out.append((PlatformType.TELEGRAM, 0.85, "telegram-shaped JSON keys"))
    if "type" in data and data.get("type") in {"personal_chat", "private_supergroup", "private_group", "public_supergroup"}:
        out.append((PlatformType.TELEGRAM, 0.4, "telegram chat type"))

    # Facebook + Instagram both use sender_name + timestamp_ms.
    if "sender_name" in sample_msg and "timestamp_ms" in sample_msg:
        # Disambiguate via top-level / per-message fields.
        if any(k in data for k in _INSTAGRAM_HINTS):
            out.append((PlatformType.INSTAGRAM, 0.85, "instagram top-level keys"))
        elif any(k in data for k in _FACEBOOK_HINTS):
            out.append((PlatformType.FACEBOOK, 0.85, "facebook top-level keys"))
        else:
            # No tiebreaker - it's one of them. Default to Facebook because
            # the format originated there and Instagram uses Facebook's
            # downloader.
            out.append((PlatformType.FACEBOOK, 0.6, "fb-shaped JSON, no IG hints"))

    return out
