"""
Azure Document Intelligence extractor implementation.

Requires: pip install azure-ai-formrecognizer azure-core
"""

import logging
from typing import Optional

from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient

from ..base import (
    BaseExtractor,
    ExtractionInput,
    ExtractionResult,
    ExtractedBlock,
    ExtractedTable,
    TableCell,
    BlockType,
    BoundingBox,
)

logger = logging.getLogger(__name__)


class AzureExtractor(BaseExtractor):
    """Extraction via Azure AI Document Intelligence (Form Recognizer)."""

    provider_name = "azure"

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model_id: str = "prebuilt-document",
    ):
        if not endpoint or not api_key:
            raise ValueError("Azure extractor requires both endpoint and api_key")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_id = model_id
        self._client = DocumentAnalysisClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.api_key),
        )

    def extract(self, input_data: ExtractionInput) -> ExtractionResult:



        with open(input_data.file_path, "rb") as f:
            poller = self._client.begin_analyze_document(
                self.model_id,
                document=f,
                pages=self._pages_param(input_data.pages),
            )

        result = poller.result()

        # Page dimensions are needed to normalize every bbox on that page.
        # Keyed by 1-indexed page number, matching BoundingRegion.page_number.
        page_dims = self._page_dims(result)

        table_span_ranges = self._table_span_ranges(result)

        warnings = self._collect_warnings(result)

        blocks = self._map_blocks(result, skip_ranges=table_span_ranges, page_dims=page_dims, warnings=warnings)
        tables = self._map_tables(result, page_dims=page_dims, warnings=warnings)

        return ExtractionResult(
            blocks=blocks,
            tables=tables,
            raw_text=result.content or "",
            provider=self.provider_name,
            page_count=len(result.pages) if result.pages else 0,
            warnings=warnings,
            raw_response=result,
        )

    @staticmethod
    def _pages_param(pages: Optional[list[int]]) -> Optional[str]:
        if not pages:
            return None
        return ",".join(str(p) for p in pages)

    @staticmethod
    def _collect_warnings(result) -> list[str]:
        warnings = []
        raw_warnings = getattr(result, "warnings", None) or []
        for w in raw_warnings:
            warnings.append(getattr(w, "message", str(w)))
        return warnings

    @staticmethod
    def _page_dims(result) -> dict[int, tuple[float, float, str]]:
        """page_number -> (width, height, unit) for every page in the result.
        Needed to turn each polygon's absolute coordinates into normalized
        [0,1] fractions. Azure reports unit as 'inch' or 'pixel' depending on
        whether the page came from a native PDF text layer or a rasterized
        image/scan."""
        dims = {}
        for page in getattr(result, "pages", None) or []:
            width = getattr(page, "width", None)
            height = getattr(page, "height", None)
            unit = getattr(page, "unit", None)
            if width and height:
                dims[page.page_number] = (width, height, unit)
        return dims

    # ------------------------------------------------------------------
    # Blocks: paragraphs, headings, key-value pairs
    # ------------------------------------------------------------------

    def _map_blocks(
        self,
        result,
        skip_ranges: list[tuple[int, int]],
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
    ) -> list[ExtractedBlock]:
        blocks: list[ExtractedBlock] = []
        blocks.extend(self._map_paragraphs(result, skip_ranges, page_dims, warnings))
        blocks.extend(self._map_key_value_pairs(result, page_dims, warnings))
        return blocks

    def _map_paragraphs(
        self,
        result,
        skip_ranges: list[tuple[int, int]],
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
    ) -> list[ExtractedBlock]:
        blocks = []
        for para in getattr(result, "paragraphs", None) or []:
            span = para.spans[0] if para.spans else None
            if span and self._in_ranges(span.offset, skip_ranges):
                continue  # content already represented in a table

            role = getattr(para, "role", None)
            bbox = self._first_bbox(
                getattr(para, "bounding_regions", None), page_dims, warnings,
                context=f"paragraph {para.content[:30]!r}",
            )
            blocks.append(
                ExtractedBlock(
                    type=self._role_to_block_type(role),
                    content=para.content,
                    # Azure's paragraphs carry no confidence score in the API response
                    # (only words/cells/some KV pairs do) — None here is expected,
                    # not a bug. Derive it yourself from constituent word confidences
                    # if you need paragraph-level confidence.
                    confidence=None,
                    bbox=bbox,
                    metadata={"role": role} if role else {},
                )
            )
        return blocks

    def _map_key_value_pairs(
        self,
        result,
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
    ) -> list[ExtractedBlock]:
        blocks = []
        for kv in getattr(result, "key_value_pairs", None) or []:
            key_content = kv.key.content if kv.key else ""
            value_content = kv.value.content if kv.value else ""

            bbox = None
            if kv.key is not None:
                bbox = self._first_bbox(
                    getattr(kv.key, "bounding_regions", None), page_dims, warnings,
                    context=f"key-value key {key_content[:30]!r}",
                )
            else:
                warnings.append(f"KV pair value={value_content[:30]!r} has no key element; bbox unavailable")

            blocks.append(
                ExtractedBlock(
                    type=BlockType.KEY_VALUE,
                    content=f"{key_content}: {value_content}",
                    confidence=getattr(kv, "confidence", None),
                    bbox=bbox,
                    metadata={"key": key_content, "value": value_content},
                )
            )
        return blocks

    @staticmethod
    def _role_to_block_type(role: Optional[str]) -> BlockType:
        if role in ("title", "sectionHeading"):
            return BlockType.HEADING
        return BlockType.TEXT

    # ------------------------------------------------------------------
    # Tables: reconstruct grid internally, expose only header + ordered rows
    # ------------------------------------------------------------------

    def _map_tables(
        self,
        result,
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
    ) -> list[ExtractedTable]:
        tables = []
        for t_idx, table in enumerate(getattr(result, "tables", None) or []):
            grid = self._build_grid(table)

            header_row_indices = self._header_row_indices(table)
            header: list[TableCell] = []
            data_rows: list[list[TableCell]] = []

            for row_idx, row in enumerate(grid):
                if row_idx in header_row_indices:
                    # Merge multiple header rows into one flat header, left-to-right,
                    # top-to-bottom, in case the table has a multi-row header.
                    header.extend(row)
                else:
                    data_rows.append(row)

            regions = getattr(table, "bounding_regions", None) or []
            bbox_by_page: dict[int, BoundingBox] = {}
            for region in regions:
                box = self._region_to_bbox(region, page_dims, warnings, context=f"table[{t_idx}]")
                if box is not None:
                    bbox_by_page[box.page] = box

            if not regions:
                warnings.append(f"table[{t_idx}] has no bounding_regions at all")
            elif not bbox_by_page:
                warnings.append(f"table[{t_idx}] has {len(regions)} bounding_region(s) but none produced a usable bbox")

            # Primary bbox kept for backwards-compat convenience: first page the
            # table appears on, in region order (== reading order for tables
            # that don't span pages, which is the common case).
            primary_bbox = bbox_by_page.get(regions[0].page_number) if regions else None

            tables.append(
                ExtractedTable(
                    header=header,
                    rows=data_rows,
                    bbox=primary_bbox,
                    bbox_by_page=bbox_by_page,
                    metadata={"spans_pages": sorted(bbox_by_page.keys())} if len(bbox_by_page) > 1 else {},
                )
            )
        return tables

    @staticmethod
    def _header_row_indices(table) -> set[int]:
        """Row indices that contain at least one columnHeader cell."""
        return {
            cell.row_index
            for cell in table.cells
            if getattr(cell, "kind", None) == "columnHeader"
        }

    @staticmethod
    def _build_grid(table) -> list[list[TableCell]]:
        """Reconstruct a dense row x column grid from Azure's flat cell list,
        filling merged/spanned cells so every row has column_count entries
        and list order alone conveys position."""
        grid: list[list[Optional[TableCell]]] = [
            [None for _ in range(table.column_count)] for _ in range(table.row_count)
        ]

        for cell in table.cells:
            content = (cell.content or "").strip()
            confidence = getattr(cell, "confidence", None)
            row_span = getattr(cell, "row_span", 1) or 1
            col_span = getattr(cell, "column_span", 1) or 1

            for r in range(cell.row_index, min(cell.row_index + row_span, table.row_count)):
                for c in range(cell.column_index, min(cell.column_index + col_span, table.column_count)):
                    if grid[r][c] is None:
                        # First cell of a span carries the real content;
                        # spanned-into cells repeat it so rows stay rectangular.
                        grid[r][c] = TableCell(content=content, confidence=confidence)

        # Fill any remaining gaps (shouldn't normally happen, but keep rows rectangular)
        for r in range(table.row_count):
            for c in range(table.column_count):
                if grid[r][c] is None:
                    grid[r][c] = TableCell(content="")

        return grid  # type: ignore[return-value]

    def _table_span_ranges(self, result) -> list[tuple[int, int]]:
        ranges = []
        for table in getattr(result, "tables", None) or []:
            for span in getattr(table, "spans", None) or []:
                ranges.append((span.offset, span.offset + span.length))
        return ranges

    @staticmethod
    def _in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
        return any(start <= offset < end for start, end in ranges)

    # ------------------------------------------------------------------
    # Shared bbox helpers
    # ------------------------------------------------------------------

    def _first_bbox(
        self,
        bounding_regions,
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
        context: str = "",
    ) -> Optional[BoundingBox]:
        """Bbox from the first bounding region only. Use this for blocks that
        can't reasonably span pages (paragraphs, KV pairs). For anything that
        can span pages (tables), use bbox_by_page via _region_to_bbox instead."""
        if not bounding_regions:
            warnings.append(f"{context}: no bounding_regions on element")
            return None
        return self._region_to_bbox(bounding_regions[0], page_dims, warnings, context)

    @staticmethod
    def _region_to_bbox(
        region,
        page_dims: dict[int, tuple[float, float, str]],
        warnings: list[str],
        context: str = "",
    ) -> Optional[BoundingBox]:
        poly = getattr(region, "polygon", None)
        page_number = getattr(region, "page_number", None)

        if not poly:
            warnings.append(f"{context}: bounding_region has empty/missing polygon (page {page_number})")
            return None

        # azure-ai-formrecognizer's stable release (3.x) represents `polygon` as
        # Sequence[Point], where Point is a namedtuple("Point", "x y") — NOT a
        # flat list of floats. Each element has .x/.y attributes. A normal quad
        # has 4 Points, listed clockwise from top-left. Anything else is a
        # degenerate/rotated detection; rather than dropping it, use whatever
        # points exist — a rectangle from 2+ points is still more useful than
        # nothing for markdown placement.
        try:
            xs = [p.x for p in poly]
            ys = [p.y for p in poly]
        except AttributeError:
            warnings.append(
                f"{context}: polygon elements have no .x/.y (page {page_number}); "
                f"got type {type(poly[0]).__name__ if poly else 'unknown'} — check SDK version, skipping"
            )
            return None

        if len(poly) < 4:
            warnings.append(
                f"{context}: polygon has only {len(poly)} point(s) (page {page_number}), "
                "expected 4 — using best-effort bounding rectangle from available points"
            )

        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)

        page_width = page_height = page_unit = None
        if page_number in page_dims:
            page_width, page_height, page_unit = page_dims[page_number]
        else:
            warnings.append(f"{context}: no page dimensions found for page {page_number}; bbox left unnormalized")

        if page_width and page_height:
            # Normalize to [0,1] fraction of page so PDF (inch) and scanned (pixel)
            # sources are comparable on the same scale for layout/placement logic.
            return BoundingBox(
                x0=x0 / page_width,
                y0=y0 / page_height,
                x1=x1 / page_width,
                y1=y1 / page_height,
                page=page_number,
                page_width=page_width,
                page_height=page_height,
                page_unit=page_unit,
            )

        # No page dims available (shouldn't normally happen) — fall back to raw,
        # unnormalized coordinates rather than losing the box entirely.
        return BoundingBox(
            x0=x0, y0=y0, x1=x1, y1=y1,
            page=page_number if page_number is not None else -1,
        )