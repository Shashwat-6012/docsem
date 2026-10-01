from __future__ import annotations


import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence, Any
from ..llm import Message


@dataclass(frozen=True)
class TableIssues:
    expected_cols: int                                   # modal data-row width
    bad_header_indices: list[int] = field(default_factory=list)  # which header rows are off
    missing_header: bool = False                         # table has no header rows at all
    bad_row_indices: list[int] = field(default_factory=list)

    @property
    def header_flagged(self) -> bool:
        return self.missing_header or bool(self.bad_header_indices)


def detect_issues(
    header: Sequence[Sequence[Any]], rows: Sequence[Sequence[Any]]
) -> Optional[TableIssues]:
    """header is a list of header rows (top to bottom); rows is the data rows.
    Return None if the table is consistent (or can't be judged)."""
    if not rows:
        return None

    counts = Counter(len(r) for r in rows)
    header_widths = Counter(len(h) for h in header)
    # Deterministic tie-break: higher count, then most header rows agree, then wider.
    expected = max(counts, key=lambda w: (counts[w], header_widths[w], w))

    missing_header = len(header) == 0
    bad_header = [i for i, h in enumerate(header) if len(h) != expected]
    bad_rows = [i for i, r in enumerate(rows) if len(r) != expected]

    if not missing_header and not bad_header and not bad_rows:
        return None
    return TableIssues(expected, bad_header, missing_header, bad_rows)


# Prompting

_HEADER_SYSTEM = (
    "You repair table headers that were auto-detected incorrectly. "
    "Placeholder names such as \"Unknown\" or empty names are wrong. "
    "Return ONLY JSON with the corrected header."
)
_ROW_SYSTEM = (
    "You repair a malformed table row so it matches the header's columns. "
    "Do not invent data; only split, merge, or realign the existing cell values, "
    "using an empty string for a genuinely missing cell. Return ONLY JSON."
)


def header_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "header": {"type": "array", "items": {"type": "string"},
                       "minItems": n, "maxItems": n},
        },
        "required": ["header"],
        "additionalProperties": False,
    }


def row_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "row": {"type": "array", "items": {"type": "string"},
                    "minItems": n, "maxItems": n},
        },
        "required": ["row"],
        "additionalProperties": False,
    }


def build_header_messages(
    levels: Sequence[Sequence[str]],
    target: int,                       # 0-based index of the header row to repair
    sample_rows: Sequence[Sequence[str]],
    n: int,
) -> list[Message]:
    def render(lv: Sequence[Sequence[str]], t: int) -> str:
        return "\n".join(
            f"Level {i + 1}{' (REPAIR THIS)' if i == t else ''}: {json.dumps(list(r))}"
            for i, r in enumerate(lv)
        )

    example_levels = [
        ["Revenue", "Revenue", "Region", "Unknown"],
        ["Q1", "Q2", ""],
    ]
    user = (
        "Example input:\n"
        f"Header levels (top to bottom):\n{render(example_levels, 0)}\n"
        "Expected columns: 3\n"
        'Sample rows: [["100", "200", "North"]]\n'
        "Example output:\n"
        '{"header": ["Revenue", "Revenue", "Region"]}\n\n'
        "Input:\n"
        f"Header levels (top to bottom):\n{render(levels, target)}\n"
        f"Expected columns: {n}\n"
        f"Sample rows: {json.dumps([list(r) for r in sample_rows])}\n"
        "Output:\n"
    )
    return [{"role": "system", "content": _HEADER_SYSTEM},
            {"role": "user", "content": user}]

def build_row_messages(header: Sequence[str], row: Sequence[str]) -> list[Message]:
    user = (
        'Example input:\nHeader: ["Date", "Customer", "Amount", "Status"]\n'
        'Malformed row: ["2026-01-02", "Mary Ann", "$80"]\n'
        'Example output:\n{"row": ["2026-01-02", "Mary Ann", "$80", ""]}\n\n'
        f"Input:\nHeader: {json.dumps(list(header))}\n"
        f"Malformed row: {json.dumps(list(row))}\nOutput:\n"
    )
    return [{"role": "system", "content": _ROW_SYSTEM},
            {"role": "user", "content": user}]