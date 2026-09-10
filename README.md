# docsem

**Layout-aware semantic document representation for Python.**

`docsem` is a document-semantic processing layer that converts physically extracted document content into a structured **Document Intermediate Representation (Document IR)**.

It is designed to sit between document extraction systems and downstream document-understanding applications.

```text
Document
   │
   ▼
Physical Extraction
   │
   │ text, bounding boxes, tables,
   │ page elements, layout information
   ▼
Component & Zone Construction
   │
   ▼
PageIR
   │
   ▼
Semantic Reconstruction
   │
   │ LLM / SLM / deterministic reasoning
   ▼
DocumentIR
   │
   ▼
Downstream Applications
```

## The Problem

Modern OCR and document-layout systems can extract document elements with high accuracy.

They can identify:

* Text
* Tables
* Images
* Headings
* Bounding boxes
* Reading order
* Page boundaries
* Other layout information

However, the output is still primarily a **physical representation** of the document.

A document may physically be extracted as:

```text
Page 1
├── Paragraph A
├── Paragraph B
└── Table Part 1

Page 2
├── Table Part 2
└── Paragraph C

Page 3
├── Table Part 3
└── Total
```

But the logical document may actually contain:

```text
Document
├── Paragraph A
├── Paragraph B
├── Table
│   ├── Header
│   ├── Row 1
│   ├── Row 2
│   └── Row 3
├── Paragraph C
└── Total
```

The extraction system has correctly identified the physical elements.

The missing step is **semantic reconstruction**.

`docsem` is intended to provide that intermediate layer.

---

## Three-Stage Architecture

### Stage 1 — Physical Extraction

The first stage is performed by a document extraction system.

Examples include OCR and document-layout systems that provide information such as:

```text
Page
├── text
├── bounding boxes
├── tables
├── images
├── headings
└── other layout elements
```

`docsem` does not require a single extraction engine.

The extracted representation can come from an existing pipeline or a supported adapter.

---

### Stage 2 — Components and Zones

The extracted physical elements are converted into `docsem` components.

A page is represented using three positional zones:

```text
PageIR
├── header
├── body
└── footer
```

The zones are primarily based on **layout and position**, rather than semantic interpretation.

Each zone contains an ordered collection of components.

```text
PageIR
│
├── Header
│   ├── Component
│   └── Component
│
├── Body
│   ├── Component
│   ├── TableComponent
│   └── Component
│
└── Footer
    └── Component
```

This stage establishes a normalized page-level representation.

---

### Stage 3 — Semantic Reconstruction

Multiple `PageIR` objects are then combined into a document-level `DocumentIR`.

This is where relationships that cannot be reliably determined from individual pages can be resolved.

Examples include:

#### Cross-page table continuation

```text
Page 1                 Page 2                 Page 3
Table A                Table B                Table C
   │                       │                       │
   └───────────────────────┴───────────────────────┘
                           │
                           ▼
                    One logical table
```

#### Repeated content

```text
Page 1: ACME Corp — Invoice #123
Page 2: ACME Corp — Invoice #123
Page 3: ACME Corp — Invoice #123
```

can become a single logical component with provenance indicating its physical occurrences.

#### Semantic relationships

Components can be evaluated to determine whether they represent:

* The same information
* A continuation of the same structure
* Related information
* Independent content

---

## AI as a Pluggable Reasoning Layer

AI is an important part of the semantic reconstruction stage, but `docsem` is **not tied to a specific LLM provider**.

The package is designed around a provider abstraction:

```text
                 SemanticModel
                      │
          ┌───────────┼───────────┐
          │           │           │
       OpenAI       Local SLM   Custom
          │           │         Model
          │           │           │
          └───────────┴───────────┘
                      │
                      ▼
             Semantic Decisions
```

This allows semantic reasoning to be performed using:

* External LLM APIs
* Locally running SLMs
* Custom models
* Hybrid deterministic + AI approaches

The core document IR remains independent of the model provider.

---

## Example

The intended high-level API is:

```python
from docsem import DocSem

docsem = DocSem(
    extractor=extractor,
    semantic_model=semantic_model,
)

document = docsem.process("invoice.pdf")
```

The resulting object is a `DocumentIR`:

```python
print(document.document_type)

for component in document.body.components:
    print(component)
```

Advanced users can also provide already-extracted document data:

```python
document = docsem.process(extracted_document)
```

This allows `docsem` to operate as a semantic layer on top of an existing document-extraction pipeline.

---

## Document IR

The final `DocumentIR` preserves both physical and semantic information.

For example:

```python
DocumentIR(
    page_count=3,
    document_type="invoice",
    ...
)
```

Components can retain information such as:

```text
Component
├── type
├── text
├── position
├── source references
├── duplicate count
└── semantic confidence
```

Tables additionally retain structured information such as:

```text
TableComponent
├── columns
├── header rows
├── cells
├── spans
└── contributing pages
```

The IR is designed to be machine-readable, validated, serializable, and suitable as an input to downstream systems.

---

## Why an Intermediate Representation?

Different document extraction systems produce different output schemas.

Downstream applications, meanwhile, need a consistent representation that can preserve:

* Layout
* Reading order
* Document structure
* Provenance
* Cross-page relationships
* Semantic relationships

`docsem` provides a boundary between physical extraction and downstream document understanding.

```text
              Physical World
                    │
                    ▼
             OCR / Layout
                 Parser
                    │
                    ▼
             ┌───────────┐
             │  docsem   │
             │    IR     │
             └─────┬─────┘
                   │
       ┌───────────┼───────────┐
       ▼           ▼           ▼
      RAG        Search     Analytics
```

---

## Design Principles

### Layout-aware

Physical information such as page position and bounding boxes should not be discarded simply because semantic processing is being performed.

### Provider-independent

Semantic reasoning should not require a particular LLM provider.

### Deterministic where possible

Layout-based operations should be handled deterministically where reliable rules exist.

AI should primarily be used for decisions that require semantic understanding.

### Provenance-preserving

When physical components are merged or deduplicated, their relationship to the source document should remain traceable.

### Structured and validated

Semantic decisions should produce structured data rather than unvalidated free-form model output.

### Composable

`docsem` should be usable with existing extraction pipelines as well as future extraction adapters.

---

## Installation

```bash
pip install docsem
```

Using `uv`:

```bash
uv add docsem
```

AI providers can be installed separately so that the core package remains lightweight.

---

## License

See `LICENSE` for the project license.
