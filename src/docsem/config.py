"""
Configuration for DocSem.

Configuration describes how a document should be processed while
remaining independent of concrete implementation libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .exceptions import ConfigurationError
from .extraction.factory import ExtractorConfig, ProviderName


ExtractionMode = Literal[
    "auto",
    "text",
    "ocr",
    "layout",
]


ArtifactStoreType = Literal["file", "redis"]


@dataclass(frozen=True)
class ArtifactStoreConfig:
    """Configuration for debug artifacts; Redis is reserved for future support."""

    type: ArtifactStoreType
    location: Path | None = None

    def __post_init__(self) -> None:
        if self.type == "file" and self.location is None:
            raise ConfigurationError(
                "A location is required for the file artifact store."
            )


@dataclass(frozen=True)
class DebugConfig:
    """Settings for recording analysis and processing-step artifacts."""

    enabled: bool = False
    artifact_store: ArtifactStoreConfig | None = None

    def __post_init__(self) -> None:
        if self.enabled and self.artifact_store is None:
            raise ConfigurationError(
                "An artifact store must be configured when debug mode is enabled."
            )


@dataclass(frozen=True)
class DocSemConfig:
    """
    Top-level configuration for DocSem.

    Example
    -------
    >>> config = DocSemConfig(
    ...     extraction=ExtractionConfig(mode="layout"),
    ...     structuring=StructuringConfig(mode="hybrid"),
    ... )
    """

    extraction: ExtractorConfig

    debug: DebugConfig = DebugConfig()

    @classmethod
    def default(cls) -> "DocSemConfig":
        """Return the default configuration."""
        return cls(
            extraction=ExtractorConfig(
                provider=ProviderName.PADDLEOCR,
                options={
                    "lang": "en",
                    "use_gpu": False,}
            )
        )
