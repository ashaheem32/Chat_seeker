"""
Parser factory.

The orchestration layer between platform detection, parsing, and UCJ
assembly. Routes/services should call `parse_file()` directly - the
factory handles the rest.

Flow:
    detect platform -> get_parser(platform) -> parser.parse(content)
                    -> UCJBuilder.build(result) -> UCJFile

Errors:
    - Unknown platform                 -> ValueError
    - Empty / unparseable file         -> UCJFile with empty messages
    - Parser internal error            -> bubbles up as RuntimeError
"""

from __future__ import annotations

import logging

from app.schemas.ucj import UCJFile
from app.services.parser.base import ChatParser, UCJResult
from app.services.parser.csv_parser import CSVParser
from app.services.parser.detector import (
    DetectionResult,
    PlatformDetector,
    PlatformType,
)
from app.services.parser.facebook import FacebookParser
from app.services.parser.instagram import InstagramParser
from app.services.parser.telegram import TelegramParser
from app.services.parser.whatsapp import WhatsAppParser

logger = logging.getLogger(__name__)


class ParserFactory:
    """Maps PlatformType enum values to parser instances.

    Parsers are stateless, so we keep a single instance of each. If a future
    parser ever holds state, switch this to per-call instantiation.
    """

    _instances: dict[PlatformType, ChatParser] = {
        PlatformType.WHATSAPP: WhatsAppParser(),
        PlatformType.TELEGRAM: TelegramParser(),
        PlatformType.INSTAGRAM: InstagramParser(),
        PlatformType.FACEBOOK: FacebookParser(),
        PlatformType.CSV: CSVParser(),
    }

    @classmethod
    def get_parser(cls, platform: PlatformType) -> ChatParser:
        try:
            return cls._instances[platform]
        except KeyError as e:
            raise ValueError(f"No parser registered for platform: {platform}") from e

    @classmethod
    def parse_file(
        cls,
        content: str,
        filename: str = "",
        *,
        platform: PlatformType | None = None,
        min_confidence: float = 0.4,
    ) -> tuple[UCJFile, DetectionResult]:
        """
        End-to-end: detect platform, parse, build UCJ.

        Args:
            content: Raw file contents as a string.
            filename: Original filename, used as a detection hint.
            platform: If provided, skip detection and use this parser.
            min_confidence: Minimum detection confidence to accept the
                auto-detected platform. Below this we raise ValueError so
                the caller can prompt the user to select manually.

        Returns:
            (UCJFile, DetectionResult) - the result is also returned so
            callers can surface confidence/warnings to the user.

        Raises:
            ValueError: If the platform can't be confidently detected and
                no `platform` override was provided.
        """
        if platform is None:
            detection = PlatformDetector.detect(content, filename)
            if detection.confidence < min_confidence or detection.platform is PlatformType.UNKNOWN:
                raise ValueError(
                    f"Could not confidently detect chat platform "
                    f"(best guess: {detection.platform.value}, "
                    f"confidence={detection.confidence:.2f}, reason={detection.reason}). "
                    f"Please specify the platform explicitly."
                )
            platform = detection.platform
        else:
            detection = DetectionResult(
                platform=platform, confidence=1.0, reason="explicit override"
            )

        parser = cls.get_parser(platform)
        logger.info("Parsing %s with %s", filename or "<inline>", parser.__class__.__name__)

        result: UCJResult = parser.parse(content)

        # Local import to avoid a circular dependency: ucj_builder needs
        # UCJResult / UCJMessage from this package.
        from app.services.ucj_builder import UCJBuilder

        ucj = UCJBuilder.build(
            result=result,
            source_filename=filename or "<unknown>",
        )
        return ucj, detection


def parse_file(
    content: str,
    filename: str = "",
    *,
    platform: PlatformType | None = None,
    min_confidence: float = 0.4,
) -> tuple[UCJFile, DetectionResult]:
    """Module-level convenience wrapper around `ParserFactory.parse_file`."""
    return ParserFactory.parse_file(
        content, filename, platform=platform, min_confidence=min_confidence
    )
