"""
Reading-order analysis for DocumentIR construction.

ReadingOrderAnalyzer establishes the coordinate system (`order_index`)
that downstream analyzers such as TableContinuationAnalyzer and
DuplicateAnalyzer use to reason about node adjacency and document order.

It is a mandatory first pipeline step. It runs before the pluggable
analyzers and annotates the existing IRNode objects in place.

No relations are produced by this analyzer. `order_index` itself is the
canonical representation of reading order.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from ..ir.document import DocumentIR, Node
from .base import Analyzer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

# Two bounding boxes are considered to be on the same line when their
# y0 values fall within this fraction of the page height.
_Y_TOLERANCE_FRACTION = 0.01

# Minimum horizontal gap between clusters of content for them to be
# treated as separate columns.
_COLUMN_GAP_FRACTION = 0.04


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _bbox(
    document_ir: DocumentIR,
    node: Node,
):
    """Resolve a node's raw extraction object and return its bounding box."""
    source = document_ir.resolve(node)
    return source.bbox if source is not None else None


def _detect_columns(
    document_ir: DocumentIR,
    page_nodes: list[Node],
) -> list[tuple[float, float]] | None:
    """
    Best-effort column detection for one page.

    Returns:
        A list of `(x0, x1)` column ranges sorted left-to-right,
        or `None` when the page appears to be single-column.

    This is intentionally heuristic rather than a full layout parser.
    """

    boxes = sorted(
        (
            box
            for node in page_nodes
            if (box := _bbox(document_ir, node)) is not None
        ),
        key=lambda box: box.x0,
    )

    if len(boxes) < 2:
        return None

    # Group boxes into horizontal clusters. A sufficiently large gap
    # indicates a likely column gutter.
    clusters: list[list] = [[boxes[0]]]

    for box in boxes[1:]:
        previous_cluster = clusters[-1]

        previous_max_x1 = max(
            existing.x1
            for existing in previous_cluster
        )

        gap = box.x0 - previous_max_x1

        if gap > _COLUMN_GAP_FRACTION:
            clusters.append([box])
        else:
            previous_cluster.append(box)

    if len(clusters) < 2:
        return None

    # Remove clusters that are probably unrelated small elements.
    columns: list[tuple[float, float]] = []

    for cluster in clusters:
        x0 = min(box.x0 for box in cluster)
        x1 = max(box.x1 for box in cluster)

        y_span = (
            max(box.y1 for box in cluster)
            - min(box.y0 for box in cluster)
        )

        if len(cluster) > 1 or y_span > 0.2:
            columns.append((x0, x1))

    return columns if len(columns) >= 2 else None


def _sort_key_within_page(
    document_ir: DocumentIR,
    node: Node,
    columns: list[tuple[float, float]] | None,
):
    """
    Return the reading-order sort key for a node on a single page.

    Single-column:
        (column, y_bucket, x0)

    Multi-column:
        (column_index, y_bucket, x0)

    Nodes without a bounding box are deterministically placed at the
    end of the page.
    """

    box = _bbox(document_ir, node)

    if box is None:
        # Missing bbox -> last within the page.
        return (
            len(columns) if columns else 0,
            float("inf"),
            float("inf"),
        )

    y_bucket = round(
        box.y0 / _Y_TOLERANCE_FRACTION
    )

    if not columns:
        return (
            0,
            y_bucket,
            box.x0,
        )

    # Assign the node to the column whose left boundary is closest
    # to the node's x0.
    column_index = min(
        range(len(columns)),
        key=lambda i: abs(
            box.x0 - columns[i][0]
        ),
    )

    return (
        column_index,
        y_bucket,
        box.x0,
    )


# ---------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------

class ReadingOrderAnalyzer(Analyzer):
    """
    Mandatory first analyzer in the pipeline.

    Establishes `order_index` for every node in the DocumentIR.

    The analyzer:

    1. Groups nodes by page.
    2. Detects whether each page appears to contain columns.
    3. Sorts nodes according to their spatial reading order.
    4. Assigns a globally increasing `order_index`.
    5. Mutates the existing Node objects in place.
    6. Returns the same DocumentIR.

    No relations are produced.

    `order_index` is the canonical representation of document traversal:

        sorted(
            document_ir.nodes,
            key=lambda node: node.order_index,
        )

    is sufficient to reconstruct the reading sequence.
    """

    name: ClassVar[str] = "reading_order"

    owns_fields: ClassVar[tuple[str, ...]] = (
        "order_index",
    )

    def run(
        self,
        document_ir: DocumentIR
    ) -> DocumentIR:
        """
        Assign reading-order indices to all nodes.

        Nodes without a usable bounding box are placed deterministically
        at the end of their respective page rather than causing the
        pipeline to fail.
        """

        logger.debug("assigning reading order for %d nodes", len(document_ir.nodes))

        # -------------------------------------------------------------
        # Group nodes by page
        # -------------------------------------------------------------

        by_page: dict[int, list[Node]] = {}

        for node in document_ir.nodes:
            by_page.setdefault(
                node.page,
                [],
            ).append(node)

        # -------------------------------------------------------------
        # Establish spatial order page-by-page
        # -------------------------------------------------------------

        ordered: list[Node] = []

        for page_num in sorted(by_page):
            page_nodes = by_page[page_num]

            columns = _detect_columns(
                document_ir,
                page_nodes,
            )

            page_nodes_sorted = sorted(
                page_nodes,
                key=lambda node: _sort_key_within_page(
                    document_ir,
                    node,
                    columns,
                ),
            )

            ordered.extend(page_nodes_sorted)

        # -------------------------------------------------------------
        # Assign global reading-order indices
        # -------------------------------------------------------------

        for order_index, node in enumerate(ordered):
            node.order_index = order_index

        return document_ir