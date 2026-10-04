"""
Analyzers for DocumentIR construction: TableContinuationAnalyzer and TableStructureAnalyzer.

TableContinuationAnalyzer finds cross-page table fragments that are really one
logical table, judging from table data only (headers, row samples, deterministic
facts). It emits TABLE_CONTINUATION relations and never modifies raw tables.

TableStructureAnalyzer repairs header/row column-count inconsistencies in place
(rules first, LLM for the remainder) and records an audit trail in
table.metadata["structure_repair"].

Both are Analyzer subclasses (see base.py), per the wrap-don't-replace principle
for everything except the fields they explicitly document as mutated.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar, Optional

from ..extraction.base import ExtractedTable, TableCell
from ..ir.document import DocumentIR, Relation, RelationType
from ..llm import LLMError, LLMProvider
from ._continuation import (
    Candidate, build_continuation_messages, compute_facts,
    continuation_schema, find_candidates, texts,
)
from ._structure import (
    build_header_messages, build_row_messages, detect_issues, header_schema, row_schema,
)
from .base import Analyzer

logger = logging.getLogger(__name__)

METADATA_KEY = "structure_repair"

_PLACEHOLDER = re.compile(
    r"^(|unknown|none|null|n/a|unnamed:?[ _]?\d*|col(umn)?[ _]?\d+)$", re.IGNORECASE
)

# Self-reported certainty -> probability of the stated answer (coarse buckets, not calibrated).
_CERTAINTY = {"certain": 0.95, "likely": 0.75, "unsure": 0.55}


def _is_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER.match(text.strip()))


def _texts(cells: list[TableCell]) -> list[str]:
    return [c.content for c in cells]


def _cells(values: list[str]) -> list[TableCell]:
    return [TableCell(content=t) for t in values]  # LLM output -> confidence None


def _is_str_list(value: Any, n: int) -> bool:
    return isinstance(value, list) and len(value) == n and all(isinstance(v, str) for v in value)


def _same_content(original: list[str], fixed: list[str]) -> bool:
    """Realigning/splitting/merging cells must not add or drop characters (ignoring spaces)."""
    squash = lambda cells: "".join("".join(cells).split())
    return squash(original) == squash(fixed)


def _generate_json(
    provider: LLMProvider, messages, schema, attempts: int, label: str = ""
) -> Optional[dict]:
    """Call the provider with retries; returns a dict or None. `label` ties log lines to a candidate."""
    for attempt in range(1, attempts + 1):
        try:
            data = provider.generate_json(messages, schema)
        except LLMError as e:
            logger.warning("[%s] LLM attempt %d/%d failed: %s", label, attempt, attempts, e)
            continue
        if isinstance(data, dict):
            logger.debug("[%s] LLM verdict: %s", label, data)
            return data
        logger.warning("[%s] LLM attempt %d/%d returned non-dict", label, attempt, attempts)
    return None


class TableContinuationAnalyzer(Analyzer):
    """
    Finds cross-page table continuations.

    1. Candidates (code): consecutive tables in reading order, consecutive pages,
       same trusted column count.
    2. Every candidate is judged by the LLM from headers and rows only, plus a few
       deterministic facts (header relation, incrementing numbering). Text between
       the fragments is not used.
    3. The LLM's certainty label maps to a fixed probability. A TABLE_CONTINUATION
       relation is emitted for each verdict at or above `emit_floor`;
       relation.confidence = P(continuation), metadata["status"] in
       likely | needs_review | unlikely, plus the LLM's reason, for user verification.

    Without a provider nothing is emitted. Raw tables are never modified.
    """

    name: ClassVar[str] = "table_continuation"
    requires: ClassVar[tuple[str, ...]] = ("reading_order",)
    owns_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        sample_rows: int = 3,
        emit_floor: float = 0.15,     # 0.0 = emit every candidate
        accept_at: float = 0.7,       # "likely"/"certain" continuation
        review_at: float = 0.4,
        attempts: int = 2,
        max_workers: int = 4,         # 1 = sequential
    ):
        super().__init__()
        self.provider = provider
        self.sample_rows = sample_rows
        self.emit_floor = emit_floor
        self.accept_at = accept_at
        self.review_at = review_at
        self.attempts = attempts
        self.max_workers = max_workers

    def run(self, document_ir: DocumentIR) -> DocumentIR:
        if self.provider is None:
            logger.warning("TableContinuationAnalyzer has no provider; skipping")
            return document_ir

        linked = {(r.source_id, r.target_id) for r in document_ir.relations.continuations()}
        todo = [c for c in find_candidates(document_ir) if (c.prev.id, c.nxt.id) not in linked]
        logger.debug("%d continuation candidates to judge", len(todo))
        if not todo:
            return document_ir

        # Prompts are built up front; only the LLM calls run in threads.
        jobs = [(c, *self._prepare(c)) for c in todo]
        schema = continuation_schema()
        with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as pool:
            verdicts = list(pool.map(
                lambda j: self._ask(j[1], schema, f"{j[0].prev.id}->{j[0].nxt.id}"), jobs
            ))

        for (c, _, facts), data in zip(jobs, verdicts):
            p = self._probability(data)
            if p is None:
                logger.warning("no usable verdict for %s -> %s", c.prev.id, c.nxt.id)
                continue
            status = ("likely" if p >= self.accept_at
                      else "needs_review" if p >= self.review_at else "unlikely")
            logger.info("continuation %s -> %s: %.2f (%s) | %s | %s",
                        c.prev.id, c.nxt.id, p, status, facts["header_relation"],
                        data.get("reason", ""))
            if p < self.emit_floor:
                continue
            document_ir.relations.append(
                Relation(
                    type=RelationType.TABLE_CONTINUATION,
                    source_id=c.prev.id,
                    target_id=c.nxt.id,
                    confidence=round(p, 3),
                    method="llm",
                    metadata={
                        "status": status,
                        "reason": str(data.get("reason", "")),
                        "certainty": data.get("certainty"),
                        "n_cols": c.n_cols,
                        "has_header": [bool(c.prev_table.header), bool(c.next_table.header)],
                        "header_relation": facts["header_relation"],
                        "incrementing_columns": facts["incrementing_columns"],
                    },
                )
            )
        return document_ir

    def _prepare(self, c: Candidate) -> tuple[list[dict], dict]:
        k = self.sample_rows
        prev_header, next_header = texts(c.prev_table.header), texts(c.next_table.header)
        prev_tail, next_head = texts(c.prev_table.rows[-k:]), texts(c.next_table.rows[:k])
        facts = compute_facts(prev_header, prev_tail, next_header, next_head)
        messages = build_continuation_messages(
            prev_header=prev_header, prev_tail=prev_tail,
            next_header=next_header, next_head=next_head,
            n_cols=c.n_cols, facts=facts,
        )
        return messages, facts

    @staticmethod
    def _probability(data: Optional[dict]) -> Optional[float]:
        """P(continuation) from the boolean answer and the coarse certainty bucket."""
        if not data or not isinstance(data.get("is_continuation"), bool):
            return None
        p = _CERTAINTY.get(data.get("certainty"), _CERTAINTY["unsure"])
        return p if data["is_continuation"] else 1.0 - p

    def _ask(self, messages, schema, label: str = "") -> Optional[dict]:
        return _generate_json(self.provider, messages, schema, self.attempts, label)  # type: ignore[arg-type]


class TableStructureAnalyzer(Analyzer):
    """
    1. Deterministically flag header/row column-count inconsistencies.
    2. Fix what rules can fix safely (trailing placeholder header cells, trailing empty row cells).
    3. Send only the remainder to an LLM provider.

    Repairs are applied IN PLACE to document_ir.source.tables (header and rows), so all
    downstream stages see the corrected tables. Each repaired table gets an audit record in
    table.metadata["structure_repair"]. With provider=None only rule-based repairs run.
    """

    name: ClassVar[str] = "table_structure"
    requires: ClassVar[tuple[str, ...]] = ("reading_order",)
    owns_fields: ClassVar[tuple[str, ...]] = ()  # mutates ExtractedTable.header / .rows

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        max_sample_rows: int = 3,
        attempts: int = 2,
    ):
        super().__init__()
        self.provider = provider
        self.max_sample_rows = max_sample_rows
        self.attempts = attempts

    def run(self, document_ir: DocumentIR) -> DocumentIR:
        for table in document_ir.source.tables:
            self._process(table)
        return document_ir

    # ---- per-table ---------------------------------------------------------
    def _process(self, table: ExtractedTable) -> None:
        issues = detect_issues(table.header, table.rows)
        if issues is None:
            return

        logger.info("table %s flagged: %d expected cols, %d bad header rows, %d bad data rows",
                    table.id, issues.expected_cols,
                    len(issues.bad_header_indices), len(issues.bad_row_indices))

        n = issues.expected_cols
        record = self._record(table, n)

        # ---- header levels, repaired one row at a time ----
        if issues.missing_header:
            record["header"] = {"unresolved": True, "original": []}
        else:
            entry = record.setdefault("header", {})
            for i in issues.bad_header_indices:
                original = _texts(table.header[i])
                fixed, method = self._repair_header_row(table, i, n)
                if fixed is None:
                    entry[i] = {"unresolved": True, "original": original}
                else:
                    table.header[i] = fixed                    # in-place
                    entry[i] = {"method": method, "original": original}

        # ---- data rows ----
        if issues.bad_row_indices:
            if not table.header or any(len(h) != n for h in table.header):
                # Can't trust the header levels to guide row repair.
                record["unresolved_rows"] = sorted(
                    set(record["unresolved_rows"]) | set(issues.bad_row_indices)
                )
                return

            levels = [_texts(h) for h in table.header]         # list[list[str]], structure kept
            for i in issues.bad_row_indices:
                original = _texts(table.rows[i])
                fixed_row, method = self._repair_row(levels, table.rows[i], n)
                if fixed_row is None:
                    if i not in record["unresolved_rows"]:
                        record["unresolved_rows"].append(i)
                else:
                    table.rows[i] = fixed_row                  # in-place
                    record["rows"][i] = {"method": method, "original": original}
                    if i in record["unresolved_rows"]:
                        record["unresolved_rows"].remove(i)

    @staticmethod
    def _record(table: ExtractedTable, n: int) -> dict:
        """Get-or-create the audit record (merges with any earlier run)."""
        record = table.metadata.setdefault(METADATA_KEY, {})
        record["expected_cols"] = n
        record.setdefault("rows", {})
        record.setdefault("unresolved_rows", [])
        return record

    # ---- header (one level / row at a time) --------------------------------
    def _repair_header_row(
        self, table: ExtractedTable, i: int, n: int
    ) -> tuple[Optional[list[TableCell]], Optional[str]]:
        row = table.header[i]

        # Rule: extra trailing placeholder columns -> drop them (keeps original cells/confidence).
        if len(row) > n and all(_is_placeholder(c.content) for c in row[n:]):
            return row[:n], "rule"

        # An empty header row would force the model to invent every column name.
        if self.provider is None or not row:
            return None, None

        sample = [_texts(r) for r in table.rows if len(r) == n][: self.max_sample_rows]
        data = self._ask(
            build_header_messages(_texts(row), sample, n), header_schema(n),
            f"{table.id}:header[{i}]",
        )
        value = data.get("header") if data else None
        if _is_str_list(value, n):
            return _cells(value), "llm"
        logger.warning("header repair failed for table %s (level %d)", table.id, i)
        return None, None

    # ---- rows --------------------------------------------------------------
    def _repair_row(
        self, header_levels: list[list[str]], row: list[TableCell], n: int
    ) -> tuple[Optional[list[TableCell]], Optional[str]]:
        # Rule: extra trailing empty cells -> drop them.
        if len(row) > n and all(not c.content.strip() for c in row[n:]):
            return row[:n], "rule"

        if self.provider is None:
            return None, None

        data = self._ask(
            build_row_messages(header_levels, _texts(row)), row_schema(n), "row"
        )
        value = data.get("row") if data else None
        if _is_str_list(value, n) and _same_content(_texts(row), value):
            return _cells(value), "llm"
        logger.warning("row repair rejected (invalid shape or content changed)")
        return None, None

    # ---- provider call -----------------------------------------------------
    def _ask(self, messages, schema, label: str = "") -> Optional[dict]:
        return _generate_json(self.provider, messages, schema, self.attempts, label)  # type: ignore[arg-type]