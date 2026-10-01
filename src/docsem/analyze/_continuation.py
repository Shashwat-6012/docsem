"""
Helpers for TableContinuationAnalyzer.

Candidates: consecutive tables in reading order, on consecutive pages,
with the same trusted column count. Every candidate goes to the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..extraction.base import ExtractedTable
from ..ir.document import DocumentIR, Node


def column_count(table: ExtractedTable) -> Optional[int]:
    """Single column count if header levels and rows all agree, else None (ragged)."""
    widths = {len(r) for r in table.header} | {len(r) for r in table.rows}
    return widths.pop() if len(widths) == 1 else None


def texts(rows) -> list[list[str]]:
    return [[c.content.strip() for c in r] for r in rows]


@dataclass
class Candidate:
    prev: Node
    nxt: Node
    prev_table: ExtractedTable
    next_table: ExtractedTable
    n_cols: int
    between: list[Node] = field(default_factory=list)


def find_candidates(ir: DocumentIR) -> list[Candidate]:
    ordered = ir.nodes.ordered()
    position = {n.id: i for i, n in enumerate(ordered)}
    tables = ordered.tables()

    out: list[Candidate] = []
    for prev, nxt in zip(tables, tables[1:]):
        if nxt.page != prev.page + 1:
            continue
        pt, nt = ir.resolve(prev), ir.resolve(nxt)
        if pt is None or nt is None:
            continue
        a, b = column_count(pt), column_count(nt)
        if a is None or a != b:
            continue
        between = ordered[position[prev.id] + 1: position[nxt.id]]
        out.append(Candidate(prev, nxt, pt, nt, a, list(between)))
    return out


def between_snippets(ir: DocumentIR, c: Candidate, limit: int = 3, width: int = 160) -> list[str]:
    """Short text of blocks sitting between the two fragments (footers, headings, paragraphs)."""
    out = []
    for node in c.between:
        if node.is_table:
            continue
        raw: Any = ir.resolve(node)
        text = (getattr(raw, "text", None) or getattr(raw, "content", None) or "").strip()
        if text:
            out.append(text[:width])
        if len(out) >= limit:
            break
    return out


# ---------- LLM prompt / schema ----------

def continuation_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "is_continuation": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["is_continuation", "confidence"],
        "additionalProperties": False,
    }


def _fmt(rows: list[list[str]], empty: str = "(none)") -> str:
    return "\n".join(" | ".join(r) for r in rows) or empty


def build_continuation_messages(
    prev_header: list[list[str]], prev_tail: list[list[str]],
    next_header: list[list[str]], next_head: list[list[str]],
    n_cols: int, between: list[str],
) -> list[dict]:
    system = (
        "You decide whether table fragment B (top of the next page) continues "
        "table fragment A (bottom of the previous page). Both have the same number "
        "of columns. Headers may be repeated, re-worded, or missing on B, and an "
        "extractor may have mistaken B's first data row for a header; so a "
        "different header does not rule out a continuation, and an identical header "
        "does not prove one (separate tables can share a schema). Judge mainly from "
        "the rows: column meaning, value types/units/formats, row numbering, and "
        "whether B's first row reads as a natural follow-on of A's last row. Real "
        "content between the fragments (a paragraph or a new heading) suggests "
        "separate tables; page footers or numbers do not. "
        "Set confidence to how sure you are of your answer. Answer with JSON only."
    )
    user = (
        f"Columns: {n_cols}\n"
        f"Text between the fragments: {between or 'nothing'}\n\n"
        f"Fragment A header:\n{_fmt(prev_header, '(no header)')}\n"
        f"Fragment A last rows:\n{_fmt(prev_tail)}\n\n"
        f"Fragment B header:\n{_fmt(next_header, '(no header)')}\n"
        f"Fragment B first rows:\n{_fmt(next_head)}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]