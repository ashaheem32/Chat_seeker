"""
NLP processing pipeline.

Public surface (imported by workers and tests):
    NLPPipeline                  — orchestrator
    ProgressCallback             — `(stage, fraction, detail) -> Awaitable[None]`
    TextPreprocessor             — text cleanup + emoji/url handling
    SentimentAnalyzer            — twitter-roberta + VADER fallback
    EmotionAnalyzer              — j-hartmann emotion classifier
    TopicExtractor               — KeyBERT (per-msg) + BERTopic (conversation)
    EntityExtractor              — spaCy NER
    SentimentResult / EmotionResult / EntityResult — typed outputs

The heavy ML libraries (torch, transformers, ...) live in the optional
`nlp` Poetry group. The submodules import them lazily inside
class methods so importing `app.services.nlp` from the API container
(which doesn't ship torch) does not crash.
"""

from app.services.nlp.entities import (
    ConversationEntities,
    EntityExtractor,
    EntityResult,
)
from app.services.nlp.emotion import EmotionAnalyzer, EmotionResult
from app.services.nlp.pipeline import NLPPipeline, ProgressCallback
from app.services.nlp.preprocessor import TextPreprocessor
from app.services.nlp.sentiment import SentimentAnalyzer, SentimentResult
from app.services.nlp.topics import TopicExtractor

__all__ = [
    "ConversationEntities",
    "EmotionAnalyzer",
    "EmotionResult",
    "EntityExtractor",
    "EntityResult",
    "NLPPipeline",
    "ProgressCallback",
    "SentimentAnalyzer",
    "SentimentResult",
    "TextPreprocessor",
    "TopicExtractor",
]
