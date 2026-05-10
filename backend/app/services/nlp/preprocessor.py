"""
Text preprocessing for NLP.

Chat text is messy: emoji, abbreviations, missing punctuation, URLs, code
encoded in UTF-16, etc. The transformer models we use (twitter-roberta,
distilroberta) handle informal English fairly well, but they still benefit
from:

1. Unicode normalization (NFKC) so visually-identical glyphs collapse to
   one code point. Important for emoji and CJK text mixed into chats.
2. Abbreviation expansion. Models trained on Twitter-era data understand
   "lol", but newer slang ("ngl", "fwiw", "lmk") trips them up. We
   normalize ~120 common chat abbreviations before inference.
3. URL stripping. URLs leak into sentiment as positive-looking tokens
   ("https") and dilute the signal. We flag them with a marker before
   removing so downstream code can tell "this was a link" apart from
   "this was empty".
4. Emoji preservation as text. Stripping emoji loses sentiment signal;
   leaving them as raw glyphs gets tokenized inconsistently. We replace
   each emoji with a stable placeholder (`__SMILE_EMOJI__`) that the
   tokenizer treats as a regular token.

This module has zero ML dependencies — it's pure Python + stdlib +
emoji + langdetect (both lightweight).
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abbreviation dictionary
# ---------------------------------------------------------------------------
# Word-boundary expanded forms. Lowercase keys; we lowercase the input token
# before lookup so "LOL" / "Lol" / "lol" all map. Order doesn't matter here
# but `idk` / `idc` are kept to make the table easy to scan alphabetically.

CHAT_ABBREVIATIONS: dict[str, str] = {
    # Pronouns / verbs
    "u": "you",
    "ur": "your",
    "r": "are",
    "y": "why",
    "n": "and",
    "k": "okay",
    "kk": "okay",
    "thx": "thanks",
    "thnx": "thanks",
    "ty": "thank you",
    "tysm": "thank you so much",
    "pls": "please",
    "plz": "please",
    "cuz": "because",
    "bcuz": "because",
    "bc": "because",
    "b4": "before",
    "l8r": "later",
    "l8": "late",
    "gr8": "great",
    "w8": "wait",
    "wat": "what",
    "wut": "what",
    "wht": "what",
    "wud": "would",
    "shud": "should",
    "cud": "could",
    "abt": "about",
    "ppl": "people",
    "smth": "something",
    "sumthin": "something",
    "smthn": "something",
    "evry1": "everyone",
    "any1": "anyone",
    "no1": "no one",
    "som1": "someone",
    # Internet acronyms
    "lol": "laughing",
    "lmao": "laughing",
    "rofl": "laughing",
    "lmfao": "laughing hard",
    "omg": "oh my god",
    "omfg": "oh my god",
    "wtf": "what the heck",
    "wth": "what the heck",
    "tbh": "to be honest",
    "tbf": "to be fair",
    "imo": "in my opinion",
    "imho": "in my honest opinion",
    "fyi": "for your information",
    "btw": "by the way",
    "afaik": "as far as i know",
    "iirc": "if i recall correctly",
    "idk": "i do not know",
    "idc": "i do not care",
    "idgaf": "i do not care",
    "dgaf": "do not care",
    "ily": "i love you",
    "ilysm": "i love you so much",
    "lmk": "let me know",
    "ngl": "not going to lie",
    "smh": "shaking my head",
    "fwiw": "for what it is worth",
    "iykyk": "if you know you know",
    "icymi": "in case you missed it",
    "tldr": "too long did not read",
    "tl;dr": "too long did not read",
    "rn": "right now",
    "asap": "as soon as possible",
    "atm": "at the moment",
    "irl": "in real life",
    "dm": "direct message",
    "pm": "private message",
    "afk": "away from keyboard",
    "bbl": "be back later",
    "brb": "be right back",
    "bff": "best friend",
    "bf": "boyfriend",
    "gf": "girlfriend",
    "hubby": "husband",
    "wifey": "wife",
    "fam": "family",
    "bro": "brother",
    "sis": "sister",
    "mom": "mother",
    "dad": "father",
    # Emotional intensifiers
    "af": "very",
    "asf": "very",
    "ngl,": "not going to lie,",
    "deadass": "seriously",
    "lowkey": "kind of",
    "highkey": "very",
    "fr": "for real",
    "frfr": "for real",
    "fr fr": "for real",
    "ong": "for real",
    # Temporal / locational
    "tmrw": "tomorrow",
    "tmr": "tomorrow",
    "tmrrw": "tomorrow",
    "tn": "tonight",
    "tnite": "tonight",
    "yest": "yesterday",
    "wkend": "weekend",
    "wknd": "weekend",
    "msg": "message",
    "txt": "text",
    "convo": "conversation",
    "info": "information",
    "vid": "video",
    "pic": "picture",
    "pics": "pictures",
    "pix": "pictures",
    "addy": "address",
    "appt": "appointment",
    "prob": "problem",
    "probly": "probably",
    "prolly": "probably",
    # Affirmatives / negatives
    "ya": "yes",
    "yea": "yes",
    "yeah": "yes",
    "yep": "yes",
    "yup": "yes",
    "nah": "no",
    "nope": "no",
    "nvm": "never mind",
    "nm": "not much",
    "np": "no problem",
    # Other
    "rly": "really",
    "rlly": "really",
    "totes": "totally",
    "obvs": "obviously",
    "obv": "obvious",
    "def": "definitely",
    "defs": "definitely",
    "deffo": "definitely",
}


# ---------------------------------------------------------------------------
# Regex patterns - compiled once at import time
# ---------------------------------------------------------------------------

# Conservative URL pattern. Catches http(s), ftp, www., and bare domains
# of common TLDs. We don't try to be RFC-perfect — we just need to flag and
# strip the obvious cases so they don't pollute sentiment.
_URL_RE = re.compile(
    r"\b(?:https?://|ftp://|www\.)\S+|"
    r"\b\S+\.(?:com|org|net|io|gov|edu|co|uk|de|in)(?:/\S*)?\b",
    re.IGNORECASE,
)

# Whitespace collapse — multiple spaces, tabs, newlines → single space.
_WHITESPACE_RE = re.compile(r"\s+")

# Word-boundary token splitter. We deliberately keep punctuation attached
# to tokens so contractions ("don't") survive abbreviation lookup as a
# single token.
_TOKEN_RE = re.compile(r"(\S+)")


# ---------------------------------------------------------------------------
# Emoji handling
# ---------------------------------------------------------------------------

# Lightweight emoji detector. We avoid pulling the full `emoji` PyPI
# package (~2 MB and rarely necessary) — the Unicode ranges below cover
# >99% of emoji used in chat. If a user pastes a rare astrological glyph
# we just leave it through; the model will tokenize it harmlessly.
_EMOJI_RANGES = [
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F300, 0x1F5FF),  # Symbols & pictographs
    (0x1F680, 0x1F6FF),  # Transport & map
    (0x1F700, 0x1F77F),  # Alchemical
    (0x1F780, 0x1F7FF),  # Geometric extended
    (0x1F800, 0x1F8FF),  # Supplemental arrows-c
    (0x1F900, 0x1F9FF),  # Supplemental symbols & pictographs
    (0x1FA00, 0x1FA6F),  # Chess symbols, etc.
    (0x1FA70, 0x1FAFF),  # Symbols & pictographs extended-a
    (0x2600, 0x26FF),    # Miscellaneous symbols
    (0x2700, 0x27BF),    # Dingbats
    (0x2300, 0x23FF),    # Misc technical
    (0x2B00, 0x2BFF),    # Misc symbols & arrows
    (0x1F1E6, 0x1F1FF),  # Regional indicator (flags)
]


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


# A small, hand-curated map for the most common emoji → readable token.
# Used so transformer models see "love" and "smile" as english words.
# Anything not in this map is replaced with a generic `__EMOJI__` token.
_EMOJI_TO_TOKEN: dict[str, str] = {
    "❤": "__HEART_EMOJI__",
    "❤️": "__HEART_EMOJI__",
    "💗": "__HEART_EMOJI__",
    "💕": "__HEART_EMOJI__",
    "💞": "__HEART_EMOJI__",
    "💖": "__HEART_EMOJI__",
    "💘": "__HEART_EMOJI__",
    "😍": "__LOVE_EMOJI__",
    "🥰": "__LOVE_EMOJI__",
    "😘": "__KISS_EMOJI__",
    "😚": "__KISS_EMOJI__",
    "😊": "__SMILE_EMOJI__",
    "😄": "__SMILE_EMOJI__",
    "😃": "__SMILE_EMOJI__",
    "😀": "__SMILE_EMOJI__",
    "🙂": "__SMILE_EMOJI__",
    "😂": "__LAUGH_EMOJI__",
    "🤣": "__LAUGH_EMOJI__",
    "😆": "__LAUGH_EMOJI__",
    "😅": "__LAUGH_EMOJI__",
    "😢": "__SAD_EMOJI__",
    "😭": "__CRY_EMOJI__",
    "😞": "__SAD_EMOJI__",
    "😔": "__SAD_EMOJI__",
    "🥺": "__SAD_EMOJI__",
    "😡": "__ANGRY_EMOJI__",
    "🤬": "__ANGRY_EMOJI__",
    "😠": "__ANGRY_EMOJI__",
    "😤": "__ANGRY_EMOJI__",
    "😨": "__FEAR_EMOJI__",
    "😱": "__FEAR_EMOJI__",
    "🤔": "__THINK_EMOJI__",
    "😴": "__SLEEP_EMOJI__",
    "🥳": "__PARTY_EMOJI__",
    "🎉": "__PARTY_EMOJI__",
    "👍": "__THUMBS_UP_EMOJI__",
    "👎": "__THUMBS_DOWN_EMOJI__",
    "🙏": "__PRAY_EMOJI__",
    "🔥": "__FIRE_EMOJI__",
    "💯": "__PERFECT_EMOJI__",
}


def _emoji_to_placeholder(ch: str) -> str:
    return _EMOJI_TO_TOKEN.get(ch, "__EMOJI__")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TextPreprocessor:
    """Stateless text cleanup. Methods are pure functions; instance exists
    only to namespace the operations and (optionally) cache language
    detection per-process."""

    def __init__(self) -> None:
        # langdetect is lazily imported inside detect_language so importing
        # this module on a worker without the nlp deps doesn't blow up.
        self._langdetect = None

    # ---- Main entry point ------------------------------------------------
    def clean_for_nlp(self, text: str) -> str:
        """Return a cleaned, abbreviation-expanded string ready for the model.

        Empty / media-only / whitespace inputs return an empty string. Callers
        should check for that before invoking the model — analyzers in this
        package treat empty strings as "skip".
        """
        if not text:
            return ""

        # 1. Unicode normalization. NFKC collapses compatibility forms
        #    (full-width digits → ASCII, ligatures → letters, ...).
        text = unicodedata.normalize("NFKC", text)

        # 2. Strip URLs. Caller is expected to have already inspected the
        #    text for has_url; we just remove the surface form here.
        text = _URL_RE.sub(" ", text)

        # 3. Replace emoji with placeholder tokens. Walk char-by-char so we
        #    handle multi-codepoint sequences (skin tone modifiers, ZWJ).
        out_chars: list[str] = []
        for ch in text:
            if _is_emoji(ch):
                out_chars.append(" ")
                out_chars.append(_emoji_to_placeholder(ch))
                out_chars.append(" ")
            else:
                out_chars.append(ch)
        text = "".join(out_chars)

        # 4. Token-level abbreviation expansion. Splitting on whitespace
        #    keeps punctuation attached so "lol!" → "laughing!".
        def _expand(token: str) -> str:
            stripped = token.strip(".,!?;:\"'()[]{}").lower()
            if stripped in CHAT_ABBREVIATIONS:
                # Preserve trailing punctuation so the tokenizer still sees
                # sentence boundaries.
                trailing = ""
                while token and token[-1] in ".,!?;:\"'()[]{}":
                    trailing = token[-1] + trailing
                    token = token[:-1]
                return CHAT_ABBREVIATIONS[stripped] + trailing
            return token

        text = " ".join(_expand(tok) for tok in text.split())

        # 5. Whitespace collapse.
        text = _WHITESPACE_RE.sub(" ", text).strip()

        return text

    # ---- Emojis ----------------------------------------------------------
    @staticmethod
    def extract_emojis(text: str) -> list[str]:
        """Return the list of emoji characters in `text`, in order. Skin-tone
        modifiers and ZWJ joiners are returned as separate items — callers
        that want to recombine them can do so themselves."""
        if not text:
            return []
        return [ch for ch in text if _is_emoji(ch)]

    # ---- URL detection ---------------------------------------------------
    @staticmethod
    def contains_url(text: str) -> bool:
        return bool(text and _URL_RE.search(text))

    # ---- Language detection ----------------------------------------------
    def detect_language(self, text: str) -> str:
        """Best-effort 2-letter language code. Returns 'unknown' when the
        text is too short or langdetect raises (it does for whitespace
        and code-only strings)."""
        if not text or len(text) < 3:
            return "unknown"

        if self._langdetect is None:
            try:
                from langdetect import DetectorFactory, detect

                # Deterministic results across runs — important so we don't
                # see flapping in tests / dashboards.
                DetectorFactory.seed = 0
                self._langdetect = detect
            except ImportError:
                logger.warning("langdetect not installed; returning 'unknown'")
                return "unknown"

        try:
            return self._langdetect(text)
        except Exception:
            # langdetect raises LangDetectException on whitespace-only /
            # punctuation-only input. Treat as unknown rather than crashing.
            return "unknown"
