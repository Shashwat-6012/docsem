import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..exceptions import ConfigurationError
from .base import BaseExtractor

logger = logging.getLogger(__name__)


class ProviderName(StrEnum):
    AZURE = "azure"
    PADDLEOCR = "paddleocr"


@dataclass
class ExtractorConfig:
    provider: ProviderName = ProviderName.AZURE
    options: dict[str, Any] = field(default_factory=dict)


def build_extractor(config: ExtractorConfig) -> BaseExtractor:
    """Single composition point: knows about every provider so nothing
    else in the codebase has to."""
    logger.debug("building extractor for provider %s", config.provider)
    if config.provider == ProviderName.AZURE:
        endpoint = config.options.get("endpoint")
        if endpoint is None:
            endpoint = os.environ.get("AZURE_ENDPOINT")
        api_key = config.options.get("api_key")
        if api_key is None:
            api_key = os.environ.get("AZURE_API_KEY")
        if not endpoint or not api_key:
            raise ConfigurationError(
                "Azure extraction requires endpoint and api_key. Provide them in "
                "ExtractorConfig options or set AZURE_ENDPOINT and AZURE_API_KEY."
            )

        from .providers.azure import AzureExtractor

        return AzureExtractor(
            endpoint=endpoint,
            api_key=api_key,
            model_id=config.options.get("model_id", "prebuilt-document"),
        )
    elif config.provider == ProviderName.PADDLEOCR:
        from .providers.paddle import PaddleOCRExtractor

        return PaddleOCRExtractor(
            lang=config.options.get("lang", "en"),
            use_gpu=config.options.get("use_gpu", False),
        )
    raise ValueError(f"Unknown provider: {config.provider}")
