"""
Public exception hierarchy for DocSem.
"""


class DocSemError(Exception):
    """Base exception for all DocSem errors."""

    pass


# ----------------------------------------------------------------------
# Document / input errors
# ----------------------------------------------------------------------


class DocumentError(DocSemError):
    """Base class for document-related errors."""

    pass


class DocumentNotFoundError(DocumentError):
    """Raised when the input document cannot be found."""

    pass


class UnsupportedDocumentError(DocumentError):
    """Raised when the document format is not supported."""

    pass


class InvalidDocumentError(DocumentError):
    """Raised when the document is invalid or cannot be parsed."""

    pass


class CorruptDocumentError(InvalidDocumentError):
    """Raised when the document appears to be corrupted."""

    pass


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------


class ExtractionError(DocSemError):
    """Base class for extraction failures."""

    pass


class TextExtractionError(ExtractionError):
    """Raised when text extraction fails."""

    pass


class LayoutExtractionError(ExtractionError):
    """Raised when layout information cannot be extracted."""

    pass


class OCRExtractionError(ExtractionError):
    """Raised when OCR processing fails."""

    pass


class TableExtractionError(ExtractionError):
    """Raised when table extraction fails."""

    pass


# ----------------------------------------------------------------------
# Document structuring
# ----------------------------------------------------------------------


class StructuringError(DocSemError):
    """Base class for DocumentIR construction failures."""

    pass


class RelationshipError(StructuringError):
    """Raised when relationships between document elements cannot be resolved."""

    pass


class DocumentIRBuildError(StructuringError):
    """Raised when the extracted document cannot be converted into DocumentIR."""

    pass


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------


class ConfigurationError(DocSemError):
    """Raised when DocSem configuration is invalid."""

    pass