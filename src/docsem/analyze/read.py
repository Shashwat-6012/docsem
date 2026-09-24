"""
Analyzers for DocumentIR construction: ReadingOrderAnalyzer and
TableContinuationAnalyzer.

ReadingOrderAnalyzer establishes the coordinate system (order_index)
that TableContinuationAnalyzer and DuplicateAnalyzer both express their
logic in. TableContinuationAnalyzer finds cross-page table fragments
that are really one logical table, using that ordering to reason about
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

# BlockType lives in your shared extraction-types module (see document_ir.py's
# top-of-file import comment). Import it from wherever that module actually is:
#     from extraction_types import BlockType
# Left as a plain name here so this module has no hard dependency on the
# import path; set it once at the call site or adjust the import above.
try:
    from ..extraction.base import BlockType  # your actual module name
except ImportError:
    BlockType = None  # heading-detection veto degrades gracefully to "no heading found"


# =====================================================================
# Pass 1: compute_reading_order
# =====================================================================

# Two bboxes are considered "same line" if their y0 values fall within
# this fraction of page height of each other. Tune per your documents;
# scanned/OCR'd pages tend to need a slightly larger tolerance than
# clean digital-PDF text layers.
_Y_TOLERANCE_FRACTION = 0.01

# Minimum horizontal gap (as a fraction of page width) between two
# clusters of content for them to be treated as separate columns.
_COLUMN_GAP_FRACTION = 0.04


def _bbox(node: IRNode):
    """Every source type (ExtractedBlock, ExtractedTable) carries `.bbox`."""
    return node.source.bbox


def _detect_columns(page_nodes: list[IRNode]) -> Optional[list[tuple[float, float]]]:
    """
    Best-effort column detection for one page. Returns a list of
    (x0, x1) column ranges sorted left-to-right, or None if the page
    looks single-column (don't force column logic where it isn't needed).

    Heuristic: sort nodes by x0, then walk them looking for a gap between
    one node's x1 and the next node's x0 that's wide enough to be a
    column gutter rather than normal inter-word/inter-block spacing.
    This is intentionally simple — it will misfire on unusual layouts
    (rotated text, sidebars, pull-quotes). Treat it as a heuristic to
    refine once you see real failures, not a robust layout parser.
    """
    boxes = sorted((_bbox(n) for n in page_nodes if _bbox(n) is not None), key=lambda b: b.x0)
    if len(boxes) < 2:
        return None

    # Merge boxes into horizontal-range clusters, splitting where the gap
    # between clusters exceeds the column-gap threshold.
    clusters: list[list] = [[boxes[0]]]
    for b in boxes[1:]:
        prev_cluster = clusters[-1]
        prev_max_x1 = max(x.x1 for x in prev_cluster)
        gap = b.x0 - prev_max_x1
        if gap > _COLUMN_GAP_FRACTION:
            clusters.append([b])
        else:
            prev_cluster.append(b)

    if len(clusters) < 2:
        return None  # single column — nothing to do

    # Require the "columns" to each span a meaningful vertical range and
    # not just be two unrelated small elements (e.g. a page number next
    # to a stray mark). Cheap sanity check: each cluster should contain
    # more than one box, OR span a reasonable fraction of page height.
    ranges = []
    for cluster in clusters:
        x0 = min(x.x0 for x in cluster)
        x1 = max(x.x1 for x in cluster)
        y_span = max(x.y1 for x in cluster) - min(x.y0 for x in cluster)
        if len(cluster) > 1 or y_span > 0.2:
            ranges.append((x0, x1))

    return ranges if len(ranges) >= 2 else None


def _sort_key_within_page(node: IRNode, columns: Optional[list[tuple[float, float]]]):
    """
    Sort key for a node within a single page.

    Single-column: (y0-bucket, x0) — top-to-bottom, tie-break left-to-right.
    Multi-column:  (column_index, y0-bucket, x0) — walk column 0 fully
                   top-to-bottom, then column 1, etc. This is the standard
                   "read left column, then right column" convention; if
                   your documents interleave differently, this is the
                   function to change.
    """
    box = _bbox(node)
    if box is None:
        # No bbox (shouldn't normally happen) — push to the end of the
        # page deterministically rather than crashing or sorting randomly.
        return (len(columns) if columns else 0, float("inf"), float("inf"))

    y_bucket = round(box.y0 / _Y_TOLERANCE_FRACTION)

    if not columns:
        return (0, y_bucket, box.x0)

    # Assign this box to whichever column range its x0 falls closest to.
    col_index = min(
        range(len(columns)),
        key=lambda i: abs(box.x0 - columns[i][0]),
    )
    return (col_index, y_bucket, box.x0)


class ReadingOrderAnalyzer(Analyzer):
    """
    Pass 1 — establishes the coordinate system (order_index) that
    TableContinuationAnalyzer and DuplicateAnalyzer both express their
    logic in. See module docstring for why this must run first.

    Produces NO relations. order_index is a pure annotation on each
    node — bbox + order_index is already a complete, reconstruction-
    ready representation of position, so a parallel edge-list encoding
    of the same sequence (e.g. a READING_ORDER_NEXT relation per
    consecutive pair) would just be a second, more expensive copy of
    information the field already holds. sorted(nodes, key=lambda n:
    n.order_index) IS the traversal; no graph walk needed.
    """

    name: ClassVar[str] = "reading_order"
    requires: ClassVar[tuple[str, ...]] = ()
    owns_fields: ClassVar[tuple[str, ...]] = ("order_index",)

    def run(self, nodes: list[IRNode], relations: list[IRRelation]) -> list[IRRelation]:
        """
        Assigns `order_index` on each node IN PLACE (mutates the IRNode
        objects you pass in — these are your IR's own node objects, not
        raw source data, so this is consistent with wrap-don't-replace:
        we're annotating the address, not the content).

        Nodes without a usable bbox are ordered last within their page,
        deterministically, rather than raising — malformed/missing bbox
        data from the provider shouldn't crash the whole pipeline.

        Returns an empty list — see class docstring for why this
        analyzer emits no relations.
        """
        by_page: dict[int, list[IRNode]] = {}
        for n in nodes:
            by_page.setdefault(n.page, []).append(n)

        ordered: list[IRNode] = []
        for page_num in sorted(by_page.keys()):
            page_nodes = by_page[page_num]
            columns = _detect_columns(page_nodes)
            page_nodes_sorted = sorted(
                page_nodes,
                key=lambda n: _sort_key_within_page(n, columns),
            )
            ordered.extend(page_nodes_sorted)

        for i, node in enumerate(ordered):
            node.order_index = i

        return []