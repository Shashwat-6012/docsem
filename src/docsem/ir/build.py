"""
reads `ExtractionResult.blocks` / `.tables` directly — everything downstream (analyzers, DocumentIR
methods) works purely in terms of IRNode/IRRelation. Keeping that
translation in one function means if your extraction schema changes
shape later, this is the only file that needs to change.
"""

from __future__ import annotations

from .document import IRNode, NodeKind, DocumentIR
from ..analyze.base import AnalyzerPipeline
from ..analyze.read import ReadingOrderAnalyzer
from ..analyze.table import  TableContinuationAnalyzer


def _extraction_result_to_nodes(result) -> list[IRNode]:
    """
    Flattens result.blocks and result.tables into a single list of
    IRNodes with stable, human-readable ids.

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
    nodes: list[IRNode] = []

    for i, block in enumerate(result.blocks):
        if block.bbox is None:
            continue
        nodes.append(
            IRNode(
                id=f"p{block.bbox.page}_b{i}",
                kind=NodeKind.BLOCK,
                page=block.bbox.page,
                source=block,
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
        # separate ExtractedTable objects with no cross-page awareness,
        # which is the common failure mode you said not to trust.
        if table.bbox is None:
            continue
        nodes.append(
            IRNode(
                id=f"p{table.bbox.page}_t{i}",
                kind=NodeKind.TABLE,
                page=table.bbox.page,
                source=table,
            )
        )

    return nodes


def build_document_ir(result, analyzers: list | None = None) -> DocumentIR:
    """
    The main entry point. Takes one ExtractionResult (a full document —
    see note below if your pipeline instead gives you one result per
    page) and returns a fully-analyzed DocumentIR.

    `analyzers` defaults to [ReadingOrderAnalyzer(), TableContinuationAnalyzer()].
    Pass your own list once DuplicateAnalyzer exists, e.g.:
        build_document_ir(result, analyzers=[
            ReadingOrderAnalyzer(),
            TableContinuationAnalyzer(),
            DuplicateAnalyzer(),
        ])
    AnalyzerPipeline validates `requires` ordering for you regardless of
    what you pass.

    IF YOUR PIPELINE GIVES YOU ONE ExtractionResult PER PAGE instead of
    one for the whole document, combine them before calling this:

        combined = ExtractionResult(
            blocks=[b for r in page_results for b in r.blocks],
            tables=[t for r in page_results for t in r.tables],
            raw_text="\\n".join(r.raw_text for r in page_results),
            provider=page_results[0].provider,
            page_count=len(page_results),
        )
        doc_ir = build_document_ir(combined)

    This works as long as each page's blocks/tables already carry the
    correct absolute page number in their bbox (not always page 1) —
    if your per-page provider call resets page numbers to 1 each time,
    you'll need to re-stamp bbox.page with the true page number during
    that combine step, since everything downstream (analyzers, node
    ids, DocumentIR.reading_order) depends on page being correct and
    document-global.
    """
    nodes = _extraction_result_to_nodes(result)

    if analyzers is None:
        analyzers = [ReadingOrderAnalyzer(), TableContinuationAnalyzer()]

    pipeline = AnalyzerPipeline(analyzers)
    relations = pipeline.run(nodes)

    return DocumentIR(source=result, nodes=nodes, relations=relations)