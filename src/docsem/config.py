"""
Configuration for DocSem.

Configuration describes how a document should be processed while
remaining independent of concrete implementation libraries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .exceptions import ConfigurationError
from .extraction.factory import ExtractorConfig, ProviderName
from .llm.factory import DEFAULT_LLM, LLMConfig

ArtifactStoreType = Literal["file", "redis"]


@dataclass(frozen=True)
class ArtifactStoreConfig:
    """Configuration for debug artifacts; Redis is reserved for future support."""

    type: ArtifactStoreType
    location: Path | None = None

    def __post_init__(self) -> None:
        if self.type == "file" and self.location is None:
            raise ConfigurationError("A location is required for the file artifact store.")


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


# config.py
@dataclass(frozen=True)
class AnalysisConfig:
    llm: LLMConfig | None = field(default_factory=lambda: DEFAULT_LLM)
    overrides: dict[str, LLMConfig | None] = field(default_factory=dict)  # keyed by Analyzer.name
    enabled: tuple[str, ...] = ("table_structure", "table_continuation")

    def llm_for(self, analyzer_name: str) -> LLMConfig | None:
        return self.overrides.get(analyzer_name, self.llm)


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
    analysis: AnalysisConfig = AnalysisConfig()

    debug: DebugConfig = DebugConfig()

    @classmethod
    def default(cls) -> DocSemConfig:
        """Return the default configuration."""
        return cls(
            extraction=ExtractorConfig(
                provider=ProviderName.AZURE,
            ),
            analysis=AnalysisConfig(
                llm=DEFAULT_LLM,
                overrides={},
                enabled=("table_structure", "table_continuation"),
            ),
        )
