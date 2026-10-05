"""
Helpers for TableContinuationAnalyzer.

Candidates: consecutive tables in reading order, on consecutive pages,
with the same trusted column count. Every candidate goes to the LLM.

The LLM judges from table data only: headers, row samples, and a few
deterministic facts computed here (header relation, incrementing columns).
Text between the fragments is deliberately NOT used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..extraction.base import ExtractedTable
from ..ir.document import DocumentIR, Node


def column_count(table: ExtractedTable) -> int | None:
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


def find_candidates(ir: DocumentIR) -> list[Candidate]:
    ordered = ir.nodes.ordered()
    tables = ordered.tables()

    out: list[Candidate] = []
    for prev, nxt in zip(tables, tables[1:], strict=False):
        if nxt.page != prev.page + 1:
            continue
        pt, nt = ir.resolve(prev), ir.resolve(nxt)
        if not isinstance(pt, ExtractedTable) or not isinstance(nt, ExtractedTable):
            continue
        a, b = column_count(pt), column_count(nt)
        if a is None or a != b:
            continue
        out.append(Candidate(prev, nxt, pt, nt, a))
    return out


# ---------- deterministic facts ----------

_TRAILING_INT = re.compile(r"^(.*?)(\d+)$")


def _norm(rows: list[list[str]]) -> list[list[str]]:
    return [[" ".join(c.split()).lower() for c in r] for r in rows]


def header_relation(prev_header: list[list[str]], next_header: list[list[str]]) -> str:
    if not prev_header and not next_header:
        return "neither fragment has a header"
    if not next_header:
        return "B has no header (A does)"
    if not prev_header:
        return "B has a header, A has none"
    if _norm(prev_header) == _norm(next_header):
        return "B repeats A's header identically"
    return "B's header differs from A's"


def incrementing_columns(prev_tail: list[list[str]], next_head: list[list[str]]) -> list[int]:
    """Columns where B's first row = A's last row + 1, with matching text prefixes."""
    if not prev_tail or not next_head:
        return []
    hits = []
    for j, (x, y) in enumerate(zip(prev_tail[-1], next_head[0], strict=False)):
        mx, my = _TRAILING_INT.match(x.strip()), _TRAILING_INT.match(y.strip())
        if mx and my and mx.group(1) == my.group(1) and int(my.group(2)) == int(mx.group(2)) + 1:
            hits.append(j)
    return hits


def compute_facts(
    prev_header: list[list[str]],
    prev_tail: list[list[str]],
    next_header: list[list[str]],
    next_head: list[list[str]],
) -> dict:
    return {
        "header_relation": header_relation(prev_header, next_header),
        "incrementing_columns": incrementing_columns(prev_tail, next_head),
    }


def _facts_text(facts: dict) -> str:
    cols = facts["incrementing_columns"]
    seq = (
        "column(s) "
        + ", ".join(str(c + 1) for c in cols)
        + " (1-based): B's first row is A's last row + 1"
        if cols
        else "none detected"
    )
    return f"- Header: {facts['header_relation']}\n- Incrementing numbering: {seq}"


# ---------- LLM prompt / schema ----------


def continuation_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "is_continuation": {"type": "boolean"},
            "certainty": {"type": "string", "enum": ["certain", "likely", "unsure"]},
            "reason": {"type": "string"},
        },
        "required": ["is_continuation", "certainty"],
        "additionalProperties": False,
    }


def _fmt(rows: list[list[str]], empty: str = "(none)") -> str:
    return "\n".join(" | ".join(r) for r in rows) or empty


def build_continuation_messages(
    prev_header: list[list[str]],
    prev_tail: list[list[str]],
    next_header: list[list[str]],
    next_head: list[list[str]],
    n_cols: int,
    facts: dict,
) -> list[dict]:
    system = (
        "You decide whether table fragment B (top of the next page) continues "
        "table fragment A (bottom of the previous page). Both have the same number "
        "of columns. You are shown table data only; nothing else on the pages is "
        "relevant, so do not speculate about it. Headers may be repeated, re-worded "
        "or missing on B, and an extractor may have mistaken B's first data row for "
        "a header, so a different header does not rule out a continuation and an "
        "identical header does not prove one. Judge from the rows: what each column "
        "holds, value types/units/formats, sequential numbering or IDs, and whether "
        "B's first row reads as the natural next row after A's last row. Rows in "
        "which a grouping value (e.g. a category, country or code) changes can still "
        "belong to one table. Lines marked 'Facts' were computed by code and are "
        "reliable. Answer is_continuation true or false, and set certainty to "
        "'certain', 'likely' or 'unsure'. Answer with JSON only."
    )
    user = (
        f"Columns: {n_cols}\n"
        f"Facts:\n{_facts_text(facts)}\n\n"
        f"Fragment A header:\n{_fmt(prev_header, '(no header)')}\n"
        f"Fragment A last rows:\n{_fmt(prev_tail)}\n\n"
        f"Fragment B header:\n{_fmt(next_header, '(no header)')}\n"
        f"Fragment B first rows:\n{_fmt(next_head)}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
