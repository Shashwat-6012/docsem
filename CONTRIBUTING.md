# Contributing

Thank you for your interest in contributing to this project.

This project aims to provide a modular document intelligence pipeline that separates **document extraction** from **document understanding and structuring**. Contributions are welcome across extraction, document representation, layout analysis, semantic grouping, chunking, testing, documentation, and integrations.

Please read this document before submitting a contribution.

---

## Table of Contents

* [Project Philosophy](#project-philosophy)
* [Architecture](#architecture)
* [Development Setup](#development-setup)
* [Repository Structure](#repository-structure)
* [Contribution Areas](#contribution-areas)
* [Adding an Extraction Provider](#adding-an-extraction-provider)
* [Adding Document Intelligence](#adding-document-intelligence)
* [Adding a Chunking Strategy](#adding-a-chunking-strategy)
* [Testing](#testing)
* [Code Quality](#code-quality)
* [Documentation](#documentation)
* [Commit Messages](#commit-messages)
* [Pull Requests](#pull-requests)
* [Reporting Issues](#reporting-issues)
* [Design and Architectural Changes](#design-and-architectural-changes)
* [Code of Conduct](#code-of-conduct)

---

## Project Philosophy

The project is built around a strict separation between two major stages:

```text
                    Document
                       │
                       ▼
             ┌───────────────────┐
             │ Extraction Layer  │
             └───────────────────┘
                       │
                       ▼
          Canonical Document Model
                       │
                       ▼
             ┌───────────────────┐
             │ Intelligence Layer│
             └───────────────────┘
                       │
                       ▼
            Logical Document Units
                       │
                       ▼
               Chunks / Output
```

### Extraction

The extraction layer is responsible for determining **what is physically present in a document**.

Examples include:

* text
* bounding boxes
* pages
* tables
* table cells
* images
* headers
* footers
* reading-order information
* document metadata

An extraction provider should report observations about the document without making unnecessary downstream semantic decisions.

### Document Intelligence

The intelligence layer is responsible for determining **how those extracted elements relate to one another**.

Examples include:

* identifying document types
* detecting sections
* grouping paragraphs
* associating headings with content
* reconstructing split tables
* connecting content across pages
* understanding layout relationships
* identifying logical regions
* producing document-type-aware chunks

This separation is one of the core architectural principles of the project.

> **Extract first. Interpret second.**

Contributions should preserve this separation unless there is a documented architectural reason to change it.

---

# Architecture

The project consists of several logical layers.

```text
Source Document
      │
      ▼
┌──────────────────────┐
│ Extraction Providers │
│ PDF / OCR / etc.     │
└──────────────────────┘
      │
      ▼
┌──────────────────────┐
│ Canonical Model      │
│ Document / Page /    │
│ Block / Table / ...  │
└──────────────────────┘
      │
      ▼
┌──────────────────────┐
│ Intelligence         │
│ Classification       │
│ Layout Analysis      │
│ Grouping             │
│ Reconstruction       │
└──────────────────────┘
      │
      ▼
┌──────────────────────┐
│ Chunking             │
│ Logical document     │
│ units                 │
└──────────────────────┘
      │
      ▼
Applications / Retrieval
```

## Architectural Boundaries

### Extraction providers must not own document intelligence

For example, an OCR provider may produce:

```text
Text:
"Revenue increased by 15%."

BoundingBox:
(x, y, width, height)

Page:
12
```

It should not decide:

```text
"This paragraph belongs to the Financial Performance section."
```

That decision belongs to the intelligence layer.

### Provider-specific representations should not leak into the core model

Provider-specific APIs and types should be adapted into the project's canonical representation.

Prefer:

```text
Provider
    ↓
Provider Adapter
    ↓
Canonical Model
```

over:

```text
Provider
    ↓
Provider-specific objects throughout the application
```

This allows extraction providers to be replaced without redesigning the rest of the system.

---

# Development Setup

Before contributing, make sure you can successfully build the project and run the existing test suite.

## Clone the repository

```bash
git clone <repository-url>
cd <repository-directory>
```

## Restore dependencies

Use the project's standard package/dependency restore command.

## Build

Build the complete project before making changes:

```bash
<build-command>
```

## Run tests

Run the complete test suite:

```bash
<test-command>
```

A contribution should not be submitted if it introduces failing tests unless the failure is intentional and clearly documented in the pull request.

> Replace the placeholder commands above with the project's actual commands once the build system is finalized.

---

# Repository Structure

The repository is organized around responsibilities rather than specific third-party technologies.

A typical structure is:

```text
src/
├── extraction/
│   ├── core/
│   ├── pdf/
│   ├── ocr/
│   └── ...
│
├── document/
│   ├── models/
│   └── normalization/
│
├── intelligence/
│   ├── classification/
│   ├── layout/
│   ├── grouping/
│   └── ...
│
├── chunking/
│
├── pipelines/
│
└── ...
```

Tests should mirror the architecture where practical:

```text
tests/
├── unit/
│   ├── extraction/
│   ├── document/
│   ├── intelligence/
│   └── chunking/
│
├── integration/
│
└── fixtures/
```

The exact structure may evolve, but new components should be placed according to their responsibility rather than the library or vendor they happen to use.

---

# Contribution Areas

Contributions are welcome in the following areas.

## Extraction

Examples:

* PDF extraction
* OCR integrations
* DOCX extraction
* HTML extraction
* image/document extraction
* table extraction
* metadata extraction
* reading-order extraction

## Canonical Document Model

Examples:

* document models
* page models
* text blocks
* bounding boxes
* tables
* table cells
* images
* metadata
* normalization

## Document Intelligence

Examples:

* document classification
* section detection
* layout analysis
* paragraph grouping
* heading detection
* table reconstruction
* cross-page relationships
* semantic grouping
* document-type-specific processing

## Chunking

Examples:

* layout-aware chunking
* semantic chunking
* document-type-aware chunking
* cross-page chunking
* table-aware chunking
* hierarchical chunking

## Infrastructure

Examples:

* pipelines
* configuration
* logging
* performance improvements
* caching
* diagnostics

## Documentation

Examples:

* API documentation
* architecture documentation
* tutorials
* examples
* usage guides
* benchmarks

---

# Adding an Extraction Provider

New extraction implementations should follow the project's extraction abstraction.

The general flow should be:

```text
External Provider
       │
       ▼
Provider Adapter
       │
       ▼
Canonical Extraction Model
```

For example:

```text
PaddleOCR
     │
     ▼
PaddleOCRExtractor
     │
     ▼
ExtractedDocument
```

The provider implementation should be responsible for translating provider-specific output into the canonical representation.

## An extractor should generally provide

* text
* page information
* bounding boxes
* tables where supported
* structural information available from the provider
* relevant metadata

## Avoid

Do not embed semantic chunking directly inside an extraction provider.

Avoid implementations such as:

```text
PaddleOCRExtractor
    ├── OCR
    ├── paragraph grouping
    ├── semantic classification
    └── chunk generation
```

Prefer:

```text
PaddleOCRExtractor
        │
        ▼
ExtractedDocument
        │
        ▼
Document Intelligence
        │
        ▼
Chunking
```

This allows the same intelligence pipeline to work with multiple extraction providers.

---

# Adding Document Intelligence

Document intelligence components operate on the canonical document representation.

Examples:

```text
ExtractedDocument
      │
      ▼
DocumentClassifier
      │
      ▼
LayoutAnalyzer
      │
      ▼
StructureDetector
      │
      ▼
Chunker
```

Intelligence components should ideally be:

* independently testable
* composable
* provider-independent
* deterministic where practical
* explicit about their inputs and outputs

If a component requires information that is not currently available in the canonical model, consider whether the model should be extended rather than accessing provider-specific objects directly.

---

# Adding a Chunking Strategy

Chunking is responsible for converting structured document information into meaningful logical units.

A chunking strategy may use:

* textual relationships
* spatial relationships
* document hierarchy
* page boundaries
* section boundaries
* tables
* document type
* semantic relationships
* cross-page relationships

For example:

```text
Page 1
 ├── Heading
 ├── Paragraph
 └── Paragraph

Page 2
 ├── Paragraph
 └── Table continuation

        │
        ▼

Logical Section
        │
        ▼

Chunk
```

## Chunking requirements

A new chunking strategy should clearly define:

### Input

What canonical document representation does it require?

### Processing

What relationships or rules does it use?

### Output

What constitutes a valid chunk?

### Failure behavior

What happens when extraction is incomplete, ambiguous, or malformed?

---

# Testing

Testing is required for behavioral changes.

## Unit Tests

Use unit tests for:

* individual extraction components
* model transformations
* grouping algorithms
* layout calculations
* classification logic
* chunking behavior

Unit tests should be fast and deterministic.

## Integration Tests

Use integration tests when multiple components interact.

Examples:

```text
PDF
 ↓
Extractor
 ↓
Canonical Model
 ↓
Intelligence
 ↓
Chunking
```

These tests are especially important for verifying that architectural boundaries work correctly.

---

# Document Fixtures

Document intelligence cannot be adequately tested using only artificial strings.

Where practical, use representative document fixtures.

Examples:

```text
tests/fixtures/
├── multi-column.pdf
├── multi-page-table.pdf
├── scanned-document.pdf
├── annual-report.pdf
├── invoice.pdf
└── mixed-layout.pdf
```

Important cases include:

* multi-column layouts
* split paragraphs
* split tables
* repeated headers
* repeated footers
* page breaks
* rotated pages
* irregular layouts
* scanned documents
* mixed text and tables

When adding a new document fixture, explain what behavior it is intended to test.

---

# Regression Tests

Bug fixes should generally include a regression test.

For example, if a multi-page table was previously split incorrectly:

```text
Before:

Chunk 1 → Table page 1
Chunk 2 → Table page 2

Expected:

Chunk 1 → Complete logical table
```

Add a fixture and test that prevents the behavior from returning.

---

# Code Quality

Contributions should follow the existing project's conventions.

In general:

* Keep components focused on a single responsibility.
* Prefer composition over unnecessary inheritance.
* Avoid unnecessary dependencies.
* Keep public APIs small.
* Avoid provider-specific types in core abstractions.
* Prefer clear names over clever implementations.
* Handle malformed or incomplete documents explicitly.
* Do not silently discard extracted information.
* Add tests for meaningful behavioral changes.
* Keep performance in mind when processing large documents.

## Performance

Document processing can involve large files and thousands of layout elements.

Avoid unnecessary:

* repeated document traversal
* string allocations
* copying of large collections
* expensive operations inside per-element loops
* repeated parsing of the same information

Performance-sensitive changes should include benchmarks when appropriate.

---

# Documentation

Documentation should be updated when a contribution changes user-visible behavior or public APIs.

Depending on the change, this may include:

```text
README.md
docs/
examples/
API documentation
CHANGELOG.md
```

Public APIs should be documented sufficiently for another developer to understand:

* what the component does
* what it accepts
* what it returns
* important constraints
* failure behavior
* relevant examples

---

# Commit Messages

Use clear and descriptive commit messages.

The project follows a Conventional Commit-style format:

```text
<type>(<scope>): <description>
```

Examples:

```text
feat(extraction): add PDF extractor
feat(chunking): add table-aware chunking
fix(layout): preserve reading order across columns
fix(extraction): normalize rotated page coordinates
refactor(document): simplify block representation
test(chunking): add cross-page table fixture
docs(architecture): document extraction boundary
perf(grouping): reduce layout traversal overhead
```

Common types include:

| Type       | Purpose                  |
| ---------- | ------------------------ |
| `feat`     | New functionality        |
| `fix`      | Bug fix                  |
| `refactor` | Internal restructuring   |
| `test`     | Tests                    |
| `docs`     | Documentation            |
| `perf`     | Performance improvement  |
| `build`    | Build/dependency changes |
| `ci`       | CI/CD changes            |
| `chore`    | Maintenance              |

Keep commits focused. Avoid combining unrelated changes into a single commit.

---

# Pull Requests

Before opening a pull request:

* [ ] The project builds successfully.
* [ ] Existing tests pass.
* [ ] New behavior has appropriate tests.
* [ ] Documentation has been updated where necessary.
* [ ] The change follows the project's architectural boundaries.
* [ ] No unnecessary dependencies were introduced.
* [ ] The commit history is reasonably clean.

## Pull Request Description

A pull request should explain:

### What changed?

Describe the implementation.

### Why?

Explain the problem being solved.

### Architectural impact

Explain where the change belongs in the architecture and why.

### Testing

Describe the tests that were added or modified.

### Examples

For user-visible behavior, provide an example where useful.

### Breaking Changes

Clearly identify any breaking API or behavioral changes.

A useful PR description looks like:

```text
## What changed?

Added cross-page table reconstruction to the document intelligence layer.

## Why?

Tables spanning multiple pages were previously treated as independent tables.

## Architectural impact

The extraction layer remains unchanged.
The grouping logic now identifies compatible table fragments
across page boundaries.

## Tests

Added a multi-page table fixture and integration test.

## Breaking changes

None.
```

---

# Reporting Issues

Before opening an issue, search existing issues to avoid duplicates.

A useful issue should include:

* a clear title
* project version/commit
* environment information where relevant
* reproduction steps
* expected behavior
* actual behavior
* relevant logs or error messages
* a minimal example or document fixture when possible

For document-processing bugs, providing a **minimal representative document** is particularly valuable.

If the document contains sensitive information, remove or anonymize it before submitting.

---

# Design and Architectural Changes

Changes to the core architecture require additional consideration.

Examples include:

* changing the canonical document model
* changing extractor interfaces
* moving responsibilities between extraction and intelligence
* introducing a new processing stage
* changing the chunk representation
* introducing a new external dependency into the core
* changing public APIs

For significant architectural changes, open an issue or design proposal before implementing the change.

The proposal should explain:

```text
Problem
   ↓
Current Behavior
   ↓
Proposed Design
   ↓
Alternatives Considered
   ↓
Trade-offs
   ↓
Migration / Compatibility
```

The goal is to avoid introducing architectural changes through an isolated pull request without considering their impact on the rest of the pipeline.

---

# Dependency Guidelines

Dependencies should be introduced only when they provide substantial value.

Before adding a dependency, consider:

* Is the functionality already available in the project?
* Is the dependency actively maintained?
* What is its license?
* Does it introduce unnecessary transitive dependencies?
* Does it increase deployment complexity?
* Does it belong in the core or only in an optional integration?
* Can the dependency be isolated behind an abstraction?

Provider-specific dependencies should generally remain inside their corresponding integration or adapter.

For example:

```text
core/
    No OCR-provider dependency

extraction/
    └── paddleocr/
          PaddleOCR dependency

extraction/
    └── azure/
          Azure dependency
```

rather than forcing every consumer of the core library to install every provider.

---

# Backward Compatibility

Public APIs should not be changed unnecessarily.

When a breaking change is required:

1. Explain why the change is necessary.
2. Document the old and new behavior.
3. Update affected tests.
4. Update documentation.
5. Add a changelog entry.
6. Clearly mark the change in the pull request.

When possible, prefer deprecation before removal.

---

# Security

Do not commit:

* API keys
* passwords
* access tokens
* private documents
* credentials
* production connection strings
* personally identifiable information

If you discover a security vulnerability, do not disclose sensitive details in a public issue. Use the project's designated private security-reporting mechanism.

---

# Code of Conduct

All contributors are expected to communicate respectfully and constructively.

Contributions should focus on improving the project and its users' experience.

Harassment, personal attacks, discrimination, or intentionally disruptive behavior are not acceptable.

If the project later adopts a formal Code of Conduct, this section should be replaced with a reference to that document.

---

# Final Principle

The most important architectural rule for contributors is:

> **Extraction tells us what is present. Intelligence determines what it means.**

Keep those responsibilities separate.

A new extraction provider should be able to feed the existing intelligence pipeline.

A new intelligence or chunking strategy should be able to operate independently of a particular extraction provider.

If a contribution preserves that property, it is likely aligned with the project's architecture.
