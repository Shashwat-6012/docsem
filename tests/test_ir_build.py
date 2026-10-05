from docsem.extraction.base import (
    BlockType,
    BoundingBox,
    ExtractedBlock,
    ExtractedBlockList,
    ExtractedTable,
    ExtractedTableList,
    ExtractionResult,
)
from docsem.ir.build import IRBuilder


def test_builder_creates_ordered_nodes_and_resolves_source():
    block = ExtractedBlock(
        type=BlockType.TEXT,
        content="Introduction",
        id="block-1",
        bbox=BoundingBox(0.1, 0.1, 0.9, 0.2, page=1),
    )
    table = ExtractedTable(
        header=[],
        rows=[],
        id="table-1",
        bbox=BoundingBox(0.1, 0.4, 0.9, 0.8, page=1),
    )
    result = ExtractionResult(
        blocks=ExtractedBlockList([block]),
        tables=ExtractedTableList([table]),
        page_count=1,
    )

    document_ir = IRBuilder(analyzers=[]).build(result)

    assert document_ir.nodes.ids() == ["p1_b0", "p1_t0"]
    assert [node.order_index for node in document_ir.reading_order()] == [0, 1]
    assert document_ir.resolve("p1_b0") is block
    assert document_ir.resolve("p1_t0") is table
