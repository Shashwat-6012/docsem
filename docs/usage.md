# DOCSEM developer usage guide

This guide documents the public data model and current behavior of the `docsem` 0.1.x release. DOCSEM is in alpha, so pin the package version in applications that depend on its object schema.

## Public imports

The package does not currently re-export its SDK classes from the top-level `docsem` namespace. Import from the defining modules:

```python
from docsem.api import DocSem
from docsem.config import DocSemConfig, ExtractorConfig, ProviderName
from docsem.exceptions import DocSemError
from docsem.ir.document import DocumentIR, NodeKind, RelationType
```

## Select an extractor

`DocSem` takes a `DocSemConfig`; its `extraction` field contains an `ExtractorConfig`. The built-in factory currently supports Azure and PaddleOCR.

If `DocSem()` is constructed without a config, its default extraction provider is PaddleOCR. Since PaddleOCR is in the optional `paddle` extra, a plain `pip install docsem` user should either install `docsem[paddle]` or explicitly configure the Azure provider as shown below.

### Azure AI Document Intelligence

```python
import os

from docsem.api import DocSem
from docsem.config import DocSemConfig, ExtractorConfig, ProviderName

config = DocSemConfig(
    extraction=ExtractorConfig(
        provider=ProviderName.AZURE,
        options={
            "endpoint": os.environ["AZURE_ENDPOINT"],
            "api_key": os.environ["AZURE_API_KEY"],
            "model_id": "prebuilt-document",
        },
    )
)

document = DocSem(config).process("input.pdf")
```

`endpoint` and `api_key` are required. `model_id` is optional and defaults to `prebuilt-document`. Azure configuration is passed through the Python API; the SDK does not load a `.env` file automatically. The package's `python-dotenv` dependency can be used with an explicit `load_dotenv()` call in your application.

### PaddleOCR

Install the `paddle` extra before selecting this provider:

```bash
python -m pip install "docsem[paddle]"
```

```python
from docsem.api import DocSem
from docsem.config import DocSemConfig, ExtractorConfig, ProviderName

config = DocSemConfig(
    extraction=ExtractorConfig(
        provider=ProviderName.PADDLEOCR,
        options={"lang": "en", "use_gpu": False},
    )
)

document = DocSem(config).process("scan.pdf")
```

The Paddle options shown are optional; `lang` defaults to `"en"` and `use_gpu` defaults to `False`.

## Model-backed table analysis

The current `IRBuilder` creates a Gemini provider when it is initialized. The default analysis pipeline uses it for table-structure repair where deterministic repairs are insufficient and to judge candidate table continuations across pages. The model client reads credentials from the environment supported by the Google Gen AI SDK; `GEMINI_API_KEY` is a common configuration.

The model is not necessarily called for every document. For example, table repair is model-assisted only when the table structure needs repair, and continuation analysis requires candidate table fragments. If a request is made, provider failures may be logged and an individual analysis may remain unresolved; inspect the document and logs rather than assuming a model-derived result was produced.

There is not currently a public `DocSem` argument for replacing the builder's provider or analyzer pipeline. Installing the `llama` extra does not change the default builder's Gemini selection.

## The extraction data model

`document.source` is an `ExtractionResult`, the normalized result returned by the selected extraction provider:

- `blocks`: `ExtractedBlockList` with text-like elements.
- `tables`: `ExtractedTableList` with tabular content.
- `raw_text`: provider-level document text when available.
- `provider`: provider identifier, such as `"azure"` or `"paddleocr"`.
- `page_count`: reported number of pages.
- `warnings`: extraction warnings.
- `raw_response`: the provider's original response object, when retained by the adapter.

An `ExtractedBlock` has a `type`, `content`, identifier, optional confidence, optional bounding box, and provider metadata. The block type enum currently includes `text`, `heading`, `image`, and `key_value`.

An `ExtractedTable` keeps `header` as a list of header rows and `rows` as a list of data rows. Each row is a list of `TableCell` objects. It also has an identifier, optional bounding boxes, and metadata.

`BoundingBox.x0`, `y0`, `x1`, and `y1` are normalized coordinates from `0` to `1`, relative to the page dimensions. The origin is at the top-left. `page` is 1-indexed; `page_width`, `page_height`, and `page_unit` retain the source page dimensions and unit where available.

```python
for block in document.source.blocks:
    print(block.type, block.content, block.confidence)
    if block.bbox is not None:
        print(block.bbox.page, block.bbox.x0, block.bbox.y0)

for table in document.source.tables:
    headers = [[cell.content for cell in row] for row in table.header]
    data_rows = [[cell.content for cell in row] for row in table.rows]
    print(headers)
    print(data_rows)
```

## DocumentIR nodes and raw-content lookup

`DocumentIR` contains:

- `source`: the extraction result.
- `nodes`: `NodeList` with addressable references to extracted blocks and tables.
- `relations`: `RelationList` with relationships inferred by the configured analyzers.

A `Node` has an ID, a `NodeSource` (kind and raw object ID), a 1-indexed page, optional `order_index`, and optional `section_id`. Nodes do not copy the raw block or table content. Resolve the raw object through `DocumentIR.resolve()` or `DocumentIR.raw()`:

```python
for node in document.reading_order():
    item = document.resolve(node)
    if item is None:
        continue
    print(node.id, node.kind, node.page, node.order_index, item)
```

The builder creates nodes only for blocks and tables that have a bounding box. An extracted item without a bounding box remains available in `document.source` but has no DocumentIR node.

`NodeList` supports:

- Lookup: `get(node_id)`, `by_source(source_id, kind=None)`, and `ids()`.
- Kind filters: `blocks()`, `tables()`, and `by_kind(NodeKind.BLOCK | NodeKind.TABLE)`.
- Page filters: `on_page(page)` and inclusive `on_pages(start, end)`.
- Ordering: `ordered()` and `unordered()`.
- Other filters: `in_section(section_id)`, `with_metadata(key)`, and `excluding(ids)`.

`document.reading_order()` returns nodes sorted by their assigned reading-order index. Ordering is best-effort: it is derived from bounding boxes and uses a heuristic to identify page columns.

## Relations and table continuations

`Relation` records a relation type, source and target node IDs, a confidence score, an optional method, and metadata. The source is the earlier node; the target is the later node. Current relation types include:

- `RelationType.TABLE_CONTINUATION`: target is judged to be a subsequent fragment of the source table.
- `RelationType.DUPLICATE`: represented in the relation schema, but not emitted by the default analyzer pipeline in this release.

Inspect continuation candidates without assuming that they are certain:

```python
for relation in document.relations.continuations():
    print(
        relation.source_id,
        relation.target_id,
        relation.confidence,
        relation.metadata.get("status"),
        relation.metadata.get("reason"),
    )
```

`RelationList` provides `by_type()`, `continuations()`, `duplicates()`, `involving(node_id)`, `from_node(node_id)`, `to_node(node_id)`, `above(min_confidence)`, `below(max_confidence)`, `by_method(method)`, `target_ids()`, `next_of(node_id)`, and `sorted_by_confidence()`.

Use `DocumentIR.table_chain(start_id, min_confidence=..., allowed_ids=...)` to follow table-continuation edges from a starting node ID. It returns a list of node IDs; it does not concatenate or rewrite table rows. Table fragments remain independently addressable in the extraction result.

## Table structure repair

The default `TableStructureAnalyzer` checks for inconsistencies between table header and row widths. It can remove safe trailing placeholder header cells or empty trailing data cells deterministically. Remaining repairs can be requested from the configured model provider.

Repairs are applied to the table objects in `document.source.tables`. When a table needs repair, the analyzer records details under:

```python
table.metadata["structure_repair"]
```

That record can include the expected column count, original values, repair method, and unresolved rows. Retain the original source file if you need an immutable audit record, and validate repaired output for your domain.

## Error handling

`DocSemError` is the base exception for the package's documented exception hierarchy. Specific exception types are available from `docsem.exceptions`, including document, extraction, configuration, and structuring errors.

```python
from docsem.api import DocSem
from docsem.exceptions import DocSemError

try:
    document = DocSem().process("input.pdf")
except DocSemError:
    # Add application-specific logging, retries, or user-facing reporting.
    raise
```

`DocSem.process()` accepts a filesystem path as `str` or `pathlib.Path`. It validates that the path exists and is a file. Raw bytes and already-extracted result objects are not currently accepted by this method.

## Logging

DOCSEM installs a `NullHandler` and leaves logging configuration to the host application. To use its helper:

```python
import logging

from docsem.logging import enable_default_logging

enable_default_logging(logging.INFO)
```

Set the level to `logging.DEBUG` for more detailed diagnostics. Avoid logging API keys or other secrets in application-level configuration.

## Serialization

The extraction result can retain a provider-specific object in `raw_response`. As a result, recursively serializing the complete `DocumentIR` can produce provider-dependent output, large payloads, or objects that need custom conversion. The CLI currently serializes dataclasses to JSON using `default=str`; that is convenient for inspection but should not be treated as a stable interchange schema.

For application APIs and durable storage, explicitly construct a versioned payload containing only fields your application needs. For example:

```python
payload = {
    "provider": document.source.provider,
    "page_count": document.source.page_count,
    "blocks": [
        {
            "id": block.id,
            "type": str(block.type),
            "content": block.content,
            "page": block.bbox.page if block.bbox else None,
        }
        for block in document.source.blocks
    ],
    "relations": [
        {
            "type": str(relation.type),
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "confidence": relation.confidence,
        }
        for relation in document.relations
    ],
}
```

Version your own payload schema and decide explicitly whether it should include OCR text, metadata, coordinates, or provider-specific information.

## Custom extractors and extension boundaries

`BaseExtractor`, `ExtractionInput`, and `ExtractionResult` define the internal adapter contract under `docsem.extraction.base`. The built-in `build_extractor()` factory currently wires only the Azure and PaddleOCR providers. Although the common extraction types are useful for understanding the adapter boundary, `DocSem` does not currently expose a public constructor argument for injecting a custom extractor or an already-created `ExtractionResult`.

For this release, use a built-in provider through `ExtractorConfig`. Check the project release notes before building an integration that depends on internal factory or analyzer APIs.

## Current limitations

- Alpha API: pin versions and validate behavior on representative documents.
- Declared runtime support is Python 3.12 and 3.13.
- Provider-specific input formats, resource use, and model downloads depend on the selected extractor.
- The default builder creates Gemini-backed table analyzers internally; there is no public top-level setting to replace them in this release.
- Reading order and table continuation are best-effort analysis, not ground truth.
- Continuation edges do not merge rows. Duplicate relations are not currently produced by the default pipeline.
- Nodes require bounding boxes; extracted objects without them are omitted from the node list.
- The command-line interface currently checks Azure credentials but configures PaddleOCR. Use the Python API to select the intended extraction provider explicitly.
