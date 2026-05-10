"""
Named entity recognition.

Backend: spaCy `en_core_web_sm`. We deliberately use the small model rather
than transformer-based NER:
    - 12 MB vs 500 MB on disk
    - ~10× faster on CPU
    - accuracy is "good enough" for the dashboard's use case (mention
      extraction, not legal-grade entity linking)

`en_core_web_sm` ships labels: PERSON, GPE, LOC, DATE, EVENT, ORG, ...
We bucket them into:
    persons : PERSON
    places  : GPE + LOC + FAC
    dates   : DATE + TIME
    events  : EVENT + heuristic keyword pickup ("birthday", "anniversary",
              "wedding", ...) since spaCy under-labels these in casual chat

The pipeline calls extract() per message and aggregate_for_conversation()
once at the end. Aggregation deduplicates case-insensitively and counts
mentions, which the dashboard renders as "top people / places / events
mentioned in this chat".
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntityResult:
    """Per-message NER output."""

    persons: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationEntities:
    """Aggregated NER output for a whole conversation. Counts let the
    dashboard render "top mentioned" lists without re-counting."""

    persons: dict[str, int] = field(default_factory=dict)
    places: dict[str, int] = field(default_factory=dict)
    dates: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Heuristic event detection
# ---------------------------------------------------------------------------

# spaCy's small model rarely tags things like "birthday" or "anniversary" as
# EVENT in informal chat. We supplement with a regex pass over a curated
# keyword list. The phrase is captured along with up to 3 surrounding words
# so dashboard entries are useful ("Sarah's birthday party") rather than
# bare nouns ("birthday").
_EVENT_KEYWORDS = (
    "birthday",
    "anniversary",
    "wedding",
    "engagement",
    "graduation",
    "funeral",
    "vacation",
    "trip",
    "holiday",
    "christmas",
    "thanksgiving",
    "diwali",
    "hanukkah",
    "ramadan",
    "eid",
    "new year",
    "party",
    "concert",
    "festival",
    "reunion",
    "interview",
    "meeting",
    "appointment",
    "dinner",
    "lunch",
    "brunch",
    "date night",
)

_EVENT_RE = re.compile(
    r"\b(?:[\w']+\s+){0,2}(?:" + "|".join(re.escape(w) for w in _EVENT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


_SPACY_MODEL = "en_core_web_sm"


class EntityExtractor:
    """spaCy-backed NER with heuristic event augmentation."""

    def __init__(self, model_name: str = _SPACY_MODEL) -> None:
        self.model_name = model_name
        self._nlp: Any = None
        self._unavailable: bool = False

    def _ensure_loaded(self) -> None:
        if self._nlp is not None or self._unavailable:
            return
        try:
            import spacy

            try:
                self._nlp = spacy.load(self.model_name, disable=["parser", "lemmatizer"])
            except OSError:
                # Model not downloaded — give a clear instruction in logs
                # rather than a generic 'cannot find model' error.
                logger.warning(
                    "spaCy model %s not installed. Run: "
                    "python -m spacy download %s",
                    self.model_name,
                    self.model_name,
                )
                self._unavailable = True
        except ImportError:
            logger.warning("spaCy not installed; entity extraction disabled")
            self._unavailable = True

    # ---- Per-message ----------------------------------------------------
    def extract(self, text: str) -> EntityResult:
        if not text or not text.strip():
            return EntityResult()

        self._ensure_loaded()
        if self._unavailable:
            return EntityResult()

        doc = self._nlp(text)
        result = EntityResult()
        for ent in doc.ents:
            label = ent.label_
            value = ent.text.strip()
            if not value:
                continue
            if label == "PERSON":
                result.persons.append(value)
            elif label in {"GPE", "LOC", "FAC"}:
                result.places.append(value)
            elif label in {"DATE", "TIME"}:
                result.dates.append(value)
            elif label == "EVENT":
                result.events.append(value)

        # Heuristic event augmentation from the curated keyword list.
        for match in _EVENT_RE.finditer(text):
            phrase = match.group(0).strip()
            # Avoid duplicating something spaCy already caught.
            if phrase and phrase not in result.events:
                result.events.append(phrase)

        return result

    def extract_batch(self, texts: list[str]) -> list[EntityResult]:
        """Use spaCy's nlp.pipe for a real CPU speedup on batches.
        ~3-5× faster than sequential .extract() calls on long chats."""
        if not texts:
            return []

        self._ensure_loaded()
        if self._unavailable:
            return [EntityResult() for _ in texts]

        # nlp.pipe gives us streaming docs; we still need to enrich with
        # heuristic events per text, so we walk in lockstep.
        out: list[EntityResult] = []
        for text, doc in zip(
            texts,
            self._nlp.pipe(
                (t or "" for t in texts),
                batch_size=64,
                disable=["parser", "lemmatizer"],
            ),
        ):
            result = EntityResult()
            for ent in doc.ents:
                label = ent.label_
                value = ent.text.strip()
                if not value:
                    continue
                if label == "PERSON":
                    result.persons.append(value)
                elif label in {"GPE", "LOC", "FAC"}:
                    result.places.append(value)
                elif label in {"DATE", "TIME"}:
                    result.dates.append(value)
                elif label == "EVENT":
                    result.events.append(value)
            for match in _EVENT_RE.finditer(text or ""):
                phrase = match.group(0).strip()
                if phrase and phrase not in result.events:
                    result.events.append(phrase)
            out.append(result)
        return out

    # ---- Conversation aggregation ---------------------------------------
    @staticmethod
    def aggregate_for_conversation(
        per_message: list[EntityResult],
    ) -> ConversationEntities:
        """Collapse per-message results into deduplicated (case-insensitive)
        mention counts."""

        def _count(values: list[str]) -> dict[str, int]:
            normalized: list[str] = []
            # We deduplicate by lowercase form but keep the most-common
            # cased version as the display key. This keeps "Mom" / "MOM" / "mom"
            # collapsed while showing the form people actually used most.
            casings: dict[str, Counter] = {}
            for v in values:
                v = v.strip()
                if not v:
                    continue
                key = v.lower()
                casings.setdefault(key, Counter()).update([v])
                normalized.append(key)
            counts = Counter(normalized)
            display_counts: dict[str, int] = {}
            for key, n in counts.most_common():
                display = casings[key].most_common(1)[0][0]
                display_counts[display] = n
            return display_counts

        all_persons: list[str] = []
        all_places: list[str] = []
        all_dates: list[str] = []
        all_events: list[str] = []
        for r in per_message:
            all_persons.extend(r.persons)
            all_places.extend(r.places)
            all_dates.extend(r.dates)
            all_events.extend(r.events)

        return ConversationEntities(
            persons=_count(all_persons),
            places=_count(all_places),
            dates=_count(all_dates),
            events=_count(all_events),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: EntityExtractor | None = None


def get_entity_extractor() -> EntityExtractor:
    global _singleton
    if _singleton is None:
        _singleton = EntityExtractor()
    return _singleton
