from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import uuid

# ---------- Input ----------

@dataclass
class ExtractionInput:
    file_path: Path
    mime_type: Optional[str] = None
    pages: Optional[list[int]] = None
    options: dict[str, Any] = field(default_factory=dict)


# ---------- Shared ----------

class BlockType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    IMAGE = "image"
    KEY_VALUE = "key_value"


@dataclass
class BoundingBox:
    """
    Standardized, provider-agnostic bounding box.

    Coordinates are normalized to [0, 1] as a fraction of page width/height,
    origin top-left, so a box is comparable for layout purposes regardless
    of whether the source page was a PDF (points/inches) or a scanned image
    (pixels), and regardless of DPI.

    x0, y0 = top-left corner (fraction of page width/height)
    x1, y1 = bottom-right corner (fraction of page width/height)
    page   = 1-indexed page number (matches how humans/PDF viewers reference pages;
             convert to 0-indexed yourself if zipping against a 0-indexed page list)

    page_width / page_height are the *original* unit page dimensions (not
    normalized) so you can convert back to absolute coordinates if you ever
    need to draw over the source image/PDF, and so you can tell what unit
    system produced this box (e.g. inches for PDF text layer, pixels for
    scanned/rasterized pages).
    page_unit records that unit explicitly rather than leaving it implicit.
    """
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
    page_width: Optional[float] = None
    page_height: Optional[float] = None
    page_unit: Optional[str] = None  # "inch" | "pixel" | None if unknown

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0



# ---------- Text-like content ----------

@dataclass
class ExtractedBlock:
    """Any non-tabular content: paragraphs, headings, key-value pairs, image captions, etc."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: BlockType
    content: str
    confidence: Optional[float] = None
    bbox: Optional[BoundingBox] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------- Tabular content ----------

@dataclass
class TableCell:
    content: str
    confidence: Optional[float] = None


@dataclass
class ExtractedTable:
    """A table: one header row and a list of data rows, order-preserved.
    No row/col indices — position is implicit in list order."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    header: list[TableCell]
    rows: list[list[TableCell]]
    bbox: Optional[BoundingBox] = None
    # bbox_by_page covers tables that span multiple pages: page_number -> BoundingBox
    # for the portion of the table on that page. Populated whenever a table's
    # bounding_regions has more than one entry; bbox above is always just the
    # first page's box, kept for backwards-compat convenience.
    bbox_by_page: dict[int, BoundingBox] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

# --- Custom Collection Classes (Inheriting from list) ---
# Created for querying and filtering extracted blocks and tables more easily.

class ExtractedBlockList(list):
    """A list of ExtractedBlocks with built-in query helpers."""
    
    def get(self, block_id: str) -> Optional[Any]:
        """Get a single block by its ID."""
        return next((b for b in self if b.id == block_id), None)

    def headings(self) -> list:
        """Return blocks that represent headings."""
        return [b for b in self if "heading" in str(b.type).lower()]

    def paragraphs(self) -> list:
        """Return blocks that represent paragraphs."""
        return [b for b in self if "paragraph" in str(b.type).lower()]

    def by_type(self, block_type: Any) -> list:
        """Return blocks matching a specific type."""
        return [b for b in self if b.type == block_type]


class ExtractedTableList(list):
    """A list of ExtractedTables with built-in query helpers."""
    
    def get(self, table_id: str) -> Optional[Any]:
        """Get a single table by its ID."""
        return next((t for t in self if t.id == table_id), None)

    def multi_page(self) -> list:
        """Return tables that span multiple pages."""
        return [t for t in self if len(t.bbox_by_page) > 1]

# ---------- Output ----------

@dataclass
class ExtractionResult:
    blocks: list[ExtractedBlock]
    tables: list[ExtractedTable] = field(default_factory=list)
    raw_text: str = ""
    provider: str = ""
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    raw_response: Optional[Any] = None

    def __post_init__(self):
        # Automatically wrap standard lists into custom queryable lists on initialization
        self.blocks = ExtractedBlockList(self.blocks)
        self.tables = ExtractedTableList(self.tables)


# ---------- Contract ----------

class BaseExtractor(ABC):
    provider_name: str = "base"

    @abstractmethod
    def extract(self, input_data: ExtractionInput) -> ExtractionResult:
        raise NotImplementedError
