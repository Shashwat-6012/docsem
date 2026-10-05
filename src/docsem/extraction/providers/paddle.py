"""
PaddleOCR PP-StructureV3 extractor implementation (CPU-tuned).

Requires:
    pip install paddlepaddle          # CPU build, 3.x
    pip install "paddleocr[doc-parser]"   # (older 3.0.x: pip install "paddleocr[all]")
    pip install pypdfium2 opencv-python-headless numpy

Design notes
------------
* Pages are rendered ourselves (pypdfium2) and fed to the pipeline one at a
  time as BGR arrays. This gives exact control over DPI, honours
  ExtractionInput.pages, keeps peak RAM low, and lets us normalize bboxes
  against the exact pixel size PP-StructureV3 saw.
* Tables are rebuilt from the HTML PP-StructureV3 emits (rowspan/colspan are
  expanded so every row is rectangular, same contract as AzureExtractor).
* `confidence` on blocks = mean OCR line score of the text lines whose centre
  falls inside the block (PaddleOCR gives no per-paragraph score).
  The layout-detector's score is kept in metadata["layout_score"].
"""

import logging
import os
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Skip the slow "is the model host reachable?" probe on every start-up.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import numpy as np

from docsem.extraction.base import (
    BaseExtractor,
    BlockType,
    BoundingBox,
    ExtractedBlock,
    ExtractedTable,
    ExtractionInput,
    ExtractionResult,
    TableCell,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Profiles. "balanced" is the default and what I'd run on a 16 GB / 8-core
# Ryzen laptop with no usable GPU.
# ----------------------------------------------------------------------
_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": dict(
        layout_detection_model_name="PP-DocLayout-M",
        text_detection_model_name="PP-OCRv5_mobile_det",
        use_region_detection=True,
        text_det_limit_type="max",
        text_det_limit_side_len=1536,
        dpi=150,
    ),
    "fast": dict(
        layout_detection_model_name="PP-DocLayout-S",
        text_detection_model_name="PP-OCRv5_mobile_det",
        use_region_detection=False,
        text_det_limit_type="max",
        text_det_limit_side_len=1280,
        dpi=120,
    ),
    "accurate": dict(
        layout_detection_model_name="PP-DocLayout_plus-L",
        text_detection_model_name="PP-OCRv5_server_det",
        text_recognition_model_name="PP-OCRv5_server_rec",
        use_region_detection=True,
        text_det_limit_type="max",
        text_det_limit_side_len=1920,
        dpi=200,
    ),
}

# Layout label -> BlockType (anything not listed becomes TEXT with
# metadata["label"] preserved, so headers/footers/footnotes can be filtered
# downstream).
_HEADING_LABELS = {"doc_title", "paragraph_title"}
_IMAGE_LABELS = {"image", "chart", "header_image", "footer_image", "formula"}
_TABLE_LABEL = "table"


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first present key/attribute (PaddleOCR versions differ between
    dict-style and object-style parsing results)."""
    for n in names:
        if isinstance(obj, dict):
            v = obj.get(n)
        else:
            v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _as_array(x: Any, cols: int | None = None) -> np.ndarray:
    if x is None:
        arr = np.zeros((0,), dtype=float)
    else:
        arr = np.asarray(x, dtype=float)
    if cols is not None:
        arr = arr.reshape(-1, cols) if arr.size else np.zeros((0, cols), dtype=float)
    return arr


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _to_int(v: Any, default: int = 1) -> int:
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------
# HTML table -> rectangular grid
# ----------------------------------------------------------------------
class _TableHTMLParser(HTMLParser):
    """Minimal parser for the flat <table> HTML PP-StructureV3 emits.
    Only the outermost table is read; nested tables are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self._depth = 0
        self._in_thead = False
        self._row: dict[str, Any] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
            return
        if self._depth != 1:
            return
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._flush_row()
            self._row = {"in_thead": self._in_thead, "cells": []}
        elif tag in ("td", "th") and self._row is not None:
            self._flush_cell()
            self._cell = {
                "text": [],
                "rs": _to_int(a.get("rowspan")),
                "cs": _to_int(a.get("colspan")),
                "th": tag == "th",
            }
        elif tag == "br" and self._cell is not None:
            self._cell["text"].append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
            return
        if self._depth != 1:
            return
        if tag == "thead":
            self._in_thead = False
        elif tag in ("td", "th"):
            self._flush_cell()
        elif tag == "tr":
            self._flush_row()

    def handle_data(self, data):
        if self._cell is not None and self._depth == 1:
            self._cell["text"].append(data)

    def _flush_cell(self):
        if self._cell is not None and self._row is not None:
            self._cell["text"] = " ".join("".join(self._cell["text"]).split())
            self._row["cells"].append(self._cell)
        self._cell = None

    def _flush_row(self):
        self._flush_cell()
        if self._row is not None and self._row["cells"]:
            self.rows.append(self._row)
        self._row = None

    def finish(self):
        self._flush_row()


def _html_to_grid(html: str) -> tuple[list[list[TableCell]], set[int]]:
    """Return (dense grid, indices of header rows). Spanned-into cells repeat
    the spanning cell's content so every row has the same width."""
    parser = _TableHTMLParser()
    parser.feed(html)
    parser.close()
    parser.finish()

    n_rows = len(parser.rows)
    placed: dict[tuple[int, int], str] = {}
    header_rows: set[int] = set()

    for r, row in enumerate(parser.rows):
        c = 0
        for cell in row["cells"]:
            while (r, c) in placed:
                c += 1
            for dr in range(cell["rs"]):
                if r + dr >= n_rows:
                    break
                for dc in range(cell["cs"]):
                    placed.setdefault((r + dr, c + dc), cell["text"])
            c += cell["cs"]
        if row["in_thead"] or all(cell["th"] for cell in row["cells"]):
            header_rows.add(r)

    if not placed:
        return [], set()

    n_cols = max(c for _, c in placed) + 1
    grid = [
        [TableCell(content=placed.get((r, c), "")) for c in range(n_cols)] for r in range(n_rows)
    ]
    return grid, header_rows


# ----------------------------------------------------------------------
# Extractor
# ----------------------------------------------------------------------
class PaddleOCRExtractor(BaseExtractor):
    """Extraction via PaddleOCR PP-StructureV3 (layout + OCR + table structure),
    tuned for CPU inference."""

    provider_name = "paddleocr"

    def __init__(
        self,
        lang: str = "en",
        use_gpu: bool = False,
        profile: str = "balanced",
        cpu_threads: int | None = None,
        enable_mkldnn: bool = False,
        dpi: int | None = None,
        max_side_px: int = 2800,
        first_row_as_header_fallback: bool = True,
        keep_raw_response: bool = True,
        **pipeline_overrides: Any,
    ):
        """
        lang: OCR language (ignored if you override the rec model name).
        profile: "fast" | "balanced" | "accurate" (see _PROFILES).
        cpu_threads: defaults to physical cores (logical // 2), capped at 8.
        enable_mkldnn: oneDNN acceleration. If you hit a oneDNN/PIR runtime
            error on your paddlepaddle version, set this to False.
        dpi / max_side_px: PDF render resolution and a cap on the longest side
            so oversized pages cannot blow up RAM/time.
        first_row_as_header_fallback: if the model returns no <thead>/<th>,
            treat row 0 as the header (flagged in table.metadata).
        pipeline_overrides: passed straight to PPStructureV3(...), winning over
            the profile (e.g. use_seal_recognition=True).
        """
        if profile not in _PROFILES:
            raise ValueError(f"Unknown profile {profile!r}; choose from {list(_PROFILES)}")

        try:
            from paddleocr import PPStructureV3
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "PaddleOCR is not installed. Run: pip install paddlepaddle 'paddleocr[doc-parser]'"
            ) from e

        prof = dict(_PROFILES[profile])
        self.dpi = int(dpi or prof.pop("dpi"))
        prof.pop("dpi", None)
        self.max_side_px = max_side_px
        self.first_row_as_header_fallback = first_row_as_header_fallback
        self.keep_raw_response = keep_raw_response
        self.lang = lang
        self.profile = profile

        if cpu_threads is None:
            cpu_threads = min(8, max(1, (os.cpu_count() or 2) // 2))

        kwargs: dict[str, Any] = dict(
            device="gpu:0" if use_gpu else "cpu",
            # Pre-processing models: only useful for phone photos / skewed
            # scans. Born-digital PDFs don't need them, and each is a full
            # extra network pass per page.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # Heavy optional modules you don't map into ExtractionResult.
            use_seal_recognition=False,
            use_formula_recognition=False,
            use_chart_recognition=False,
            # The whole point of this provider.
            use_table_recognition=True,
            # Keep table content as HTML (we parse it into a grid).
            format_block_content=False,
            enable_mkldnn=enable_mkldnn,
            cpu_threads=cpu_threads,
        )
        kwargs.update(prof)
        kwargs.update(pipeline_overrides)
        if "text_recognition_model_name" not in kwargs:
            kwargs["lang"] = lang  # lang selects the rec model only if none is named

        logger.info("Loading PP-StructureV3 (profile=%s, device=%s)", profile, kwargs["device"])
        self._pipeline = PPStructureV3(**kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def extract(self, input_data: ExtractionInput) -> ExtractionResult:
        dpi = int(input_data.options.get("dpi", self.dpi))
        warnings: list[str] = []
        blocks: list[ExtractedBlock] = []
        tables: list[ExtractedTable] = []
        raw_parts: list[str] = []
        raw_pages: list[dict[str, Any]] = []
        pages_done = 0

        for page_no, img in self._iter_pages(input_data, dpi, warnings):
            h, w = img.shape[:2]
            try:
                outputs = self._pipeline.predict(img)
            except Exception as exc:  # keep going; surface it in warnings
                logger.exception("PP-StructureV3 failed on page %s", page_no)
                warnings.append(f"page {page_no}: PP-StructureV3 failed: {exc!r}")
                continue

            pages_done += 1
            for res in outputs:
                data = self._res_to_dict(res)
                if self.keep_raw_response:
                    raw_pages.append({"page": page_no, **data})
                self._map_page(data, page_no, w, h, blocks, tables, raw_parts, warnings)

        return ExtractionResult(
            blocks=blocks,
            tables=tables,
            raw_text="\n\n".join(p for p in raw_parts if p),
            provider=self.provider_name,
            page_count=pages_done,
            warnings=warnings,
            raw_response=raw_pages if self.keep_raw_response else None,
        )

    # ------------------------------------------------------------------
    # Page rendering / loading
    # ------------------------------------------------------------------
    def _iter_pages(
        self, input_data: ExtractionInput, dpi: int, warnings: list[str]
    ) -> Iterator[tuple[int, np.ndarray]]:
        """Yield (1-indexed page number, BGR uint8 array), one page at a time."""
        path = Path(input_data.file_path)
        is_pdf = path.suffix.lower() == ".pdf" or (input_data.mime_type or "") == "application/pdf"

        if not is_pdf:
            import cv2

            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Could not decode image: {path}")
            if input_data.pages and input_data.pages != [1]:
                warnings.append("pages ignored for single-image input")
            yield 1, self._cap_size(img)
            return

        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            n = len(pdf)
            if input_data.pages:
                wanted = sorted({p for p in input_data.pages if 1 <= p <= n})
                skipped = sorted(set(input_data.pages) - set(wanted))
                if skipped:
                    warnings.append(f"requested pages out of range (document has {n}): {skipped}")
            else:
                wanted = list(range(1, n + 1))

            for page_no in wanted:
                page = pdf[page_no - 1]
                try:
                    pw, ph = page.get_size()  # PDF points
                    scale = dpi / 72.0
                    longest = max(pw, ph) * scale
                    if longest > self.max_side_px:
                        scale = self.max_side_px / max(pw, ph)
                    arr = page.render(scale=scale).to_numpy()  # BGR(A)
                finally:
                    page.close()
                yield page_no, self._to_bgr(arr)
        finally:
            pdf.close()

    @staticmethod
    def _to_bgr(arr: np.ndarray) -> np.ndarray:
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        elif arr.shape[2] == 4:
            arr = arr[:, :, :3]
        return np.ascontiguousarray(arr)

    def _cap_size(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        longest = max(h, w)
        if longest <= self.max_side_px:
            return img
        import cv2

        s = self.max_side_px / longest
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

    # ------------------------------------------------------------------
    # Result mapping
    # ------------------------------------------------------------------
    @staticmethod
    def _res_to_dict(res: Any) -> dict[str, Any]:
        j = getattr(res, "json", None)
        if j is None:
            j = dict(res)
        return j.get("res", j) if isinstance(j, dict) else dict(j)

    def _map_page(
        self,
        data: dict[str, Any],
        page_no: int,
        w: int,
        h: int,
        blocks: list[ExtractedBlock],
        tables: list[ExtractedTable],
        raw_parts: list[str],
        warnings: list[str],
    ) -> None:
        parsing = data.get("parsing_res_list") or []
        ocr = data.get("overall_ocr_res") or {}
        layout_boxes = (data.get("layout_det_res") or {}).get("boxes") or []

        line_boxes = _as_array(_get(ocr, "rec_boxes"), cols=4)
        line_scores = _as_array(_get(ocr, "rec_scores"))
        if len(line_scores) != len(line_boxes):
            line_boxes = np.zeros((0, 4), dtype=float)  # can't align -> no confidence
            line_scores = np.zeros((0,), dtype=float)
        centers = (
            np.stack(
                [
                    (line_boxes[:, 0] + line_boxes[:, 2]) / 2,
                    (line_boxes[:, 1] + line_boxes[:, 3]) / 2,
                ],
                axis=1,
            )
            if len(line_boxes)
            else np.zeros((0, 2))
        )

        for order, item in enumerate(parsing):
            label = str(_get(item, "block_label", "label", default="text"))
            content = _get(item, "block_content", "content", default="") or ""
            coord = _get(item, "block_bbox", "bbox")
            ctx = f"page {page_no} {label}[{order}]"

            bbox = self._to_bbox(coord, page_no, w, h, warnings, ctx)
            confidence = self._text_confidence(coord, centers, line_scores)
            meta: dict[str, Any] = {
                "label": label,
                "page": page_no,
                "reading_order": order,
                "layout_score": self._layout_score(coord, label, layout_boxes),
            }

            if label == _TABLE_LABEL:
                table = self._build_table(
                    str(content), bbox, page_no, meta, confidence, warnings, ctx
                )
                if table is not None:
                    tables.append(table)
                    raw_parts.append(
                        "\n".join(
                            " | ".join(c.content for c in row) for row in table.header + table.rows
                        )
                    )
                    continue
                # Unparseable table: fall through and keep whatever text we got.

            if label in _IMAGE_LABELS and not str(content).strip():
                btype = BlockType.IMAGE
            elif not str(content).strip():
                continue  # empty non-image region, nothing to keep
            elif label in _HEADING_LABELS:
                btype = BlockType.HEADING
            else:
                btype = BlockType.TEXT

            blocks.append(
                ExtractedBlock(
                    type=btype,
                    content=str(content),
                    confidence=confidence,
                    bbox=bbox,
                    metadata=meta,
                )
            )
            if btype != BlockType.IMAGE:
                raw_parts.append(str(content))

    def _build_table(
        self,
        html: str,
        bbox: BoundingBox | None,
        page_no: int,
        meta: dict[str, Any],
        confidence: float | None,
        warnings: list[str],
        ctx: str,
    ) -> ExtractedTable | None:
        try:
            grid, header_idx = _html_to_grid(html)
        except Exception as exc:
            warnings.append(f"{ctx}: table HTML could not be parsed ({exc!r})")
            return None
        if not grid:
            warnings.append(f"{ctx}: table HTML produced an empty grid")
            return None

        header_inferred = False
        if not header_idx and self.first_row_as_header_fallback and len(grid) > 1:
            header_idx = {0}
            header_inferred = True

        header_rows = [row for i, row in enumerate(grid) if i in header_idx]
        data_rows = [row for i, row in enumerate(grid) if i not in header_idx]

        meta = dict(meta)
        meta["mean_ocr_confidence"] = confidence
        if header_inferred:
            meta["header_inferred"] = True

        return ExtractedTable(
            header=header_rows,
            rows=data_rows,
            bbox=bbox,
            bbox_by_page={page_no: bbox} if bbox is not None else {},
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_bbox(
        coord: Any, page_no: int, w: int, h: int, warnings: list[str], ctx: str
    ) -> BoundingBox | None:
        if coord is None:
            warnings.append(f"{ctx}: no bbox on element")
            return None
        try:
            x0, y0, x1, y1 = (float(v) for v in list(coord)[:4])
        except (TypeError, ValueError):
            warnings.append(f"{ctx}: unusable bbox {coord!r}")
            return None

        def clamp(v: float) -> float:
            return min(1.0, max(0.0, v))

        return BoundingBox(
            x0=clamp(x0 / w),
            y0=clamp(y0 / h),
            x1=clamp(x1 / w),
            y1=clamp(y1 / h),
            page=page_no,
            page_width=float(w),
            page_height=float(h),
            page_unit="pixel",  # rendered-page pixels (PDFs rasterized at self.dpi)
        )

    @staticmethod
    def _text_confidence(coord: Any, centers: np.ndarray, scores: np.ndarray) -> float | None:
        if coord is None or not len(scores):
            return None
        try:
            x0, y0, x1, y1 = (float(v) for v in list(coord)[:4])
        except (TypeError, ValueError):
            return None
        mask = (
            (centers[:, 0] >= x0)
            & (centers[:, 0] <= x1)
            & (centers[:, 1] >= y0)
            & (centers[:, 1] <= y1)
        )
        if not mask.any():
            return None
        return round(float(scores[mask].mean()), 4)

    @staticmethod
    def _layout_score(coord: Any, label: str, layout_boxes: list[Any]) -> float | None:
        if coord is None:
            return None
        try:
            target = np.asarray(list(coord)[:4], dtype=float)
        except (TypeError, ValueError):
            return None
        best, best_iou = None, 0.7
        for lb in layout_boxes:
            if str(_get(lb, "label", "cls_name", default="")) != label:
                continue
            lc = _get(lb, "coordinate", "bbox")
            if lc is None:
                continue
            iou = _iou(target, np.asarray(list(lc)[:4], dtype=float))
            if iou >= best_iou:
                best, best_iou = _get(lb, "score"), iou
        return None if best is None else round(float(best), 4)
