
"""
Public API for DocSem.

DocSem processes documents through two stages:

1. Extraction: Obtain text, bounding boxes, headers, tables, and
   other page-level information.
2. Structuring: Convert extracted information into a DocumentIR.

The concrete implementations remain hidden behind this API.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ._warnings import WarningAggregator
from .config import DocSemConfig
from .exceptions import (
    DocumentError,
    DocumentNotFoundError,
)

from .extraction.factory import build_extractor
from .extraction.base import ExtractionInput

from .ir.build import IRBuilder

logger = logging.getLogger(__name__)

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
        self.config = config or DocSemConfig.default()
        self.extractor = build_extractor(self.config.extraction)
        self.builder = IRBuilder()

    def process(
        self,
        source: str | Path
    ) -> DocumentIR:
        """
        Process a document and return its DocumentIR.

        Parameters
        ----------
        source:
            A path to a document or raw document bytes.

        Returns
        -------
        DocumentIR
            The structured representation of the document.

        Raises
        ------
        DocumentError
            If the document source is invalid.
        """

        validated_source = self._validate_source(source)
        doc_id = validated_source.name
        start = time.perf_counter()
        aggregator = WarningAggregator(logger, doc_id)
        document_ir = None
        page_count = None

        try:
            # Step 1 - Extraction: Obtain text, bounding boxes, tables, etc.
            extracted = self.extractor.extract(ExtractionInput(file_path=validated_source))
            page_count = getattr(extracted, "page_count", None)

            if page_count is not None and page_count > 0 and not getattr(extracted, "blocks", None) and not getattr(extracted, "tables", None):
                for page_no in range(1, page_count + 1):
                    aggregator.add("no_text_layer", page_no)

            start_extra = {"docsem_doc_id": doc_id}
            if page_count is not None:
                start_extra["docsem_pages_total"] = page_count
            logger.info("document processing started", extra=start_extra)

            # Step 2 - Build the DocumentIR from the extracted data.
            document_ir = self.builder.build(extracted)
            return document_ir
        finally:
            aggregator.flush()
            if document_ir is not None:
                finish_extra = {"docsem_doc_id": doc_id}
                if page_count is not None:
                    finish_extra["docsem_pages_total"] = page_count
                finish_extra["docsem_pages_ok"] = 1
                finish_extra["docsem_pages_failed"] = 0
                finish_extra["docsem_duration_ms"] = int((time.perf_counter() - start) * 1000)
                logger.info("document processing finished", extra=finish_extra)

    def _validate_source(self, source: str | Path) -> Path:
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
