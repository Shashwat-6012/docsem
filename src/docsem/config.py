"""
Configuration for DocSem.

Configuration describes how a document should be processed while
remaining independent of concrete implementation libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from .extraction.factory import ExtractorConfig, ProviderName


ExtractionMode = Literal[
    "auto",
    "text",
    "ocr",
    "layout",
]


StructuringMode = Literal[
    "layout",
    "semantic",
    "hybrid",
]


@dataclass(frozen=True)
class StructuringConfig:
    """
    Configuration for converting extracted document elements into
    a coherent DocumentIR.
    """

    mode: StructuringMode = "hybrid"

    use_layout: bool = True
    use_semantics: bool = True

    cross_page_relationships: bool = True

    merge_split_tables: bool = True
    merge_split_paragraphs: bool = True


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

    structuring: StructuringConfig = StructuringConfig()

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
