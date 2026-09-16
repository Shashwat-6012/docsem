
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

    def process(
        self,
        source: str | Path | bytes,
        *,
        filename: str | None = None,
    ) -> DocumentIR:
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

        normalized_source = self._validate_source(
            source,
            filename=filename,
        )

        extracted = self._extract(normalized_source)

        document_ir = self._build_document_ir(extracted)

        return document_ir

    def _validate_source(
        self,
        source: str | Path | bytes,
        *,
        filename: str | None = None,
    ):
        """
        Validate and normalize the input source.

        This method performs basic input validation only.
        Actual document format detection belongs to the extraction layer.
        """

        if isinstance(source, bytes):
            if not source:
                raise DocumentError("Document data is empty.")

            return {
                "data": source,
                "filename": filename,
            }

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
            "Unsupported source type. Expected a file path or bytes."
        )

    def _extract(self, source):
        """
        Execute the extraction stage.

        Expected output may include:

        - Text
        - Bounding boxes
        - Page numbers
        - Headers
        - Tables
        - Images
        - Reading order
        - Page-level metadata

        Implement this by delegating to your extraction layer.
        """

        raise NotImplementedError(
            "Extraction implementation has not been configured."
        )

    def _build_document_ir(self, extracted) -> DocumentIR:
        """
        Execute the structuring stage.

        Convert extracted document information into a DocumentIR.

        This is where layout relationships, semantic relationships,
        and document structure are resolved.
        """

        raise NotImplementedError(
            "DocumentIR construction has not been implemented."
        )

    def __repr__(self) -> str:
        return f"DocSem(config={self.config!r})"
