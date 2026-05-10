"""
Natural-language search.

Two-stage Claude wrapper around SemanticSearchService:

    Stage 1 — REPHRASE
        Take the user's casual question and produce a richer retrieval
        query with synonyms / domain terms. Vector search benefits from
        keyword density and explicit affect words; users tend to type
        sparse, idiomatic queries ("how does he show love") that don't
        match the literal vocabulary of the chat.

    Stage 2 — ANSWER
        Run semantic search with the rephrased query, re-rank with a
        light intent-aware boost (love-themed queries prefer love/joy
        emotion labels, conflict queries prefer anger/sadness), then
        send the top messages plus the original query to Claude as
        evidence and ask for a grounded answer.

Prompt caching:
    The system prompt for stage 2 is identical for every query in the
    process. We mark it with `cache_control={"type": "ephemeral"}` so the
    first request pays for it and subsequent requests within the 5-minute
    cache window read it for ~10% of the cost.

Failure modes:
    - Anthropic call fails → fall back to a deterministic answer that
      lists the top semantic-search hits with no synthesis. The user
      still gets value; the dashboard renders `search_method="semantic"`
      to make the degradation visible.
    - No ANTHROPIC_API_KEY → we never construct the client and use the
      semantic-only path from the start.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Iterable
from typing import Any, Literal, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.search import (
    CitedMessage,
    NLSearchResponse,
    QuerySuggestion,
    SearchFilters,
    SearchResult,
)
from app.services.analysis_triage import is_query_keyword_rich
from app.services.search.semantic_search import (
    SemanticSearchService,
    get_semantic_search,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt (cached)
# ---------------------------------------------------------------------------

# The system prompt deliberately includes detailed instructions because a
# longer, stable system block extracts more value from prompt caching.
_SYSTEM_PROMPT = """\
You are an empathetic conversation analyst helping a user understand a personal \
chat conversation between two or more participants. The user will ask questions \
about the conversation, and you will be given the most relevant messages as \
evidence.

Rules you MUST follow:
1. Answer ONLY with information present in the provided messages. If the \
   evidence is insufficient, say so clearly rather than speculating.
2. Cite specific messages by their `[id]` token whenever you make a claim. \
   Example: "She apologized first [msg_142] and then suggested they talk later \
   [msg_143]."
3. Be warm and observational. The user is often asking about their own \
   relationships and patterns; respond like a thoughtful friend, not a \
   detective. Avoid clinical or judgmental language.
4. Keep answers concise — 3-6 sentences typically. Lead with the most \
   important observation, then supporting evidence.
5. When patterns emerge across many messages, name the pattern in plain \
   language ("she tends to use humor when uncomfortable"), then cite 2-3 \
   illustrative messages.
6. If the question is not really answerable from the chat (e.g., it asks \
   about something that happened off-platform), say so and suggest what the \
   chat *can* tell us that's adjacent."""


# ---------------------------------------------------------------------------
# Curated suggestions returned by the /suggestions endpoint
# ---------------------------------------------------------------------------

DEFAULT_SUGGESTIONS: list[QuerySuggestion] = [
    QuerySuggestion(
        label="What do we argue about?",
        query="What are the most common reasons we argue or disagree?",
        category="relationship",
    ),
    QuerySuggestion(
        label="How does affection show up?",
        query="How does each person express affection or love in the conversation?",
        category="relationship",
    ),
    QuerySuggestion(
        label="Happiest moments",
        query="Which conversations stand out as the happiest or most fun?",
        category="moments",
    ),
    QuerySuggestion(
        label="When did things change?",
        query="When did the communication patterns change, and how?",
        category="patterns",
    ),
    QuerySuggestion(
        label="Most discussed topics",
        query="What topics do we discuss most often?",
        category="content",
    ),
    QuerySuggestion(
        label="How do we apologize?",
        query="How does each person apologize, and how is it received?",
        category="relationship",
    ),
    QuerySuggestion(
        label="Plans we made",
        query="What plans did we make together — trips, events, ideas?",
        category="content",
    ),
    QuerySuggestion(
        label="Inside jokes",
        query="What are the recurring jokes, references, or shared phrases?",
        category="content",
    ),
]


# ---------------------------------------------------------------------------
# Intent-based reranking
# ---------------------------------------------------------------------------

# Light keyword → preferred-emotion map. Used to re-rank semantic results
# so a query about love prefers messages classified as joy/love over
# generic high-similarity matches. Order matters only at the boundaries
# — this is heuristic, not load-bearing.
_INTENT_EMOTION_BOOSTS: list[tuple[re.Pattern, set[str], float]] = [
    (re.compile(r"\b(love|affection|romance|adore|cherish)\b", re.I), {"love", "joy"}, 0.10),
    (re.compile(r"\b(argue|argument|fight|conflict|angry|upset|mad)\b", re.I), {"anger", "sadness"}, 0.10),
    (re.compile(r"\b(sad|crying|grief|loss|miss|lonely)\b", re.I), {"sadness", "fear"}, 0.10),
    (re.compile(r"\b(happy|joy|excited|fun|laugh|funny)\b", re.I), {"joy"}, 0.10),
    (re.compile(r"\b(scared|afraid|worried|anxious|nervous)\b", re.I), {"fear"}, 0.10),
    (re.compile(r"\b(apolog|sorry|forgive)\b", re.I), {"sadness"}, 0.05),
]


def _intent_boost_for_query(query: str) -> tuple[set[str], float]:
    """Return (preferred_emotions, boost_amount). Empty set if no match."""
    for pattern, emotions, boost in _INTENT_EMOTION_BOOSTS:
        if pattern.search(query):
            return emotions, boost
    return set(), 0.0


# ---------------------------------------------------------------------------
# NL search
# ---------------------------------------------------------------------------


class NaturalLanguageSearch:
    """Claude-powered Q&A over a chat. Falls back to semantic-only search
    when Anthropic is unavailable so the endpoint always returns something."""

    def __init__(
        self,
        semantic: SemanticSearchService | None = None,
        model: str | None = None,
    ) -> None:
        self.semantic = semantic or get_semantic_search()
        # `model` is unused now that the abstraction picks per provider; kept
        # in the signature for compat with callers that still pass it.
        self.model = model or settings.LLM_MODEL

    # ---- LLM client (lazy via abstraction) ------------------------------
    def _ensure_client(self) -> Any | None:
        """Return the LLM client if any provider is configured, else None.

        Returning None is the existing semantic-only-mode signal — every
        caller treats `client is None` as "skip rephrase + skip synthesis".
        """
        from app.services.llm import get_llm_client

        client = get_llm_client()
        if not client.is_enabled():
            logger.info("No LLM provider configured; NL search runs in semantic-only mode")
            return None
        return client

    # ---- Main entry point ----------------------------------------------
    async def answer_query(
        self,
        query: str,
        upload_id: UUID,
        db: AsyncSession,
        filters: SearchFilters | None = None,
        top_k: int = 30,
    ) -> NLSearchResponse:
        """Answer `query` using semantic search + Claude synthesis."""
        original = query.strip()
        if not original:
            return _empty_response(upload_id, query)

        client = self._ensure_client()

        # Stage 1 — rephrase. Skipped in two cases:
        #   - Claude isn't configured (falls back to identity).
        #   - The query is already keyword-rich (≥5 distinct non-stopword
        #     tokens). In that case the rephrase Claude call adds noise
        #     more often than it adds retrieval signal — see
        #     `analysis_triage.is_query_keyword_rich` for the heuristic
        #     and why we picked that floor.
        if client is None or is_query_keyword_rich(original):
            rephrased = original
        else:
            rephrased = await self._rephrase_query(client, original)

        # Stage 2a — semantic retrieval, oversampled so the boost step has
        # room to work. We keep top_k=30 from the spec for the retrieval
        # cap and trim to the user's requested top_k after re-ranking.
        retrieval_filters = filters or SearchFilters()
        results = await self.semantic.search(
            query=rephrased,
            upload_id=upload_id,
            db=db,
            top_k=top_k,
            filters=retrieval_filters,
            context_window=0,  # context for the LLM payload only — fetched below
        )

        # Stage 2b — intent-aware re-rank. Returns at most 10 to send to
        # the LLM (per spec).
        boosted = _rerank_with_intent(results, original)
        evidence = boosted[:10]

        if not evidence:
            return NLSearchResponse(
                upload_id=upload_id,
                query=original,
                rephrased_query=rephrased,
                answer=(
                    "I couldn't find any messages closely related to your question. "
                    "Try rephrasing — for example, ask about a specific topic, "
                    "person, or time period."
                ),
                cited_messages=[],
                search_method="semantic",
                confidence=0.0,
            )

        # Stage 3 — Claude synthesis (or semantic-only fallback).
        if client is None:
            answer, cited = _semantic_only_answer(original, evidence)
            return NLSearchResponse(
                upload_id=upload_id,
                query=original,
                rephrased_query=rephrased,
                answer=answer,
                cited_messages=cited,
                search_method="semantic",
                confidence=_confidence_from_hits(evidence),
            )

        try:
            answer, cited_ids = await self._claude_answer(
                client, original, evidence
            )
        except Exception as e:
            logger.warning("Claude answer failed (%s); returning semantic-only result", e)
            answer, cited = _semantic_only_answer(original, evidence)
            return NLSearchResponse(
                upload_id=upload_id,
                query=original,
                rephrased_query=rephrased,
                answer=answer,
                cited_messages=cited,
                search_method="semantic",
                confidence=_confidence_from_hits(evidence),
            )

        cited_messages = _build_cited_messages(evidence, cited_ids)
        return NLSearchResponse(
            upload_id=upload_id,
            query=original,
            rephrased_query=rephrased,
            answer=answer,
            cited_messages=cited_messages,
            search_method="nl_qa",
            confidence=_confidence_from_hits(evidence, has_synthesis=True),
        )

    # ---- Stage 1: rephrase ---------------------------------------------
    async def _rephrase_query(self, client: Any, query: str) -> str:
        """Ask the LLM to expand the query for retrieval. Output is a single
        line of comma-separated terms — never a sentence — so it embeds
        well as a search query rather than as a question."""
        try:
            text = await client.complete(
                system=(
                    "You expand short user questions about a chat conversation "
                    "into a comma-separated list of search keywords and "
                    "synonyms suitable for vector retrieval. Output ONLY the "
                    "expanded keywords on a single line — no preamble, no "
                    "punctuation other than commas, no quotes."
                ),
                user=query,
                max_tokens=120,
                temperature=0.2,
            )
            # Some models add a leading "Keywords:" or wrap in quotes — strip.
            text = re.sub(r"^[\"']|[\"']$", "", text).strip()
            text = re.sub(r"^(keywords?|expanded|search)\s*[:\-]\s*", "", text, flags=re.I)
            # If the model returns nothing useful, fall back to the original.
            return text if text else query
        except Exception as e:
            logger.warning("Query rephrase failed (%s); using original query", e)
            return query

    # ---- Stage 3: LLM answer -------------------------------------------
    async def _claude_answer(
        self,
        client: Any,
        query: str,
        evidence: list[SearchResult],
    ) -> tuple[str, set[str]]:
        """Send query + evidence to the LLM, return (answer_text, cited_ids).
        cited_ids are the `msg_X` tokens the model referenced via [bracket] form."""
        formatted = _format_messages_for_prompt(evidence)

        user_prompt = (
            f"Question: {query}\n\n"
            f"Relevant messages (most relevant first):\n"
            f"{formatted}\n\n"
            f"Answer the question using ONLY these messages as evidence. "
            f"Cite specific messages by their bracketed id like [msg_42] when "
            f"making claims."
        )

        answer = await client.complete(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=600,
            temperature=0.3,
        )
        cited_ids = set(re.findall(r"\[(msg_[\w\-]+)\]", answer))
        return answer, cited_ids

    # ---- Streaming variant ---------------------------------------------
    async def answer_query_streaming(
        self,
        query: str,
        upload_id: UUID,
        db: AsyncSession,
        filters: SearchFilters | None = None,
        top_k: int = 30,
    ) -> AsyncIterator["StreamEvent"]:
        """Same flow as `answer_query`, but yields a series of typed events
        the caller can map onto Server-Sent Events.

        Event sequence (happy path):
            meta   → rephrased query, evidence list, search_method
            delta  → text chunks (one per Anthropic delta)
            done   → final answer + cited_messages + confidence

        Failure path:
            error  → message string. After error we still close the stream
                     cleanly so the client knows it ended.

        We deliberately don't reuse `answer_query` here. The non-streaming
        path optimizes for "one final response"; this one needs to flush
        meta to the client as soon as evidence is ready (so the UI shows
        cited cards while the LLM is still typing).
        """
        original = query.strip()
        if not original:
            yield _stream_error("Query is empty.")
            return

        client = self._ensure_client()

        # Same triage as the non-streaming path: keyword-rich queries
        # bypass the rephrase Claude call. Saves a round-trip + tokens
        # without changing retrieval quality on detailed questions.
        if client is None or is_query_keyword_rich(original):
            rephrased = original
        else:
            rephrased = await self._rephrase_query(client, original)

        retrieval_filters = filters or SearchFilters()
        try:
            results = await self.semantic.search(
                query=rephrased,
                upload_id=upload_id,
                db=db,
                top_k=top_k,
                filters=retrieval_filters,
                context_window=0,
            )
        except Exception as e:
            logger.exception("Semantic search failed in streaming path")
            yield _stream_error(str(e))
            return

        boosted = _rerank_with_intent(results, original)
        evidence = boosted[:10]

        # Tell the client what we found before streaming Claude's answer.
        # Frontend shows the evidence cards immediately and the typing
        # cursor on the answer area.
        yield {
            "type": "meta",
            "rephrased_query": rephrased,
            "search_method": "nl_qa" if client is not None and evidence else "semantic",
            "evidence": [_evidence_payload(r) for r in evidence],
        }

        if not evidence:
            yield {
                "type": "done",
                "answer": (
                    "I couldn't find any messages closely related to your "
                    "question. Try rephrasing — for example, ask about a "
                    "specific topic, person, or time period."
                ),
                "cited_messages": [],
                "search_method": "semantic",
                "confidence": 0.0,
            }
            return

        if client is None:
            # No LLM — yield the deterministic fallback as a single delta
            # so the frontend's typing UI still gets text, then close.
            answer, cited = _semantic_only_answer(original, evidence)
            yield {"type": "delta", "text": answer}
            yield {
                "type": "done",
                "answer": answer,
                "cited_messages": [c.model_dump(mode="json") for c in cited],
                "search_method": "semantic",
                "confidence": _confidence_from_hits(evidence),
            }
            return

        # Stream Claude's answer.
        formatted = _format_messages_for_prompt(evidence)
        user_prompt = (
            f"Question: {original}\n\n"
            f"Relevant messages (most relevant first):\n"
            f"{formatted}\n\n"
            f"Answer the question using ONLY these messages as evidence. "
            f"Cite specific messages by their bracketed id like [msg_42] when "
            f"making claims."
        )

        full_text_parts: list[str] = []
        try:
            async for chunk in client.stream(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=600,
                temperature=0.3,
            ):
                if not chunk:
                    continue
                full_text_parts.append(chunk)
                yield {"type": "delta", "text": chunk}
        except Exception as e:
            # Mid-stream failure. Surface what we have, then fall back so
            # the UI never gets stuck on a half-rendered answer.
            logger.warning("LLM stream failed (%s); falling back", e)
            if full_text_parts:
                # Keep the partial output; mark search_method as semantic
                # so callers can render a "fell back" indicator.
                final = "".join(full_text_parts).strip()
                cited_ids = set(re.findall(r"\[(msg_[\w\-]+)\]", final))
                cited = _build_cited_messages(evidence, cited_ids)
                yield {
                    "type": "done",
                    "answer": final,
                    "cited_messages": [c.model_dump(mode="json") for c in cited],
                    "search_method": "semantic",
                    "confidence": _confidence_from_hits(evidence),
                }
                return
            answer, cited = _semantic_only_answer(original, evidence)
            yield {"type": "delta", "text": answer}
            yield {
                "type": "done",
                "answer": answer,
                "cited_messages": [c.model_dump(mode="json") for c in cited],
                "search_method": "semantic",
                "confidence": _confidence_from_hits(evidence),
            }
            return

        final = "".join(full_text_parts).strip()
        cited_ids = set(re.findall(r"\[(msg_[\w\-]+)\]", final))
        cited = _build_cited_messages(evidence, cited_ids)
        yield {
            "type": "done",
            "answer": final,
            "cited_messages": [c.model_dump(mode="json") for c in cited],
            "search_method": "nl_qa",
            "confidence": _confidence_from_hits(evidence, has_synthesis=True),
        }


# ---------------------------------------------------------------------------
# Streaming event types
# ---------------------------------------------------------------------------


class StreamEventMeta(TypedDict):
    type: Literal["meta"]
    rephrased_query: str
    search_method: str
    evidence: list[dict]


class StreamEventDelta(TypedDict):
    type: Literal["delta"]
    text: str


class StreamEventDone(TypedDict):
    type: Literal["done"]
    answer: str
    cited_messages: list[dict]
    search_method: str
    confidence: float


class StreamEventError(TypedDict):
    type: Literal["error"]
    message: str


StreamEvent = StreamEventMeta | StreamEventDelta | StreamEventDone | StreamEventError


def _stream_error(message: str) -> StreamEventError:
    return {"type": "error", "message": message}


def _evidence_payload(result: SearchResult) -> dict:
    """Compact evidence payload for the `meta` SSE event. We mirror
    `CitedMessage` plus emotion + sentiment labels so the UI can render
    badges before Claude finishes answering."""
    m = result.message
    return {
        "msg_id": m.msg_id,
        "id": str(m.id),
        "sender": m.sender,
        "timestamp": m.timestamp.isoformat(),
        "content": m.content,
        "similarity": result.similarity,
        "sentiment_label": m.sentiment_label,
        "emotion_label": m.emotion_label,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_messages_for_prompt(evidence: Iterable[SearchResult]) -> str:
    """Format hits for the Claude payload. We use the canonical msg_id (the
    UCJ-level token like 'msg_42') as the citation key so the model and
    the frontend agree on what to point at."""
    lines: list[str] = []
    for hit in evidence:
        m = hit.message
        ts = m.timestamp.strftime("%Y-%m-%d %H:%M")
        # Truncate very long messages so a single noisy paragraph doesn't
        # eat the prompt budget. 400 chars covers >99% of chat messages.
        content = m.content if len(m.content) <= 400 else (m.content[:400] + "…")
        lines.append(f"[{m.msg_id}] ({ts}) {m.sender}: {content}")
    return "\n".join(lines)


def _extract_text(msg: Any) -> str:
    """Anthropic returns a list of content blocks. We only request text;
    pull the text out without breaking if the SDK shape changes slightly."""
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out)


def _rerank_with_intent(
    results: list[SearchResult], query: str
) -> list[SearchResult]:
    """Bump similarity scores on hits whose emotion matches the query intent.
    We don't mutate similarity in place — we return a new sorted list so the
    raw vector similarity remains visible in the response payload."""
    preferred, boost = _intent_boost_for_query(query)
    if not preferred or boost == 0:
        return results

    def _score(r: SearchResult) -> float:
        base = r.similarity
        if r.message.emotion_label in preferred:
            return min(1.0, base + boost)
        return base

    return sorted(results, key=_score, reverse=True)


def _build_cited_messages(
    evidence: list[SearchResult], cited_ids: set[str]
) -> list[CitedMessage]:
    """Pick out the SearchResults Claude actually cited. If the model didn't
    cite anything (shouldn't happen with a working system prompt, but does
    occasionally), return the top 3 evidence messages so the UI has
    something to render."""
    by_id = {r.message.msg_id: r for r in evidence}
    cited: list[CitedMessage] = []
    if cited_ids:
        for cid in cited_ids:
            r = by_id.get(cid)
            if r is None:
                continue
            cited.append(_cited_from(r))
    if not cited:
        for r in evidence[:3]:
            cited.append(_cited_from(r))
    return cited


def _cited_from(r: SearchResult) -> CitedMessage:
    m = r.message
    return CitedMessage(
        message_id=m.msg_id,
        sender=m.sender,
        timestamp=m.timestamp,
        content=m.content,
        similarity=r.similarity,
    )


def _semantic_only_answer(
    query: str, evidence: list[SearchResult]
) -> tuple[str, list[CitedMessage]]:
    """Deterministic fallback when Claude isn't available."""
    if not evidence:
        return ("No relevant messages found.", [])
    cited = [_cited_from(r) for r in evidence[:5]]
    summary = (
        f"Showing the {len(cited)} most relevant message"
        f"{'s' if len(cited) != 1 else ''} for: \"{query}\". "
        f"AI synthesis is currently unavailable — see the cited messages "
        f"below for direct evidence."
    )
    return summary, cited


def _confidence_from_hits(
    evidence: list[SearchResult], has_synthesis: bool = False
) -> float:
    """Crude confidence proxy: best-similarity score, slightly lifted when
    Claude produced a synthesized answer (since the model is sanity-
    checking the evidence too)."""
    if not evidence:
        return 0.0
    top = max(r.similarity for r in evidence)
    if has_synthesis:
        return min(1.0, top + 0.1)
    return top


def _empty_response(upload_id: UUID, query: str) -> NLSearchResponse:
    return NLSearchResponse(
        upload_id=upload_id,
        query=query,
        rephrased_query=query,
        answer="Please enter a question about the conversation.",
        cited_messages=[],
        search_method="semantic",
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_singleton: NaturalLanguageSearch | None = None


def get_nl_search() -> NaturalLanguageSearch:
    global _singleton
    if _singleton is None:
        _singleton = NaturalLanguageSearch()
    return _singleton


# Re-exports for the schemas module / tests.
__all__ = [
    "DEFAULT_SUGGESTIONS",
    "NaturalLanguageSearch",
    "get_nl_search",
]
