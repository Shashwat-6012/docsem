# Design Document: Document IR Module (v2 — Three-Zone Model)

**Component:** `document_ir` (Python package)
**Scope:** The IR data model only — `PageIR`, `DocumentIR`, and `Component` schema. The layout extraction step (already built) and the merge/classification pipeline (separate doc) are out of scope here.

**Change from v1:** the earlier design used a semantic tree (`Document → Section → Paragraph/Table`, with a shared `IRNodeBase` and multiple structural subclasses). This version replaces that with a flat, position-first model: every page is just three zones (`header`, `body`, `footer`), each holding an ordered list of components. No semantic tree, no base-class hierarchy, no inferred nesting. This is simpler to build, simpler to reason about, and maps directly onto what a layout parser actually gives you — top of page, middle, bottom.

---

## 1. The core idea

Every page of a document, regardless of what it contains, is divided into exactly three zones **by position, not by meaning**:

- **`header`** — whatever sits at the top of the page. For an invoice this is typically a logo + company info block. For a plain text document it might be empty. Calling it "header" does **not** mean "the document's semantic header" — it's purely "top-of-page content."
- **`body`** — the main content in the middle of the page.
- **`footer`** — whatever sits at the bottom (page numbers, disclaimers, "thank you" notes).

Each zone is just a flat, ordered list of **components** — no nesting, no sections-within-sections. A component is one of:

- `Component` (generic) — used directly for text blocks, logos, images, lists. Distinguished only by a `type` tag.
- `TableComponent` (a variant) — same base identity/position/order fields as `Component`, but with the extra structured fields a table actually needs (rows, columns, cells, spans). It gets its own class because "rows and cells" genuinely doesn't fit into a generic text component — everything else does.

The pipeline runs in two conceptual stages, matching your two-step design:

1. **Per page:** the physical layout output for one page is converted into a `PageIR` — header/body/footer zones populated with components, in reading order, using simple x/y layout info to decide zone placement and ordering. No cross-page reasoning happens here.
2. **Across pages:** all of a document's `PageIR` objects are combined into one `DocumentIR` with the same three-zone shape, but document-wide instead of per-page:
   - Components in `header`/`footer` that repeat across pages (same logo, same running header text) are **deduplicated** into one component, with a count of how many pages it appeared on.
   - Components that don't repeat are **concatenated** into the zone in order.
   - Tables in `body` that an LLM judges to be continuations of each other are **merged** into one `TableComponent` spanning all contributing pages.
   - The per-page `PageIR` objects are then **discarded** — `DocumentIR` is the final, standalone artifact. Nothing about the original per-page breakdown is kept once the merge is done.

---

## 2. Data model

### 2.1 Supporting types

```python
from __future__ import annotations

from enum import Enum
from typing import Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ComponentType(str, Enum):
    SECTION = "section"       # generic text block: paragraph, heading, caption, note
    LOGO = "logo"
    IMAGE = "image"
    TABLE = "table"
    LIST = "list"


class BBox(BaseModel):
    """Simple x/y layout box, in the page's own coordinate space (whatever
    the layout parser uses — top-left origin, points or pixels, etc).
    This model doesn't redefine that convention, just carries it through."""
    x: float
    y: float
    width: float
    height: float


class SourceRef(BaseModel):
    """Pointer back to the physical element a component came from.
    Deliberately minimal — just enough to trace provenance without
    assuming the layout parser's internal schema."""
    page_number: int = Field(..., ge=1)
    element_id: str
```

### 2.2 Component (generic — used for everything except tables)

```python
class Component(BaseModel):
    """One visual/content element inside a zone. Used directly for
    SECTION, LOGO, IMAGE, and LIST — distinguished only by `type`.
    No subclass per type; the shape is identical across all of them."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: ComponentType
    order: int = Field(..., description="Reading-order index within this zone.")
    position: Optional[BBox] = Field(
        default=None, description="Layout position, if available from the layout parser."
    )
    text: Optional[str] = Field(default=None, description="Text content, where applicable.")

    # Populated during the merge stage; left at defaults on a fresh per-page component.
    source_refs: list[SourceRef] = Field(default_factory=list)
    duplicate_count: int = Field(
        default=1, description="Physical occurrences collapsed into this one component. 1 = no dedup occurred."
    )
    merge_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence score if this component resulted from an LLM merge/dedup decision. None for pass-through components that needed no decision.",
    )
```

### 2.3 TableComponent (the one variant with real structure)

```python
class TableCell(BaseModel):
    row: int = Field(..., ge=0)
    col: int = Field(..., ge=0)
    text: str = ""
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    is_header: bool = False


class TableComponent(BaseModel):
    """Table variant of Component. Carries the same base identity/
    position/order/provenance fields, plus grid structure. Kept as a
    separate class rather than forcing rows/cells into Component's
    generic `text` field, since that would need ad-hoc parsing on
    every read instead of a validated structure."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: ComponentType = Field(default=ComponentType.TABLE, frozen=True)
    order: int
    position: Optional[BBox] = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    duplicate_count: int = Field(default=1)
    merge_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    column_count: int = Field(..., ge=0)
    header_rows: list[int] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)
    merged_from_pages: list[int] = Field(
        default_factory=list,
        description="Page numbers this table was stitched from, in order. Empty or single value means no cross-page merge happened.",
    )

    @model_validator(mode="after")
    def _validate_grid(self) -> "TableComponent":
        for cell in self.cells:
            if cell.col >= self.column_count:
                raise ValueError(
                    f"cell at row={cell.row} col={cell.col} exceeds declared column_count={self.column_count}"
                )
        return self


AnyComponent = Union[Component, TableComponent]
```

### 2.4 Zone, PageIR, DocumentIR

```python
class Zone(BaseModel):
    """One of the three positional zones. A flat, ordered list of
    components — no further nesting."""
    components: list[AnyComponent] = Field(default_factory=list)


class PageIR(BaseModel):
    """IR for a single physical page, before any cross-page merge.
    Purely positional: header = top of page, body = middle, footer =
    bottom. Makes no semantic claim about the content — that only
    happens when pages are combined into a DocumentIR."""
    page_number: int = Field(..., ge=1)
    header: Zone = Field(default_factory=Zone)
    body: Zone = Field(default_factory=Zone)
    footer: Zone = Field(default_factory=Zone)


class DocumentIR(BaseModel):
    """Final, merged IR for the whole logical document. Same three-zone
    shape as PageIR, but document-wide: duplicate header/footer content
    is collapsed, non-duplicate content is concatenated in order, and
    tables judged to continue each other are stitched into one
    TableComponent. This is the standalone artifact — per-page PageIR
    objects are discarded once this is built."""
    page_count: int = Field(..., ge=1)
    title: Optional[str] = None
    document_type: Optional[str] = None
    header: Zone = Field(default_factory=Zone)
    body: Zone = Field(default_factory=Zone)
    footer: Zone = Field(default_factory=Zone)

    def iter_components(self):
        """Flat traversal across all three zones, in header/body/footer
        order — useful for consumers that just want every component
        once without caring which zone it's in."""
        for zone in (self.header, self.body, self.footer):
            for c in zone.components:
                yield c
```

That's the entire model. Four real classes (`Component`, `TableComponent`, `Zone`, plus `PageIR`/`DocumentIR` sharing the same zone shape), two small supporting types (`BBox`, `SourceRef`), one enum. No base class shared between `Component` and `TableComponent` — they're independent classes that happen to carry a similar identity/position/order shape, joined only by the `AnyComponent` union type used inside `Zone`.

---

## 3. Worked example: 3-page invoice, table split across all 3 pages

**Physical layout (page 1):** logo top-left, company name/invoice number top-right → header zone. Bill-to block, then a table (header row + 1 line item) → body zone. Nothing at the bottom.

**Page 2:** same logo + company name repeated at top → header zone. Just table rows (no header row repeated) → body zone. Nothing at bottom.

**Page 3:** same logo + company name repeated → header zone. Final table row, then subtotal/tax/total block → body zone. "Thank you for your business" → footer zone.

### Step 1 output — three separate `PageIR` objects

```python
page_1 = PageIR(
    page_number=1,
    header=Zone(components=[
        Component(type=ComponentType.LOGO, order=0),
        Component(type=ComponentType.SECTION, order=1, text="ACME Corp — Invoice #INV-2026-0042"),
    ]),
    body=Zone(components=[
        Component(type=ComponentType.SECTION, order=0, text="Bill To: Example Customer"),
        TableComponent(order=1, column_count=4, header_rows=[0], cells=[
            TableCell(row=0, col=0, text="Item", is_header=True),
            TableCell(row=0, col=1, text="Qty", is_header=True),
            TableCell(row=0, col=2, text="Unit Price", is_header=True),
            TableCell(row=0, col=3, text="Total", is_header=True),
            TableCell(row=1, col=0, text="Widget A"),
            TableCell(row=1, col=1, text="2"),
            TableCell(row=1, col=2, text="10.00"),
            TableCell(row=1, col=3, text="20.00"),
        ]),
    ]),
    footer=Zone(),  # empty
)

page_2 = PageIR(
    page_number=2,
    header=Zone(components=[
        Component(type=ComponentType.LOGO, order=0),
        Component(type=ComponentType.SECTION, order=1, text="ACME Corp — Invoice #INV-2026-0042"),
    ]),
    body=Zone(components=[
        TableComponent(order=0, column_count=4, cells=[
            TableCell(row=0, col=0, text="Widget B"),
            TableCell(row=0, col=1, text="1"),
            TableCell(row=0, col=2, text="15.00"),
            TableCell(row=0, col=3, text="15.00"),
        ]),
    ]),
    footer=Zone(),
)

page_3 = PageIR(
    page_number=3,
    header=Zone(components=[
        Component(type=ComponentType.LOGO, order=0),
        Component(type=ComponentType.SECTION, order=1, text="ACME Corp — Invoice #INV-2026-0042"),
    ]),
    body=Zone(components=[
        TableComponent(order=0, column_count=4, cells=[
            TableCell(row=0, col=0, text="Widget C"),
            TableCell(row=0, col=1, text="3"),
            TableCell(row=0, col=2, text="5.00"),
            TableCell(row=0, col=3, text="15.00"),
        ]),
        Component(type=ComponentType.SECTION, order=1, text="Subtotal: 50.00 | Tax: 5.00 | Total: 55.00"),
    ]),
    footer=Zone(components=[
        Component(type=ComponentType.SECTION, order=0, text="Thank you for your business."),
    ]),
)
```

Nine components across three pages, no relationship between them yet.

### Step 2 (pipeline, separate doc) — what it decides

- **Header zone, across all 3 pages:** the `LOGO` component and the `SECTION` text component are identical (or near-identical) on every page → both get collapsed to one component each, with `duplicate_count=3` and `source_refs` listing all three pages.
- **Body zone:** the three `TableComponent`s share the same 4-column structure, and there's no repeated header row on pages 2–3, with item text/numbers reading as a continuous list → merge into **one** `TableComponent` with all rows renumbered into a single grid, `merged_from_pages=[1, 2, 3]`, and a `merge_confidence` score. The two `SECTION` components (bill-to, totals) aren't duplicates of anything, so they pass through unchanged, just placed in document-wide order.
- **Footer zone:** only page 3 had footer content, so it passes straight through with no merge decision needed.

### Resulting `DocumentIR` (the final, standalone artifact)

```python
document = DocumentIR(
    page_count=3,
    title="Invoice INV-2026-0042",
    document_type="invoice",
    header=Zone(components=[
        Component(
            type=ComponentType.LOGO, order=0, duplicate_count=3,
            source_refs=[SourceRef(page_number=p, element_id="logo") for p in (1, 2, 3)],
        ),
        Component(
            type=ComponentType.SECTION, order=1,
            text="ACME Corp — Invoice #INV-2026-0042",
            duplicate_count=3, merge_confidence=0.98,
            source_refs=[SourceRef(page_number=p, element_id="header-text") for p in (1, 2, 3)],
        ),
    ]),
    body=Zone(components=[
        Component(type=ComponentType.SECTION, order=0, text="Bill To: Example Customer"),
        TableComponent(
            order=1, column_count=4, header_rows=[0],
            merge_confidence=0.90, merged_from_pages=[1, 2, 3],
            cells=[
                TableCell(row=0, col=0, text="Item", is_header=True),
                TableCell(row=0, col=1, text="Qty", is_header=True),
                TableCell(row=0, col=2, text="Unit Price", is_header=True),
                TableCell(row=0, col=3, text="Total", is_header=True),
                TableCell(row=1, col=0, text="Widget A"), TableCell(row=1, col=1, text="2"),
                TableCell(row=1, col=2, text="10.00"),   TableCell(row=1, col=3, text="20.00"),
                TableCell(row=2, col=0, text="Widget B"), TableCell(row=2, col=1, text="1"),
                TableCell(row=2, col=2, text="15.00"),   TableCell(row=2, col=3, text="15.00"),
                TableCell(row=3, col=0, text="Widget C"), TableCell(row=3, col=1, text="3"),
                TableCell(row=3, col=2, text="5.00"),    TableCell(row=3, col=3, text="15.00"),
            ],
        ),
        Component(type=ComponentType.SECTION, order=2, text="Subtotal: 50.00 | Tax: 5.00 | Total: 55.00"),
    ]),
    footer=Zone(components=[
        Component(type=ComponentType.SECTION, order=0, text="Thank you for your business."),
    ]),
)
```

Nine components in, six components out: 1 logo + 1 header text (was 3+3) + 1 bill-to + 1 merged table (was 3 fragments) + 1 totals + 1 footer note. `document.iter_components()` gives a flat walk across all six in header→body→footer order. This was verified to construct, serialize to JSON, and validate correctly (the table grid validator rejects any cell whose `col` exceeds `column_count`).

---

## 4. What to keep vs. cut

Ranked by how much it costs you vs. how much it buys you, given your stated priorities:

**Core to the idea — keep:**
- The three-zone split itself (`header`/`body`/`footer`) on both `PageIR` and `DocumentIR`.
- `TableComponent.merged_from_pages` and the grid validator — this is your invoice use case.
- `duplicate_count` and `source_refs` — cheap, and this is your entire dedup story in two fields.

**Worth keeping, low cost:**
- `BBox` as fully optional — costs nothing if the layout parser doesn't give you it; useful the moment you want to render or debug layout visually.
- `merge_confidence` on both component types — one field, and it's your only signal for "this decision might be wrong."

**Candidates to cut or defer if you want to move faster:**
- `TableCell.row_span`/`col_span` — only needed if your real documents actually have merged cells. Drop if not, add back later; it's additive.
- Per-cell `source_refs` (not currently in the model, but a natural next addition) — resist adding this unless you actually need cell-level audit trails; component-level provenance is probably enough.
- `document_type` on `DocumentIR` — nice for downstream routing, but only useful if Step 2 actually classifies it; leave as `Optional` and ignore it until you need it.

I deliberately left out anything resembling the earlier `SectionNode`/nested-tree concept — no version of that survives in this design. If you find later that some documents genuinely need sub-grouping within `body` (e.g. distinguishing "line items table" from "an unrelated second table" when there are two), that's an argument for a `group` hint field on `Component` rather than resurrecting a tree.
