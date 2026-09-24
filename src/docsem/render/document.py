"""
RenderedDocument — the final, consumable materialization of a DocumentIR.

DocumentIR (nodes + relations) is a QUERY layer: correct, but not
something you hand to a downstream system as-is. A consumer wants actual
content, in order, with continuations already stitched and duplicates
already removed — and the ability to see what was uncertain, without
having to re-derive any of that from relations themselves.

RenderedDocument is that materialization. It is built ONCE from a
DocumentIR via `render(doc_ir, ...)`, is plain-data (safe to
json.dumps after a trivial asdict conversion, safe to hand to a chunker,
a search indexer, or a Markdown exporter), and never mutates the
DocumentIR or any raw source content it points back to.

Relationship to ChunkUnit/chunking_units() on DocumentIR: those already
do the hard part (merge confident continuation chains, drop confident
duplicates, preserve order). RenderedDocument wraps that output for
consumption:
  - adds export methods (to_markdown, to_dict) so a consumer doesn't
    write their own serialization
  - adds an explicit `needs_review` list: relations that were
    DELIBERATELY NOT auto-applied because they fell in an uncertain
    confidence band, surfaced so a human or a downstream policy can
    decide, rather than silently guessed either way
  - adds top-line stats a caller will almost always want (counts of
    merged tables, dropped duplicates, flagged-for-review items) without
    re-scanning the relation list

Nothing here does new analysis. If you swap the continuation/duplicate
SCORING method later (heuristics -> embeddings), nothing in this file
changes — it only ever consumes confidence scores, never computes them.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ..ir.document import DocumentIR, IRRelation, RelationType, NodeKind, ChunkUnit


# =====================================================================
# Confidence bands
# =====================================================================
#
# Three-way split, not a single cutoff, because a single threshold forces
# a binary choice (apply / ignore) on every relation, including the ones
# genuinely too close to call. The middle band exists so those get
# surfaced instead of guessed:
#
#   confidence >= auto_apply_threshold      -> apply automatically
#                                               (merge chain hop / drop duplicate)
#   review_threshold <= confidence < auto   -> apply is WITHHELD; the
#                                               relation is surfaced in
#                                               needs_review instead
#   confidence < review_threshold           -> treated as effectively
#                                               unrelated; not applied,
#                                               not surfaced (too low to
#                                               be worth a reviewer's time)
#
# Defaults are starting points, not calibrated against your real
# documents — tune per relation type once you see false positive/negative
# rates on actual data.


@dataclass
class ConfidenceThresholds:
    continuation_auto_apply: float = 0.75
    continuation_review: float = 0.5
    duplicate_auto_apply: float = 0.85
    duplicate_review: float = 0.6

    def __post_init__(self) -> None:
        if not (0.0 <= self.continuation_review <= self.continuation_auto_apply <= 1.0):
            raise ValueError(
                "continuation thresholds must satisfy "
                "0 <= continuation_review <= continuation_auto_apply <= 1"
            )
        if not (0.0 <= self.duplicate_review <= self.duplicate_auto_apply <= 1.0):
            raise ValueError(
                "duplicate thresholds must satisfy "
                "0 <= duplicate_review <= duplicate_auto_apply <= 1"
            )


# =====================================================================
# Review queue entries
# =====================================================================


@dataclass
class ReviewItem:
    """
    One relation that fell in the review band: confident enough to be
    worth a human's attention, not confident enough to auto-apply.

    `would_do` is a short, human-readable description of what applying
    this relation WOULD have meant, so a reviewer (or a UI) doesn't have
    to go translate "table_continuation, confidence 0.61" into English
    themselves.
    """
    relation: IRRelation
    would_do: str

    @property
    def type(self) -> RelationType:
        return self.relation.type

    @property
    def confidence(self) -> float:
        return self.relation.confidence


# =====================================================================
# The rendered document itself
# =====================================================================


@dataclass
class RenderedDocument:
    """
    The final, ordered, cleaned representation of a document. Plain
    data — safe to serialize, iterate, and hand downstream.

    `units` is the payload: every ChunkUnit that survived dedup and had
    its confident continuation chains merged, in reading order.

    `needs_review` is every relation that landed in the review band
    (see ConfidenceThresholds) — surfaced explicitly rather than
    silently resolved. Consumers that want a fully-automatic pipeline
    can ignore this list; consumers that want a human in the loop for
    uncertain cases have exactly what they need to build that UI.

    `stats` is a small, always-present summary so a caller doesn't have
    to recompute basic counts themselves.
    """
    units: list[ChunkUnit]
    needs_review: list[ReviewItem]
    stats: "RenderStats"
    source_page_count: int

    # ---------------- export methods ----------------

    def to_dict(self) -> dict:
        """
        Plain-dict form, safe for json.dumps. Table cell objects
        (TableCell) are flattened to their .content string here — a
        consumer of the rendered output almost never wants per-cell
        confidence, just the value; that per-cell confidence is still
        recoverable from the raw source via node provenance if ever
        needed, since RenderedDocument never deletes anything from
        DocumentIR, it only decides what to surface by default.
        """
        return {
            "source_page_count": self.source_page_count,
            "stats": {
                "total_units": self.stats.total_units,
                "merged_tables": self.stats.merged_tables,
                "duplicates_dropped": self.stats.duplicates_dropped,
                "flagged_for_review": self.stats.flagged_for_review,
            },
            "units": [self._unit_to_dict(u) for u in self.units],
            "needs_review": [
                {
                    "type": item.type.value,
                    "confidence": item.confidence,
                    "source_id": item.relation.source_id,
                    "target_id": item.relation.target_id,
                    "would_do": item.would_do,
                }
                for item in self.needs_review
            ],
        }

    @staticmethod
    def _unit_to_dict(unit: ChunkUnit) -> dict:
        base = {
            "node_ids": unit.node_ids,
            "kind": unit.kind.value,
            "order_index": unit.order_index,
            "merged": unit.merged,
        }
        if unit.kind == NodeKind.BLOCK:
            base["text"] = unit.text
        else:
            base["header"] = [cell.content for cell in unit.header] if unit.header else []
            base["rows"] = (
                [[cell.content for cell in row] for row in unit.rows] if unit.rows else []
            )
        return base

    def to_markdown(self) -> str:
        """
        Human-readable export. Tables render as GitHub-flavored Markdown
        tables; blocks render as plain paragraphs. This is meant for
        quick inspection/review, not as a lossless format — use to_dict()
        or the underlying DocumentIR if you need full fidelity.
        """
        lines: list[str] = []
        for unit in self.units:
            if unit.kind == NodeKind.BLOCK:
                lines.append(unit.text or "")
                lines.append("")
            else:
                lines.append(RenderedDocument._table_to_markdown(unit))
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _table_to_markdown(unit: ChunkUnit) -> str:
        if not unit.header:
            return ""
        header_cells = [c.content for c in unit.header]
        lines = [
            "| " + " | ".join(header_cells) + " |",
            "| " + " | ".join("---" for _ in header_cells) + " |",
        ]
        for row in unit.rows or []:
            cells = [c.content for c in row]
            # Pad/truncate defensively — a malformed provider row with a
            # different column count than the header shouldn't break the
            # table's Markdown structure.
            if len(cells) < len(header_cells):
                cells = cells + [""] * (len(header_cells) - len(cells))
            elif len(cells) > len(header_cells):
                cells = cells[: len(header_cells)]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


@dataclass
class RenderStats:
    total_units: int
    merged_tables: int
    duplicates_dropped: int
    flagged_for_review: int


# =====================================================================
# The builder function
# =====================================================================


def render(
    doc_ir: DocumentIR,
    thresholds: Optional[ConfidenceThresholds] = None,
) -> RenderedDocument:
    """
    Materialize a DocumentIR into a RenderedDocument.

    This is a thin orchestration layer over DocumentIR.chunking_units():
    that method already does the real work (confident-chain merging,
    confident-duplicate dropping, ordering). render() additionally:
      1. computes the review queue — relations in the "review" band that
         chunking_units() silently declined to apply (by design; that
         method only auto-applies at or above the confidence you pass
         it), so nothing uncertain disappears without a trace
      2. computes summary stats
      3. wraps the result in the export-capable RenderedDocument shape

    Pure function: does not mutate doc_ir, its nodes, or its relations.
    """
    thresholds = thresholds or ConfidenceThresholds()

    units = doc_ir.chunking_units(
        min_continuation_confidence=thresholds.continuation_auto_apply,
        min_duplicate_confidence=thresholds.duplicate_auto_apply,
    )

    needs_review = _build_review_queue(doc_ir, thresholds)

    merged_tables = sum(1 for u in units if u.kind == NodeKind.TABLE and u.merged)
    duplicates_dropped = sum(
        1
        for r in doc_ir.relations
        if r.type == RelationType.DUPLICATE and r.confidence >= thresholds.duplicate_auto_apply
    )

    stats = RenderStats(
        total_units=len(units),
        merged_tables=merged_tables,
        duplicates_dropped=duplicates_dropped,
        flagged_for_review=len(needs_review),
    )

    source_page_count = getattr(doc_ir.source, "page_count", 0) or _infer_page_count(doc_ir)

    return RenderedDocument(
        units=units,
        needs_review=needs_review,
        stats=stats,
        source_page_count=source_page_count,
    )


def _infer_page_count(doc_ir: DocumentIR) -> int:
    """Fallback if doc_ir.source has no usable page_count: derive from nodes."""
    pages = [n.page for n in doc_ir.nodes]
    return max(pages) if pages else 0


def _build_review_queue(
    doc_ir: DocumentIR, thresholds: ConfidenceThresholds
) -> list[ReviewItem]:
    """
    Every relation whose confidence lands in [review_threshold,
    auto_apply_threshold) for its type — confident enough to flag,
    not confident enough to auto-apply. This is where uncertain
    decisions become visible instead of being silently absorbed into
    "just didn't merge" with no explanation.
    """
    items: list[ReviewItem] = []

    for r in doc_ir.relations:
        if r.type == RelationType.TABLE_CONTINUATION:
            lo, hi = thresholds.continuation_review, thresholds.continuation_auto_apply
            if lo <= r.confidence < hi:
                items.append(
                    ReviewItem(
                        relation=r,
                        would_do=(
                            f"Merge table fragment {r.target_id} into the table "
                            f"starting at {r.source_id} (treat as one continued table)."
                        ),
                    )
                )
        elif r.type == RelationType.DUPLICATE:
            lo, hi = thresholds.duplicate_review, thresholds.duplicate_auto_apply
            if lo <= r.confidence < hi:
                node = doc_ir.node(r.target_id)
                kind_label = node.kind.value if node else "item"
                items.append(
                    ReviewItem(
                        relation=r,
                        would_do=(
                            f"Drop {kind_label} {r.target_id} as a duplicate of "
                            f"{r.source_id} (method={r.method or 'unknown'})."
                        ),
                    )
                )

    return sorted(items, key=lambda it: it.confidence, reverse=True)