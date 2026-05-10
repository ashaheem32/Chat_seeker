"""
Language detector.

Pure-Python, deterministic. Zero API calls, zero ML weights — runs in
microseconds per message and is safe to call inline during ingest.

Decision tree (in priority order):

  1. Strip emoji + punctuation. If ≤ 2 word tokens remain → `too_short`.
     Short messages are too sparse to classify; the normalizer skips
     translation for them.

  2. Scan for non-Latin scripts via Unicode codepoint ranges. If a
     non-Latin script accounts for ≥ 30% of the message's letters, the
     message is classified by that script (`devanagari`, `tamil`,
     `arabic_script`, `hangul`, etc.). Below 30% we fall through to the
     romanized + Latin path so a single Hindi word inside an English
     message doesn't flip the whole message to "hindi".

  3. Lexicon-based check on the lowercased Latin tokens. We ship
     curated word lists for the romanized South-Asian languages
     (Manglish, Hinglish, Tanglish, Tenglish, Bengali Roman) and for
     French / Spanish / German. The category whose vocabulary
     dominates (with hits ≥ 2 and ≥ 15% of tokens) wins.

  4. If multiple lexicons score similarly within a small margin →
     `mixed_code_switched`.

  5. Default → `english`.

We deliberately keep the lexicons small. The aim isn't perfect
identification — it's "good enough to route to translation". A handful
of high-signal anchor words per language outperforms a 1000-word
dictionary that introduces false positives on common English overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


# Stable category strings. Must stay in sync with the frontend's expectations
# and the values we write to messages.original_language.
LanguageCategory = Literal[
    # Pure / mixed
    "english",
    "mixed_code_switched",
    "too_short",
    # Romanized Indian languages (Latin script, Indic vocabulary)
    "manglish",
    "hinglish",
    "tanglish",
    "tenglish",
    "bengali_roman",
    # Roman-script foreign languages
    "french",
    "spanish",
    "german",
    # Native scripts
    "devanagari",
    "malayalam",
    "tamil",
    "telugu",
    "kannada",
    "bengali_script",
    "arabic_script",
    "han_chinese",
    "hangul",
    "cyrillic",
    # Catch-all when a non-Latin block dominates but doesn't match any
    # specific block we care about.
    "non_latin_other",
]


@dataclass(slots=True)
class DetectionResult:
    """Per-message classification output."""

    category: LanguageCategory
    detected_language: str
    """Same string as `category` today; kept as a separate field so
    callers can stash extra detail (e.g. 'manglish:malayalam-heavy')
    without breaking the literal."""

    confidence: float
    """[0, 1] — strength of the lexicon / script match. 0.0 when we
    fell back to English by default. Used for routing edge cases."""

    needs_translation: bool
    """True when the normalizer should produce content_english for this
    message. False for `english` and `too_short`."""

    use_claude: bool
    """True when we should send the message to the Claude translator.
    Same value as `needs_translation` today, but kept as a separate
    flag so we can swap in a different translation backend per category
    later (e.g. cached transliteration tables for Devanagari)."""


# ---------------------------------------------------------------------------
# Unicode range scanner
# ---------------------------------------------------------------------------


# Each entry: (category, lo, hi). Order matters only for tie-breaks; we use
# the dominant block by codepoint count.
_SCRIPT_RANGES: tuple[tuple[LanguageCategory, int, int], ...] = (
    ("devanagari",     0x0900, 0x097F),  # Hindi, Marathi, Sanskrit
    ("bengali_script", 0x0980, 0x09FF),
    ("tamil",          0x0B80, 0x0BFF),
    ("telugu",         0x0C00, 0x0C7F),
    ("kannada",        0x0C80, 0x0CFF),
    ("malayalam",      0x0D00, 0x0D7F),
    ("arabic_script",  0x0600, 0x06FF),  # Arabic + Persian + Urdu (incomplete; see also 0x0750)
    ("arabic_script",  0x0750, 0x077F),  # Arabic Supplement
    ("hangul",         0xAC00, 0xD7AF),  # Korean syllables
    ("han_chinese",    0x4E00, 0x9FFF),  # CJK Unified Ideographs
    ("han_chinese",    0x3400, 0x4DBF),  # CJK Extension A
    ("cyrillic",       0x0400, 0x04FF),
)

_LATIN_LO, _LATIN_HI = 0x0041, 0x024F  # Basic Latin + Latin-1 + extended-A


def _classify_codepoint(cp: int) -> str | None:
    """Bucket a codepoint into a script tag. Letters only — punctuation,
    digits, whitespace return None so they don't dilute the dominance
    calculation."""
    if cp < 0x0030:
        return None  # control chars / punctuation up to '/'
    if 0x0030 <= cp <= 0x0039:
        return None  # digits
    if 0x003A <= cp <= 0x0040:
        return None  # punctuation
    if _LATIN_LO <= cp <= _LATIN_HI:
        return "latin"
    for tag, lo, hi in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return tag
    if cp > 0x007F:
        # Some other non-Latin glyph (emoji, supplementary plane, etc).
        # Emoji are the common case; treat as "skip" so they don't shift
        # the script counts — emoji blocks live in the Symbols / Pictographs
        # ranges (U+1F300+).
        return None
    return None


# ---------------------------------------------------------------------------
# Romanized + foreign Roman lexicons
# ---------------------------------------------------------------------------

# Compact "anchor word" sets. Each list is ~25 high-signal tokens that
# rarely appear in English. Adding rare-but-distinctive words is more
# valuable than padding with common ones — a 200-word Hinglish list
# would over-fire on "yaar" and "bhai" appearing in English-with-Hindi-
# loanwords messages, which we want classified as `english`, not
# `hinglish`.

_MANGLISH = frozenset({
    "alle", "aano", "aanu", "aarum", "anu", "athe", "athaano", "athu",
    "chetta", "chechi", "cheriya", "edaa", "eda", "eduthu", "ente", "entha",
    "evide", "ille", "illa", "ingane", "kunje", "machaane", "machaa", "machaane",
    "mone", "ninte", "nee", "nokkam", "okke", "onnum", "polayadi",
    "polum", "raksha", "saare", "thanne", "theerthu", "ungal", "ungalal",
    "vannu", "veendum", "venam", "venda", "verum", "ariyam", "ariyilla",
    "undu", "unde", "undallo", "kandu", "thanikku", "thaane",
})
_HINGLISH = frozenset({
    "kya", "hai", "hain", "nahi", "nahin", "mera", "meri", "mere",
    "tera", "teri", "tere", "tum", "tumhe", "tumhare", "tumko",
    "yaar", "bhai", "bhaiya", "didi", "kar", "karna", "karke", "kiya",
    "raha", "rahi", "rahe", "hoga", "hogi", "hoge", "hota",
    "bahut", "bohot", "achha", "accha", "theek", "thik", "ho",
    "kuch", "kuchh", "kabhi", "abhi", "bhi", "sahi", "matlab",
    "samjha", "samjhi", "samajh", "pata", "pehle", "phir", "fir",
    "haan", "haa", "ji", "kya kar", "kyun", "kyu",
})
_TANGLISH = frozenset({
    "enna", "ennada", "ennama", "macha", "machi", "machaan", "thambi",
    "anna", "akka", "irukku", "iruken", "iruka", "irukan", "vanthuten",
    "varen", "vaa", "vendam", "venum", "puriyala", "puriyum",
    "romba", "konjam", "pannitu", "panren", "pannu", "panniten",
    "kekkanum", "kelu", "sollu", "solli", "yenna", "yethu", "ennachu",
    "amma", "appa", "ille", "illa", "irukka", "vada", "vandiya",
    "saaptiya", "saapdu", "tholaiya", "kavala",
})
_TENGLISH = frozenset({
    "enti", "ela", "evaru", "neevu", "neeku", "nenu", "naaku",
    "raa", "ra", "ree", "kani", "kaani", "ledu", "ledhu", "undi", "unde",
    "akkada", "ikkada", "ekkada", "vacchanu", "vasthunna", "vellanu",
    "chudu", "choodu", "chustha", "chustunna", "chesthe", "chesinav",
    "telusa", "telusu", "ardham", "manchidi", "kanapadu", "thondaraga",
    "annaya", "akkaya", "thammudu", "naanna", "amma", "vadu", "amma",
    "matlade", "matladu", "ela undi", "bagunnav", "bagunnaru",
})
_BENGALI_ROMAN = frozenset({
    "ami", "tumi", "tui", "amar", "tomar", "amake", "tomake",
    "kemon", "achho", "achi", "bhalo", "bhalobasi", "kothay", "kothai",
    "korbo", "korchi", "korechi", "kore", "chilo", "chilam", "hobe",
    "ho-i-c-he", "hoye", "holo", "ki", "na", "kintu", "shob", "shei",
    "dada", "didi", "boudi", "khub", "khoob", "ektu", "ekta",
    "khabo", "khaichi", "berate", "amra", "tora", "ora",
})

_FRENCH = frozenset({
    "bonjour", "bonsoir", "salut", "merci", "oui", "non", "comme", "mais",
    "avec", "pour", "dans", "tout", "rien", "très", "bien", "alors",
    "aujourd'hui", "demain", "hier", "voilà", "toujours", "jamais",
    "beaucoup", "peu", "moins", "encore", "déjà", "où", "qu'est-ce",
    "c'est", "j'ai", "tu es", "nous sommes", "ils sont", "fais",
})
_SPANISH = frozenset({
    "hola", "gracias", "por", "qué", "cómo", "está", "muy", "bien",
    "tiene", "tengo", "tienes", "estoy", "estás", "vamos", "voy",
    "puedo", "puede", "quiero", "quieres", "ahora", "después", "antes",
    "mañana", "hoy", "ayer", "hasta", "desde", "porque", "pero", "aunque",
    "todavía", "siempre", "nunca", "claro",
})
_GERMAN = frozenset({
    "hallo", "danke", "bitte", "ich", "du", "ist", "aber", "mit", "das",
    "der", "die", "den", "eine", "einen", "auch", "nicht", "schon",
    "schön", "gut", "ja", "nein", "wir", "ihr", "sie", "geht", "gehe",
    "haben", "habe", "hatte", "wäre", "würde", "kann", "können", "machen",
    "machst", "machts", "morgen", "heute", "gestern",
})


_LEXICONS: tuple[tuple[LanguageCategory, frozenset[str]], ...] = (
    ("manglish",       _MANGLISH),
    ("hinglish",       _HINGLISH),
    ("tanglish",       _TANGLISH),
    ("tenglish",       _TENGLISH),
    ("bengali_roman",  _BENGALI_ROMAN),
    ("french",         _FRENCH),
    ("spanish",        _SPANISH),
    ("german",         _GERMAN),
)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Minimum hits in a lexicon for it to be considered. 1 hit is too noisy
# (English borrowings like "yaar" / "bhai" / "vada" appear in English chats);
# 2 hits is an acceptable signal/noise tradeoff.
_MIN_LEXICON_HITS = 2

# Minimum share of message tokens a lexicon must own to win outright.
_DOMINANCE_THRESHOLD = 0.15

# When the top lexicon's score is within this margin of the runner-up,
# we treat the message as code-switched rather than picking arbitrarily.
_MIXED_MARGIN = 0.04

# Minimum share of letters that must be in a non-Latin script before that
# script wins outright. Below this we proceed to the lexicon scan.
_SCRIPT_DOMINANCE = 0.30


# Token regex — letters / apostrophes only, ≥1 char. Catches romanized
# words including ones with apostrophes ("c'est", "j'ai"). Lowercased
# downstream.
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ']*")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_language(text: str) -> DetectionResult:
    """Classify `text` into a `LanguageCategory`. See module docstring."""
    if text is None:
        return _english_default(0.0)
    stripped = text.strip()
    if not stripped:
        return _too_short()

    # 1. Word-token count — emoji + punctuation are excluded by _TOKEN_RE.
    tokens = [t.lower() for t in _TOKEN_RE.findall(stripped)]
    if len(tokens) <= 2:
        return _too_short()

    # 2. Script dominance.
    script_counts: dict[str, int] = {}
    total_letters = 0
    for ch in stripped:
        tag = _classify_codepoint(ord(ch))
        if tag is None:
            continue
        total_letters += 1
        script_counts[tag] = script_counts.get(tag, 0) + 1
    if total_letters > 0:
        non_latin = {
            tag: n for tag, n in script_counts.items() if tag != "latin"
        }
        if non_latin:
            top_tag, top_n = max(non_latin.items(), key=lambda kv: kv[1])
            share = top_n / total_letters
            if share >= _SCRIPT_DOMINANCE:
                return DetectionResult(
                    category=top_tag,  # type: ignore[arg-type]
                    detected_language=top_tag,
                    confidence=round(share, 3),
                    needs_translation=True,
                    use_claude=True,
                )
            # If we have a smaller non-Latin block (e.g. occasional Hindi
            # word) — fall through; the message will likely classify as
            # mixed_code_switched or english based on the romanized scan.

    # 3. Lexicon scan — Latin path.
    token_set = set(tokens)
    n_tokens = len(tokens)
    scores: dict[LanguageCategory, tuple[int, float]] = {}
    for category, lex in _LEXICONS:
        # Count distinct tokens in this category's lexicon. Counting
        # distinct (not raw) avoids "yaar yaar yaar" producing 3 hits.
        hits = len(token_set & lex)
        if hits < _MIN_LEXICON_HITS:
            continue
        share = hits / n_tokens
        if share < _DOMINANCE_THRESHOLD:
            continue
        scores[category] = (hits, share)

    if not scores:
        # Latin script + no foreign anchors = English.
        return _english_default(0.6)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1][1])
    top_cat, (top_hits, top_share) = ranked[0]
    if len(ranked) >= 2:
        runner_share = ranked[1][1][1]
        if top_share - runner_share <= _MIXED_MARGIN:
            return DetectionResult(
                category="mixed_code_switched",
                detected_language="mixed_code_switched",
                confidence=round(top_share, 3),
                needs_translation=True,
                use_claude=True,
            )

    return DetectionResult(
        category=top_cat,
        detected_language=top_cat,
        confidence=round(top_share, 3),
        needs_translation=True,
        use_claude=True,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _too_short() -> DetectionResult:
    return DetectionResult(
        category="too_short",
        detected_language="too_short",
        confidence=1.0,
        needs_translation=False,
        use_claude=False,
    )


def _english_default(confidence: float) -> DetectionResult:
    return DetectionResult(
        category="english",
        detected_language="english",
        confidence=confidence,
        needs_translation=False,
        use_claude=False,
    )
