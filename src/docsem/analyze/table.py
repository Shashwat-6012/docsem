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
import logging
from typing import ClassVar

from ..extraction.base import ExtractionResult
from ..ir.document import DocumentIR, Relation, RelationType
from .base import Analyzer

logger = logging.getLogger(__name__)

class TableContinuationAnalyzer(Analyzer):
    """
    Placeholder implementation: link the last table on one page to the
    first table on the next page. Replace this heuristic with real scoring
    when table-continuation behavior is implemented.
    """

    name: ClassVar[str] = "table_continuation"
    requires: ClassVar[tuple[str, ...]] = ("reading_order",)

    def run(
        self,
        document_ir: DocumentIR
    ) -> DocumentIR:
        """
        This intentionally small dummy implementation adds at most one
        continuation relation per consecutive-page boundary.
        """
        table_nodes = document_ir.nodes.tables().ordered()
        logger.debug("checking %d table nodes for continuation", len(table_nodes))
        previous = None
        for node in table_nodes:
            if previous is not None and node.page == previous.page + 1:
                document_ir.relations.append(
                    Relation(
                        type=RelationType.TABLE_CONTINUATION,
                        source_id=previous.id,
                        target_id=node.id,
                        confidence=0.5,
                        method="dummy_table_continuation",
                    )
                )
            previous = node
        return document_ir