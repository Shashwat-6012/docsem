
"""
Public API for DocSem.

DocSem processes documents through two stages:

1. Extraction: Obtain text, bounding boxes, headers, tables, and
   other page-level information.
2. Structuring: Convert extracted information into a DocumentIR.

The concrete implementations remain hidden behind this API.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .config import DocSemConfig
from .exceptions import (
    DocumentError,
    DocumentNotFoundError,
)

import json
from dataclasses import asdict
from pathlib import Path


from .extraction.factory import build_extractor
from .extraction.base import ExtractionInput

from .ir.build import build_document_ir
from .render.document import render

if TYPE_CHECKING:
    from .ir.document import DocumentIR


class DocSem:
    """
    Main public interface for document processing.

    Example
    -------
    >>> docsem = DocSem()
    >>> document = docsem.process("report.pdf")
    """

    def __init__(
        self,
        config: DocSemConfig | None = None,
    ) -> None:
        """
        Initialize the DocSem processing engine.

        Parameters
        ----------
        config:
            Optional configuration. If omitted, default configuration
            is used.
        """
        self.config = config or DocSemConfig()
        self.extractor = build_extractor(self.config.extraction)

    def process(
        self,
        source: str | Path | bytes,
        *,
        filename: str | None = None,
    ):
        """
        Process a document and return its DocumentIR.

        Parameters
        ----------
        source:
            A path to a document or raw document bytes.

        filename:
            Optional filename for byte sources. Useful for format
            detection when the file extension is unavailable.

        Returns
        -------
        DocumentIR
            The structured representation of the document.

        Raises
        ------
        DocumentError
            If the document source is invalid.
        """

        validated_source = self._validate_source(
            source
        )

        # Step 1 - Extraction: Obtain text, bounding boxes, tables, etc.
        extracted = self.extractor.extract(ExtractionInput(file_path=validated_source))

        # Step 2 - Build the DocumentIR from the extracted data.
        document_ir = build_document_ir(extracted)

        rendered_document = render(document_ir)

        return rendered_document

    def _validate_source(
        self,
        source: str | Path
    ):
        """
        Validate and normalize the input source.

        This method performs basic input validation only.
        Actual document format detection belongs to the extraction layer.
        """

        if isinstance(source, (str, Path)):
            path = Path(source)

            if not path.exists():
                raise DocumentNotFoundError(
                    f"Document not found: {path}"
                )

            if not path.is_file():
                raise DocumentError(
                    f"Expected a file, got: {path}"
                )

            return path

        raise DocumentError(
            "Unsupported source type. Expected a file path."
        )

    def __repr__(self) -> str:
        return f"DocSem(config={self.config!r})"
