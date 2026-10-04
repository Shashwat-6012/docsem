# ExtractionResult and DocumentIR

This document describes the extraction result and Document IR models as they are currently implemented. The extraction result is the provider-neutral record of what an extractor found. `DocumentIR` layers node identity, reading order, and relationships over that result; it is not a replacement content model.

The implementation is centered in `src/docsem/extraction/base.py`, `src/docsem/ir/document.py`, and `src/docsem/ir/build.py`. Analysis stages live in `src/docsem/analyze/`.

## Processing Flow

```text
document path
    |
    v
ExtractionInput -> BaseExtractor.extract() -> ExtractionResult
                                             | blocks, tables, geometry, metadata
                                             v
                                          IRBuilder
                                             |
                      nodes <- wrap eligible blocks and tables
                                             |
                 ReadingOrderAnalyzer assigns order_index
                                             |
               table analyzers repair tables / add relations
                                             v
                                         DocumentIR
```

The public `DocSem.process()` flow extracts first and then passes the result to `IRBuilder.build()`. The provider adapters are responsible for translating provider-specific responses into the shared extraction dataclasses.

## Extraction Model

### ExtractionInput

`ExtractionInput` is the request passed to an extractor:

| Field | Meaning |
| --- | --- |
| `file_path: Path` | Input document path. |
| `mime_type: str \| None` | Optional format hint. |
| `pages: list[int] \| None` | Optional page selection; page numbers are 1-indexed. |
| `options: dict[str, Any]` | Provider-specific options. |

Every provider implements `BaseExtractor.extract(input_data) -> ExtractionResult` and identifies itself with `provider_name`.

### ExtractionResult

`ExtractionResult` is the normalized output boundary. Its lists are wrapped as queryable list subclasses during initialization, including when a caller passes ordinary Python lists.

| Field | Meaning |
| --- | --- |
| `blocks` | `ExtractedBlockList` of non-tabular content. |
| `tables` | `ExtractedTableList` of tables. |
| `raw_text` | Provider's concatenated document text, when available. |
| `provider` | Provider name. |
| `page_count` | Number of pages processed/reported by the provider. |
| `warnings` | Provider or normalization warnings. |
| `raw_response` | Optional original provider payload, useful for diagnostics. |

`raw_text`, `raw_response`, confidence values, and arbitrary metadata remain extraction-level data; they are not copied onto IR nodes.

### Shared content and geometry

`BlockType` currently defines `TEXT`, `HEADING`, `IMAGE`, and `KEY_VALUE`. An `ExtractedBlock` holds a type, text content, a generated UUID string by default, optional confidence, optional bounding box, and provider metadata.

An `ExtractedTable` holds a header (`list[TableCell]`), ordered data rows (`list[list[TableCell]]`), an ID, an optional primary bounding box, `bbox_by_page`, and metadata. Each `TableCell` has content and optional confidence. The table model does not assign explicit row or column indices; list position supplies that order.

`BoundingBox` uses normalized page coordinates: `(x0, y0)` is the top-left and `(x1, y1)` the bottom-right, with the origin at the page's top-left. Coordinates are fractions of page width/height, while `page_width`, `page_height`, and `page_unit` preserve the original dimensions and unit when known. `page` is 1-indexed. Width and height are derived as `x1 - x0` and `y1 - y0`.

For a table represented by one provider object across multiple pages, `bbox` is the primary/first-page box and `bbox_by_page` stores the page-specific boxes. This is distinct from a provider that emits separate table objects for separate page fragments.

### Extraction list helpers

`ExtractedBlockList` provides `get(id)`, `headings()`, `paragraphs()`, and `by_type(type)`. `ExtractedTableList` provides `get(id)` and `multi_page()`, where multi-page means more than one entry in `bbox_by_page`.

## DocumentIR Model

`DocumentIR` contains three things:

1. `source`: the `ExtractionResult` that owns the extracted content.
2. `nodes`: `NodeList` wrappers for eligible source blocks and tables.
3. `relations`: `RelationList` edges between nodes, populated by analysis stages.

The IR is intentionally flat. There is no page tree, section tree, component hierarchy, or copied text field on a node. Page and reading-order information live on each node; the content is resolved through its source pointer. `section_id` exists as a future hook and is not assigned by the current pipeline.

### Nodes and source pointers

`NodeKind` distinguishes `BLOCK` from `TABLE`. Each `NodeSource` is a frozen pair of `type` and raw-object `id`. Each `Node` contains:

| Field | Meaning |
| --- | --- |
| `id` | IR identity, currently formatted as `p{page}_b{index}` or `p{page}_t{index}`. |
| `source` | `NodeSource` pointer to the raw block or table. |
| `page` | 1-indexed page assigned from the object's primary bounding box. |
| `order_index` | Optional document reading-order rank; unset before ordering analysis. |
| `section_id` | Optional future logical-section label; currently unset by built-in analyzers. |
| `metadata` | Scratch/cache space for analyzers, not authoritative content. |

`kind`, `is_block`, and `is_table` are convenience properties derived from `source.type`.

The builder walks blocks and tables separately. The list index is local to its source list, not a shared counter: block index 4 and table index 4 can both exist on the same page because their ID prefixes differ. The IDs are repeatable when rebuilding from the same ordered `ExtractionResult`; the raw extraction object IDs are separate identifiers.

**Eligibility rule:** the current builder skips any block or table whose primary `bbox` is `None`. It does not raise or create an unordered node for it. For a multi-page `ExtractedTable`, it creates one node using `table.bbox.page`; `bbox_by_page` does not create extra nodes. Therefore a provider that already returns one multi-page table object does not need continuation relations to link that object's pages.

### Resolving content

Nodes do not own or duplicate raw content. For a `Node` object, `DocumentIR.resolve(node)` looks up `node.source.id` in `source.blocks` or `source.tables`, returning the raw object or `None`. `raw(node_id)` is intended as an ID-based alias. See [Implementation gaps](#implementation-gaps) for the current string-ID limitation.

Example:

```python
from docsem.ir.build import IRBuilder

ir = IRBuilder().build(extraction_result)
for node in ir.reading_order():
    raw = ir.resolve(node)
    print(node.id, node.kind, node.page, node.order_index, raw)
```

## Reading Order

`ReadingOrderAnalyzer` is always placed first by `IRBuilder`. It mutates each node's `order_index` and emits no relation. The index is a single increasing integer across the document, not a per-page rank.

The current heuristic:

1. Groups nodes by page and processes pages in ascending page number.
2. Resolves each node's bounding box.
3. Tries to infer horizontal columns by sorting boxes by `x0` and splitting clusters across horizontal gaps greater than `0.04` page width.
4. Sorts within a page by column, then a `y0` bucket rounded at `0.01` page-height intervals, then `x0`. On pages treated as single-column, it sorts by y-bucket and then x-coordinate.
5. Assigns consecutive global indices in that order.

This is a layout heuristic, not a semantic section detector or a full reading-order engine. The builder normally ensures every node has a bounding box; the analyzer itself nevertheless places a missing-box node at the end of its page if one is supplied independently.

`DocumentIR.reading_order()` delegates to `nodes.ordered()`. `NodeList.ordered()` sorts by `(order_index, page)` and places nodes with no index last. `order_index` is the ordering representation; no reading-order edges are generated.

## Relations

A `Relation` is a directed edge with:

| Field | Meaning |
| --- | --- |
| `type` | `TABLE_CONTINUATION` or `DUPLICATE`. |
| `source_id` | Earlier node. |
| `target_id` | Later node. |
| `confidence` | One score from `0.0` to `1.0`; the dataclass does not enforce this range. |
| `method` | Optional detector/method label, such as `llm` or a `DuplicateMethod` value. |
| `metadata` | Optional relation-specific details. |

The direction contract is important: source is earlier and target is later. For `DUPLICATE`, the later target is the occurrence designated to drop. For `TABLE_CONTINUATION`, the target is the next fragment. Duplicate relations are intended to connect only nodes of the same kind (block-to-block or table-to-table), never block-to-table.

`DuplicateMethod` defines `NEAR_EXACT` and `SEMANTIC` as method labels on the shared `DUPLICATE` relation type. These values describe the design vocabulary; there is no built-in duplicate analyzer in the current pipeline.

### Table continuation analysis

`TableContinuationAnalyzer` is the implemented relation producer. It considers adjacent table nodes in reading order and keeps a pair as a candidate only when:

- the next table is on the immediately following page;
- every header/data row in each table has a consistent width; and
- both tables have the same non-empty column count.

The analyzer asks its LLM provider to judge candidate fragments using up to three trailing rows from the earlier table, up to three leading rows from the later table, headers, and short intervening block snippets. It does not merge the tables or their rows. If a provider is absent, it emits no continuation relations.

For a usable verdict, confidence is interpreted as the probability of continuation (inverting the model's confidence when it answers `is_continuation: false`). By default, relations below `emit_floor=0.15` are omitted. Emitted relations use `method="llm"` and include status (`likely`, `needs_review`, or `unlikely`), reason, column count, and header-presence flags in metadata. Status thresholds default to `0.8` and `0.4`; these are analyzer settings, not intrinsic relation semantics.

The `table_chain()` method is intended to follow continuation edges while applying a confidence floor and optional allowed-ID set. Its present implementation has a missing helper; see [Implementation gaps](#implementation-gaps).

### Duplicate relations

The relation schema and direction contract define how duplicate detection should be represented, but no duplicate analyzer currently emits these edges. No node is automatically deleted or marked as canonical by the current build pipeline.

## Query Helpers

`NodeList` preserves its custom type for filtered results, allowing chained queries:

```python
page_tables = ir.nodes.on_page(2).tables().ordered()
unplaced = ir.nodes.unordered()
source_node = ir.nodes.by_source(raw_table_id)
```

Available node operations are `get(id)`, `by_source(source_id, kind=None)`, `ids()`, `blocks()`, `tables()`, `by_kind(kind)`, `on_page(page)`, `on_pages(start, end)`, `in_section(section_id)`, `ordered()`, `unordered()`, `with_metadata(key)`, and `excluding(ids)`.

`RelationList` supports `by_type(type)`, `continuations()`, `duplicates()`, `involving(id)`, `from_node(id)`, `to_node(id)`, `above(min_confidence)`, `below(max_confidence)`, `by_method(method)`, `target_ids()`, `next_of(id)`, and `sorted_by_confidence(descending=True)`. `DocumentIR.relations_of(id, type=None)` returns all touching relations, optionally filtered by type.

`target_ids()` returns targets for every relation in the list, not just duplicates. On a duplicate-only relation list those targets are the proposed later copies to drop.

## Analyzer Pipeline and Mutation Boundary

`AnalyzerPipeline` executes analyzers sequentially and checks each analyzer's declared `requires` names against stages that have already run. An analyzer receives the full `DocumentIR`; it may update fields/data and add relations. `IRBuilder` always prepends `ReadingOrderAnalyzer`, removes any supplied analyzer named `reading_order`, and then runs the configured analyzers.

The default builder configures table structure repair followed by table continuation analysis. There is an important current behavior difference from the IR module's stated wrap-only principle: `TableStructureAnalyzer` can repair `source.tables` in place, updating header/row cells and recording an audit entry in each affected table's `metadata["structure_repair"]`. It first applies safe rules, and may use an LLM for remaining repairs. Thus the `ExtractionResult` is the content owner, but it is not guaranteed to remain byte-for-byte unchanged after a default IR build. Reading-order analysis changes node fields; continuation analysis adds relations and does not merge raw tables.

## Implementation Gaps

These are current code limitations, not intended data-model semantics:

- `DocumentIR.resolve(node)` works with a `Node`, but its string-ID branch calls `self.node(node_id)`, which is not implemented. Consequently `resolve("node-id")` and `raw("node-id")` currently fail instead of resolving or returning `None`.
- `table_chain()` calls `_continuations_by_source()`, which is not implemented. Calling it currently fails before traversing a chain.
- The `DocumentIR` docstring mentions derived views such as canonical nodes and chunking units, but those APIs are not implemented in this module. Likewise, there is no `canonical_nodes()` implementation.
- `Relation` does not validate endpoint existence, endpoint kinds, direction, confidence bounds, or duplicate/continuation-specific metadata. Those are contracts callers and analyzers must currently honor.
- `section_id` and `DuplicateMethod` are schema hooks/vocabulary only; built-in analyzers do not populate sections or duplicate relations.

## Source of Truth

The live definitions and behaviors documented here are in:

- `src/docsem/extraction/base.py` — extraction dataclasses and provider contract.
- `src/docsem/ir/document.py` — nodes, relations, collection helpers, and `DocumentIR` views.
- `src/docsem/ir/build.py` — extraction-to-node mapping and analyzer setup.
- `src/docsem/analyze/read.py` — reading-order heuristic.
- `src/docsem/analyze/table.py` — table repair and continuation relation analysis.
- `src/docsem/analyze/base.py` — analyzer contract and pipeline runner.