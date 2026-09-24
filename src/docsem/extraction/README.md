# Extraction Module

The extraction layer is responsible for turning a raw document file into a provider-agnostic, layout-aware representation that higher-level document processing can consume.

It defines the extraction contract, the normalized output model, and the provider adapters that adapt external OCR/Document Intelligence services into a common internal format.

## Goals

- Keep extraction providers interchangeable
- Normalize document elements into a shared schema
- Preserve layout information such as page numbers and bounding boxes
- Expose text, headings, key-value items, and tables in a predictable structure
- Allow callers to build provider-specific extractors without knowing provider internals

## Package layout

- `base.py` — shared dataclasses and abstraction layer
- `factory.py` — provider factory + configuration objects
- `providers/azure.py` — Azure AI Document Intelligence adapter
- `providers/paddle.py` — PaddleOCR adapter placeholder / implementation target

## Core concepts

### ExtractionInput

`ExtractionInput` is the single request object sent to an extractor.

```python
from pathlib import Path
from docsem.extraction.base import ExtractionInput

payload = ExtractionInput(
    file_path=Path("report.pdf"),
    mime_type="application/pdf",
    pages=[1, 2, 3],
    options={
        "language": "en",
    },
)
```

Fields:

- `file_path`: input document path
- `mime_type`: optional MIME hint
- `pages`: optional page selection, using 1-indexed page numbers
- `options`: provider-specific flags and tuning parameters

### ExtractionResult

`ExtractionResult` contains the extracted output in a provider-neutral shape.

```python
from docsem.extraction.base import ExtractionResult
```

It includes:

- `blocks`: list of non-tabular content blocks
- `tables`: list of extracted tables
- `raw_text`: concatenated textual content
- `provider`: provider name used for extraction
- `page_count`: total number of processed pages
- `warnings`: warnings from the underlying provider
- `raw_response`: raw provider payload if needed for debugging

### BoundingBox and normalized coordinates

`BoundingBox` normalizes page geometry so output can be compared across providers.

Important semantics:

- coordinates are normalized to the range `[0, 1]`
- `x0`, `y0` define the top-left corner
- `x1`, `y1` define the bottom-right corner
- `page` is 1-indexed
- `page_width`, `page_height`, and `page_unit` preserve original page dimensions if available

This makes relative layout logic possible even when one provider reports inches and another reports pixels.

### ExtractedBlock

Non-tabular document content is represented as an `ExtractedBlock`.

```python
from docsem.extraction.base import ExtractedBlock, BlockType

block = ExtractedBlock(
    type=BlockType.TEXT,
    content="Quarterly performance improved materially.",
    confidence=0.97,
)
```

Supported block kinds include:

- `TEXT`
- `HEADING`
- `IMAGE`
- `KEY_VALUE`

### ExtractedTable

Tables are represented separately from blocks, keeping header and rows distinct and order-preserving.

```python
from docsem.extraction.base import ExtractedTable, TableCell

header = [TableCell(content="Name"), TableCell(content="Revenue")]
rows = [[TableCell(content="Alpha"), TableCell(content="120k")]]

table = ExtractedTable(header=header, rows=rows)
```

This object includes:

- `header`: list of column header cells
- `rows`: list of row lists
- `bbox`: primary bounding box for the table
- `bbox_by_page`: page-specific bounding boxes for multi-page tables
- `metadata`: additional provider-specific context

## Query helper methods

The extraction result wraps block and table collections in custom list subclasses so callers can query them without writing repetitive loops.

### `ExtractedBlockList`

`ExtractionResult.blocks` is an `ExtractedBlockList`, which adds a few convenience methods:

```python
from docsem.extraction.base import BlockType

headings = result.blocks.headings()
paragraphs = result.blocks.paragraphs()
key_value_blocks = result.blocks.by_type(BlockType.KEY_VALUE)

first_heading = result.blocks.get("block-id-here")
```

Available methods:

- `get(block_id: str)`: fetch one block by its generated ID
- `headings()`: return blocks whose type is a heading
- `paragraphs()`: return text blocks that are paragraph-like content
- `by_type(block_type)`: filter blocks by exact `BlockType`

Example:

```python
for heading in result.blocks.headings():
    print(heading.content)

for block in result.blocks.by_type(BlockType.TEXT):
    print(block.content)
```

### `ExtractedTableList`

`ExtractionResult.tables` is an `ExtractedTableList`, which adds commonly needed table operations:

```python
first_table = result.tables.get("table-id-here")
all_multi_page_tables = result.tables.multi_page()
```

Available methods:

- `get(table_id: str)`: fetch one table by its generated ID
- `multi_page()`: return only tables whose `bbox_by_page` spans more than one page

Example:

```python
for table in result.tables.multi_page():
    print("Table spans multiple pages:", table.id)
    print(table.bbox_by_page)

for table in result.tables:
    if table.header:
        print("Header:", [cell.content for cell in table.header])
```

These helpers are especially useful when you want to quickly inspect a document after extraction without manual filtering logic in each caller.

## Provider contract

All providers implement the abstract base class `BaseExtractor`.

```python
from docsem.extraction.base import BaseExtractor, ExtractionInput, ExtractionResult

class MyExtractor(BaseExtractor):
    provider_name = "my_provider"

    def extract(self, input_data: ExtractionInput) -> ExtractionResult:
        ...
```

The contract is intentionally simple:

- accept an `ExtractionInput`
- return an `ExtractionResult`
- expose the provider name via `provider_name`

## Factory and configuration

The extraction factory centralizes provider selection and instantiation.

```python
from docsem.extraction.factory import ExtractorConfig, ProviderName, build_extractor

config = ExtractorConfig(
    provider=ProviderName.AZURE,
    options={
        "endpoint": "https://example.cognitiveservices.azure.com/",
        "api_key": "...",
        "model_id": "prebuilt-document",
    },
)

extractor = build_extractor(config)
```

Supported providers today:

- `ProviderName.AZURE`
- `ProviderName.PADDLEOCR`

## Current provider implementations

### Azure extractor

The Azure adapter uses Azure AI Document Intelligence to extract text, tables, and key-value pairs.

It normalizes provider-specific coordinate systems and page dimensions into the common `BoundingBox` format.

### PaddleOCR extractor

The PaddleOCR adapter is defined in the package but is currently a placeholder implementation. The `BaseExtractor` contract remains the integration point for future OCR support.

## Typical end-to-end usage

```python
from pathlib import Path

from docsem.extraction.factory import ExtractorConfig, ProviderName, build_extractor
from docsem.extraction.base import ExtractionInput

extractor = build_extractor(
    ExtractorConfig(
        provider=ProviderName.AZURE,
        options={
            "endpoint": "https://example.cognitiveservices.azure.com/",
            "api_key": "<your-key>",
            "model_id": "prebuilt-document",
        },
    )
)

result = extractor.extract(
    ExtractionInput(file_path=Path("invoice.pdf"), pages=[1, 2])
)

print(result.provider)
print(result.page_count)
print(result.raw_text[:200])

for block in result.blocks:
    print(block.type, block.content)

for table in result.tables:
    print(table.header)
    print(table.rows)
```

## Notes for contributors

When adding a new provider:

1. Create a new extractor class inheriting from `BaseExtractor`
2. Implement `extract(self, input_data: ExtractionInput) -> ExtractionResult`
3. Convert provider-native output to the shared `ExtractedBlock`, `ExtractedTable`, and `BoundingBox` types
4. Register the new provider in `factory.py`
5. Document provider-specific `options` and any behavior differences

The goal is to keep the extraction layer consistent, inspectable, and provider-independent across the rest of the project.
