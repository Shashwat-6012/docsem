"""
Configuration for DocSem.

Configuration describes how a document should be processed while
remaining independent of concrete implementation libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
class ExtractionConfig:
    """
    Configuration for the document extraction stage.
    """

    mode: ExtractionMode = "auto"

    extract_text: bool = True
    extract_bbox: bool = True
    extract_headers: bool = True
    extract_tables: bool = True

    preserve_page_structure: bool = True

    language: str | None = None


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

    extraction: ExtractionConfig = ExtractionConfig()

    structuring: StructuringConfig = StructuringConfig()

    @classmethod
    def default(cls) -> "DocSemConfig":
        """Return the default configuration."""
        return cls()

    @classmethod
    def layout_aware(cls) -> "DocSemConfig":
        """Configuration optimized for layout-aware processing."""

        return cls(
            extraction=ExtractionConfig(
                mode="layout",
            ),
            structuring=StructuringConfig(
                mode="layout",
                use_layout=True,
                use_semantics=False,
            ),
        )

    @classmethod
    def semantic(cls) -> "DocSemConfig":
        """Configuration optimized for semantic structuring."""

        return cls(
            structuring=StructuringConfig(
                mode="semantic",
                use_layout=True,
                use_semantics=True,
            )
        )

    @classmethod
    def hybrid(cls) -> "DocSemConfig":
        """
        Configuration combining layout and semantic information.

        This is expected to be the primary mode for DocSem.
        """

        return cls(
            structuring=StructuringConfig(
                mode="hybrid",
                use_layout=True,
                use_semantics=True,
                cross_page_relationships=True,
                merge_split_tables=True,
                merge_split_paragraphs=True,
            )
        )