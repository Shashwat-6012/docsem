"""
reads `ExtractionResult.blocks` / `.tables` directly — everything downstream (analyzers, DocumentIR
methods) works purely in terms of IRNode/IRRelation. Keeping that
translation in one function means if your extraction schema changes
shape later, this is the only file that needs to change.
"""

from __future__ import annotations

import logging

from ..analyze.base import Analyzer, AnalyzerPipeline
from ..analyze.read import ReadingOrderAnalyzer
from ..analyze.table import TableContinuationAnalyzer, TableStructureAnalyzer
from ..extraction.base import ExtractionResult
from ..llm import create_provider
from .document import DocumentIR, Node, NodeKind, NodeList, NodeSource

logger = logging.getLogger(__name__)


class IRBuilder:
    def __init__(self, analyzers: list[Analyzer] | None = None):
        self.provider = create_provider("gemini")
        configured = (
            analyzers
            if analyzers is not None
            else [
                TableStructureAnalyzer(provider=self.provider),
                TableContinuationAnalyzer(provider=self.provider),
            ]
        )
        configured = [analyzer for analyzer in configured if analyzer.name != "reading_order"]
        self.pipeline = AnalyzerPipeline([ReadingOrderAnalyzer(), *configured])

    def build(self, result: ExtractionResult) -> DocumentIR:
        """Build nodes from extracted data and run every analyzer."""
        logger.debug(
            "building IR from %d blocks and %d tables", len(result.blocks), len(result.tables)
        )
        nodes = self._extraction_result_to_nodes(result)
        doc_ir = DocumentIR(source=result, nodes=NodeList(nodes))
        return self.pipeline.run(doc_ir)

    def _extraction_result_to_nodes(self, result: ExtractionResult) -> list[Node]:
        """
        Flattens result.blocks and result.tables into a single list of
        Nodes with stable, human-readable ids.

        ID SCHEME: "p{page}_b{index}" for blocks, "p{page}_t{index}" for
        tables, where `index` is the item's position within its own list
        (blocks list, tables list) — NOT a global counter. This keeps ids
        stable and re-derivable even if you rebuild the IR from the same
        ExtractionResult later (same input -> same ids), which matters if
        you ever persist relations separately from nodes and need to
        re-join them.

        Skips (with nothing raised) any block/table missing a usable bbox —
        ReadingOrderAnalyzer already tolerates missing bboxes gracefully,
        but a node with no page number at all can't even be grouped by
        page, so those are dropped here rather than silently mis-sorted.
        Track how often this actually happens in your real data; frequent
        drops likely mean the extraction step needs fixing upstream, not
        that this builder should get more lenient.
        """
        nodes: list[Node] = []

        for i, block in enumerate(result.blocks):
            if block.bbox is None:
                continue
            nodes.append(
                Node(
                    id=f"p{block.bbox.page}_b{i}",
                    source=NodeSource(type=NodeKind.BLOCK, id=block.id),
                    page=block.bbox.page,
                )
            )

        for i, table in enumerate(result.tables):
            # A table spanning multiple pages has bbox = first page's box
            # (per your ExtractedTable docstring) and bbox_by_page for the
            # rest. We still create exactly ONE IRNode per ExtractedTable
            # here — if your provider already tells you a table spans pages
            # 3-4 via bbox_by_page, that's a single table object, not two
            # fragments, so it doesn't need TableContinuationAnalyzer at
            # all. TableContinuationAnalyzer exists for the OTHER case: your
            # provider extracts page 3's table and page 4's table as two
            # separate ExtractedTable objects with no cross-page awareness
            if table.bbox is None:
                continue
            nodes.append(
                Node(
                    id=f"p{table.bbox.page}_t{i}",
                    source=NodeSource(type=NodeKind.TABLE, id=table.id),
                    page=table.bbox.page,
                )
            )

        return nodes
