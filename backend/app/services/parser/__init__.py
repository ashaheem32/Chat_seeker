"""
Chat-file parsers.

Each platform-specific parser turns raw export bytes into a list of
`UCJMessage` records. The `ParserFactory` orchestrates platform detection +
parsing + UCJ assembly via the `UCJBuilder`.

Public API:
    parse_file(content, filename) -> UCJFile      (factory.parse_file)
    PlatformDetector.detect(content, filename)    (detector)
    ChatParser                                    (base.py - ABC)
    UCJMessage / UCJMetadata / UCJResult          (base.py - dataclasses)
"""

from app.services.parser.base import (
    ChatParser,
    UCJMessage,
    UCJMetadata,
    UCJResult,
)
from app.services.parser.detector import PlatformDetector, PlatformType
from app.services.parser.factory import ParserFactory, parse_file

__all__ = [
    "ChatParser",
    "UCJMessage",
    "UCJMetadata",
    "UCJResult",
    "PlatformDetector",
    "PlatformType",
    "ParserFactory",
    "parse_file",
]
