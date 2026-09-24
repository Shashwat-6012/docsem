"""
Analyzers for DocumentIR construction: TableContinuationAnalyzer.

TableContinuationAnalyzer finds cross-page table fragments
that are really one logical table, using the reading order to reason about
adjacency and "what's in between."

Both are Analyzer subclasses (see analyzer_base.py) — pure with respect
to `IRNode.source`: nodes in, relations out. Neither mutates the raw
ExtractedBlock/ExtractedTable data — only the fields each analyzer
declares in `owns_fields` (order_index; is_continuation_of), per the
wrap-don't-replace principle.
"""

from __future__ import annotations
import re
from typing import ClassVar, Optional

from ..ir.document import IRNode, IRRelation, RelationType, NodeKind
from .base import Analyzer

# Left as a plain name here so this module has no hard dependency on the
# import path; set it once at the call site or adjust the import above.
try:
    from ..extraction.base import BlockType
except ImportError:
    BlockType = None  # heading-detection veto degrades gracefully to "no heading found"



# =====================================================================
# Pass 2: detect_table_continuations
# =====================================================================

# Tunables — start here, calibrate against real documents.
_MAX_PAGE_GAP = 1                 # candidate B must be within this many pages of A
_MIN_CONFIDENCE_TO_EMIT = 0.5     # don't emit relations below this — caller can raise the bar further
_HEADING_VETO_CONFIDENCE_CAP = 0.15  # if a heading sits between A and B, cap confidence this low


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _header_texts(table_source) -> list[str]:
    return [_normalize(cell.content) for cell in table_source.header]


def _column_type_signature(table_source, sample_rows: int = 5) -> list[str]:
    """
    Cheap per-column "data type" fingerprint: for each column index,
    look at up to `sample_rows` and classify as 'numeric', 'date-like',
    or 'text'. Used to compare A's and B's columns structurally instead
    of relying only on header text (which continuation pages sometimes
    drop or reformat).
    """
    def classify(cell_text: str) -> str:
        t = cell_text.strip()
        if not t:
            return "empty"
        if re.fullmatch(r"[\d.,\-+%$]+", t):
            return "numeric"
        if re.fullmatch(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}", t):
            return "date"
        return "text"

    n_cols = len(table_source.header)
    signature = []
    for col in range(n_cols):
        votes: dict[str, int] = {}
        for row in table_source.rows[:sample_rows]:
            if col < len(row):
                cls = classify(row[col].content)
                votes[cls] = votes.get(cls, 0) + 1
        # majority vote per column, ignoring 'empty'
        votes.pop("empty", None)
        signature.append(max(votes, key=votes.get) if votes else "empty")
    return signature


def _bbox_horizontal_overlap(box_a, box_b) -> float:
    """IoU-style overlap of two bboxes' x-ranges only, 0.0-1.0."""
    left = max(box_a.x0, box_b.x0)
    right = min(box_a.x1, box_b.x1)
    if right <= left:
        return 0.0
    intersection = right - left
    union = max(box_a.x1, box_b.x1) - min(box_a.x0, box_b.x0)
    return intersection / union if union > 0 else 0.0


def _has_heading_between(
    a: IRNode, b: IRNode, all_nodes_by_order: list[IRNode]
) -> bool:
    """
    True if any HEADING block falls strictly between A and B in reading
    order. This is the strongest negative signal — a heading (e.g.
    "Table 4: Q3 Expenses") between two tables strongly implies B starts
    a new table, not a continuation of A.
    """
    if a.order_index is None or b.order_index is None:
        return False  # can't reason about "between" without order — Pass 1 must run first
    for n in all_nodes_by_order:
        if n.order_index is None:
            continue
        if a.order_index < n.order_index < b.order_index:
            if n.kind == NodeKind.BLOCK and BlockType is not None and getattr(n.source, "type", None) == BlockType.HEADING:
                return True
    return False


def _score_continuation(a: IRNode, b: IRNode, heading_between: bool) -> tuple[float, dict]:
    """
    Combine structural signals into one confidence score plus a
    signal-breakdown dict (stored in IRRelation.metadata for debugging —
    the schema keeps one score as the authoritative field, but nothing
    stops you from keeping the components around for tuning).
    """
    ta, tb = a.source, b.source
    signals: dict = {}

    # 1. Column count match (strong structural signal)
    col_count_a, col_count_b = len(ta.header), len(tb.header)
    if col_count_a == col_count_b:
        signals["column_count_match"] = 1.0
    elif abs(col_count_a - col_count_b) == 1:
        signals["column_count_match"] = 0.5  # e.g. dropped leading index column on continuation
    else:
        signals["column_count_match"] = 0.0

    # 2. Column content-type consistency
    sig_a = _column_type_signature(ta)
    sig_b = _column_type_signature(tb)
    compare_len = min(len(sig_a), len(sig_b))
    if compare_len > 0:
        matches = sum(1 for i in range(compare_len) if sig_a[i] == sig_b[i])
        signals["column_type_match"] = matches / compare_len
    else:
        signals["column_type_match"] = 0.0

    # 3. Header text similarity (token overlap — cheap stand-in for the
    #    embedding-based version noted in the design; swap in an embedding
    #    cosine-similarity call here if you want the more robust version)
    headers_a, headers_b = _header_texts(ta), _header_texts(tb)
    if headers_a and headers_b and compare_len > 0:
        pair_count = min(len(headers_a), len(headers_b))
        exact = sum(1 for i in range(pair_count) if headers_a[i] == headers_b[i])
        signals["header_text_match"] = exact / pair_count if pair_count else 0.0
    else:
        signals["header_text_match"] = 0.0

    # 4. Bbox horizontal alignment
    box_a, box_b = ta.bbox, tb.bbox
    signals["bbox_horizontal_overlap"] = (
        _bbox_horizontal_overlap(box_a, box_b) if box_a and box_b else 0.0
    )

    # --- combine ---
    # Weighted toward structural signals (column count/type) since those
    # are the most reliable indicator; header text and bbox are supporting
    # evidence, not requirements, since many providers vary on whether
    # they repeat headers or preserve exact table width on continuation
    # pages.
    score = (
        0.35 * signals["column_count_match"]
        + 0.30 * signals["column_type_match"]
        + 0.20 * signals["header_text_match"]
        + 0.15 * signals["bbox_horizontal_overlap"]
    )

    signals["heading_between"] = heading_between
    if heading_between:
        score = min(score, _HEADING_VETO_CONFIDENCE_CAP)

    return round(score, 3), signals


class TableContinuationAnalyzer(Analyzer):
    """
    Pass 2 — finds cross-page table fragments that are really one
    logical table, using ReadingOrderAnalyzer's ordering to reason about
    adjacency and "what's in between." See module-level scoring helpers
    (_score_continuation etc.) for the signal breakdown.
    """

    name: ClassVar[str] = "table_continuation"
    requires: ClassVar[tuple[str, ...]] = ("reading_order",)
    owns_fields: ClassVar[tuple[str, ...]] = ("is_continuation_of",)

    def run(self, nodes: list[IRNode], relations: list[IRRelation]) -> list[IRRelation]:
        """
        Returns TABLE_CONTINUATION relations for fragment pairs scoring
        at or above _MIN_CONFIDENCE_TO_EMIT. Also sets
        `is_continuation_of` on each target node as a convenience (see
        IRNode docstring — derived, not authoritative; the relation list
        remains the source of truth).

        Does not merge or mutate any table content — only links
        fragment ids.
        """
        table_nodes = sorted(
            (n for n in nodes if n.kind == NodeKind.TABLE),
            key=lambda n: n.order_index if n.order_index is not None else float("inf"),
        )
        all_nodes_ordered = sorted(
            nodes, key=lambda n: n.order_index if n.order_index is not None else float("inf")
        )

        # Group table nodes by page so we can find "last table on page P" /
        # "first table on page P+k" without rescanning every time.
        tables_by_page: dict[int, list[IRNode]] = {}
        for n in table_nodes:
            tables_by_page.setdefault(n.page, []).append(n)

        new_relations: list[IRRelation] = []

        for page_num, tables_on_page in tables_by_page.items():
            a = tables_on_page[-1]  # last table on this page (already order-sorted)

            for gap in range(1, _MAX_PAGE_GAP + 1):
                candidate_page = page_num + gap
                candidates = tables_by_page.get(candidate_page)
                if not candidates:
                    continue
                b = candidates[0]  # first table on the candidate page

                heading_between = _has_heading_between(a, b, all_nodes_ordered)
                confidence, signals = _score_continuation(a, b, heading_between)

                if confidence >= _MIN_CONFIDENCE_TO_EMIT:
                    new_relations.append(
                        IRRelation(
                            type=RelationType.TABLE_CONTINUATION,
                            source_id=a.id,
                            target_id=b.id,
                            confidence=confidence,
                            method="structural_signals",
                            metadata=signals,
                        )
                    )
                    b.is_continuation_of = a.id
                # Only try the nearest page that clears the threshold —
                # stop after the first hit per A.
                if confidence >= _MIN_CONFIDENCE_TO_EMIT:
                    break

        return new_relations