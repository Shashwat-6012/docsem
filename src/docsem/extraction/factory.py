from enum import Enum
from dataclasses import dataclass, field
from typing import Any

from .base import BaseExtractor
from .providers.azure import AzureExtractor
from .providers.paddle import PaddleOCRExtractor


class ProviderName(str, Enum):
    AZURE = "azure"
    PADDLEOCR = "paddleocr"


@dataclass
class ExtractorConfig:
    provider: ProviderName = ProviderName.PADDLEOCR
    options: dict[str, Any] = field(default_factory=dict)


def build_extractor(config: ExtractorConfig) -> BaseExtractor:
    """Single composition point: knows about every provider so nothing
    else in the codebase has to."""
    if config.provider == ProviderName.AZURE:
        return AzureExtractor(
            endpoint=config.options["endpoint"],
            api_key=config.options["api_key"],
            model_id=config.options.get("model_id", "prebuilt-document"),
        )
    elif config.provider == ProviderName.PADDLEOCR:
        return PaddleOCRExtractor(
            lang=config.options.get("lang", "en"),
            use_gpu=config.options.get("use_gpu", False),
        )
    raise ValueError(f"Unknown provider: {config.provider}")
