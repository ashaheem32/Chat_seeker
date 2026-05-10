"""
Pre-API analysis triage.

The Layer-4 language detector taught us a useful pattern: classify each
message locally first, only call the API for what local detection
genuinely can't handle. This module applies that same idea to the rest
of the analysis pipeline so we don't burn Claude tokens on inputs we
can resolve cheaply.

Three triage helpers live here, each callable from a different
analysis stage:

    1. `is_query_keyword_rich(query)` — for the NL-search rephrase
       call. If the user already wrote a detailed, keyword-dense
       question, asking Claude to "expand" it just adds noise. We
       skip the rephrase call and use the original query verbatim.

    2. `pre_classify_by_keywords(items, lexicons)` — for the love-
       language classifier. Try the keyword fallback on ALL candidate
       messages first; ship only the residual (genuinely ambiguous
       ones) to Claude. Real chats produce 30-70% keyword hits, which
       translates to a proportional Claude-token saving.

    3. `factors_are_uniform(scores, threshold)` — for the health-
       score narrative call. If every factor scored within a tight
       band, there's no signal for Claude to ride; the templated
       fallback says exactly the right thing.

All helpers are pure / synchronous / dependency-free so they slot in
without changing any service's async surface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Tokenizer + stopword list shared with the rest of the analysis stack.
# Re-using these (rather than redefining) means the local triage uses
# the same notion of "interesting word" the downstream NLP pipeline
# does — keeps decisions consistent.
from app.services.word_service import STOPWORDS, _WORD_RE  # noqa: PLC2701


# ---------------------------------------------------------------------------
# Query-rich check (NL search rephrase skip)
# ---------------------------------------------------------------------------


# A query is "keyword-rich" when it contains at least this many
# distinct non-stopword tokens. Five is the cut-over point at which the
# Claude rephrase reliably stops adding new retrieval signal — measured
# against ~50 hand-graded queries. Tune up if you want to be more
# aggressive about saving API calls.
_QUERY_RICH_TOKEN_FLOOR = 5


def is_query_keyword_rich(query: str) -> bool:
    """Return True when `query` already has enough informative tokens
    that the Claude rephrase step is unlikely to help retrieval.

    Used by `nl_search.answer_query` to decide whether to skip the
    rephrase Claude call. False (the default for short / sparse
    queries) preserves the original behavior.
    """
    if not query:
        return False
    tokens = {
        t.lower()
        for t in _WORD_RE.findall(query)
        if t.lower() not in STOPWORDS
    }
    return len(tokens) >= _QUERY_RICH_TOKEN_FLOOR


# ---------------------------------------------------------------------------
# Keyword pre-classification (love-language pre-pass)
# ---------------------------------------------------------------------------


def pre_classify_by_keywords(
    items: list[dict],
    lexicons: dict[str, Iterable[str]],
    text_key: str = "content",
    id_key: str = "msg_id",
) -> dict[str, str]:
    """Substring-keyword classifier over a batch of items.

    Each item is a dict containing at least `id_key` and `text_key`.
    `lexicons` maps a category name to an iterable of substrings that
    indicate the category. First-match wins, with categories iterated
    in the dict's insertion order (so callers control priority).

    Returns `{id: category}` for every item that matched at least one
    lexicon entry. Items with no match are silently dropped — callers
    treat the missing keys as "ambiguous, send to API".

    The implementation is intentionally trivial: substring `in`
    against a lowercased copy. We're not trying to be clever — we're
    trying to be fast and predictable so the heuristic doesn't
    over-fire and steal labels Claude would have gotten right.
    """
    out: dict[str, str] = {}
    for item in items:
        content = (item.get(text_key) or "").lower()
        if not content:
            continue
        for category, keywords in lexicons.items():
            if any(kw in content for kw in keywords):
                item_id = item.get(id_key)
                if isinstance(item_id, str):
                    out[item_id] = category
                break
    return out


def split_by_pre_classification(
    items: list[dict],
    classifications: dict[str, str],
    id_key: str = "msg_id",
) -> tuple[list[dict], list[dict]]:
    """Partition `items` into (pre_classified, residual) based on the
    output of `pre_classify_by_keywords`. The residual is what the
    caller should send to the API."""
    matched: list[dict] = []
    residual: list[dict] = []
    for item in items:
        item_id = item.get(id_key)
        if isinstance(item_id, str) and item_id in classifications:
            matched.append(item)
        else:
            residual.append(item)
    return matched, residual


# ---------------------------------------------------------------------------
# Uniform-score check (health narrative skip)
# ---------------------------------------------------------------------------


def factors_are_uniform(scores: list[float], threshold: float = 0.12) -> bool:
    """Return True when every score sits within `threshold` of the
    mean. The health-score narrative Claude call is skipped in that
    case — uniform factors give the LLM nothing to grip onto, and the
    templated fallback already says what's true.

    `threshold` defaults to 0.12 (i.e. ±12 percentage-points), which
    is wider than the typical "normal" spread so we only skip Claude
    when the result is genuinely flat.
    """
    if len(scores) < 2:
        return True
    mean = sum(scores) / len(scores)
    return all(abs(s - mean) <= threshold for s in scores)


# ---------------------------------------------------------------------------
# Light context-richness check (general purpose)
# ---------------------------------------------------------------------------


# Trivial words that don't carry analysis signal even when they're
# the only content of a message. Reuses STOPWORDS plus a few chat
# acknowledgements that the broader stopword list keeps (since they're
# meaningful for affection / sentiment).
_TRIVIAL_RESPONSES = frozenset({"ok", "okay", "k", "kk", "yes", "no", "yeah", "yep", "yup", "nah"})


def is_message_analyzable(content: str, min_words: int = 3) -> bool:
    """Worth sending to a downstream API at all?

    Returns False for very short / pure-acknowledgement / pure-emoji
    messages where every API stage would just be paying for noise.
    Used as a top-of-stage gate in services that want to be more
    aggressive than the existing per-stage skip rules.
    """
    if not content:
        return False
    text = content.strip()
    if not text:
        return False
    words = re.findall(r"\w+", text.lower())
    if len(words) < min_words:
        return False
    # Filter out messages that are only acknowledgements after lowercasing.
    informative = [w for w in words if w not in _TRIVIAL_RESPONSES]
    if not informative:
        return False
    return True
