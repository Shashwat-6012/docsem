import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .base import BaseExtractor

logger = logging.getLogger(__name__)


class ProviderName(StrEnum):
    AZURE = "azure"
    PADDLEOCR = "paddleocr"


@dataclass
class ExtractorConfig:
    provider: ProviderName = ProviderName.PADDLEOCR
    options: dict[str, Any] = field(default_factory=dict)


def build_extractor(config: ExtractorConfig) -> BaseExtractor:
    """Single composition point: knows about every provider so nothing
    else in the codebase has to."""
    logger.debug("building extractor for provider %s", config.provider)
    if config.provider == ProviderName.AZURE:
        from .providers.azure import AzureExtractor

        return AzureExtractor(
            endpoint=config.options["endpoint"],
            api_key=config.options["api_key"],
            model_id=config.options.get("model_id", "prebuilt-document"),
        )
    elif config.provider == ProviderName.PADDLEOCR:
        from .providers.paddle import PaddleOCRExtractor

        return PaddleOCRExtractor(
            lang=config.options.get("lang", "en"),
            use_gpu=config.options.get("use_gpu", False),
        )
    raise ValueError(f"Unknown provider: {config.provider}")
