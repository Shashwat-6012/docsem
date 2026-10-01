"""
Analyzers for DocumentIR construction: TableContinuationAnalyzer.

TableContinuationAnalyzer finds cross-page table fragments
that are really one logical table, using the reading order to reason about
adjacency and "what's in between."

Both are Analyzer subclasses (see analyzer_base.py) — pure with respect
to `IRNode.source`: nodes in, relations out. Neither mutates the raw
ExtractedBlock/ExtractedTable data — only the fields each analyzer
declares in `owns_fields` (order_index; is_continuation_of), per the
wrap-don't-replace principle.
"""

from __future__ import annotations
import logging
from typing import ClassVar
from concurrent.futures import ThreadPoolExecutor

from ..extraction.base import TableCell, ExtractedTable
from ..ir.document import DocumentIR, Relation, RelationType
from .base import Analyzer
from ._continuation import Candidate, find_candidates, between_snippets
from ._continuation import texts, build_continuation_messages, continuation_schema

import logging
import re
from typing import Any, ClassVar, Optional

from ..llm import LLMError, LLMProvider
from ._structure import detect_issues
from ._structure import (
    build_header_messages, build_row_messages, header_schema, row_schema,
)

logger = logging.getLogger(__name__)

METADATA_KEY = "structure_repair"

_PLACEHOLDER = re.compile(
    r"^(|unknown|none|null|n/a|unnamed:?[ _]?\d*|col(umn)?[ _]?\d+)$", re.IGNORECASE
)


def _is_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER.match(text.strip()))


def _texts(cells: list[TableCell]) -> list[str]:
    return [c.content for c in cells]


def _cells(texts: list[str]) -> list[TableCell]:
    return [TableCell(content=t) for t in texts]  # LLM output -> confidence None


def _is_str_list(value: Any, n: int) -> bool:
    return isinstance(value, list) and len(value) == n and all(isinstance(v, str) for v in value)


def _same_content(original: list[str], fixed: list[str]) -> bool:
    """Realigning/splitting/merging cells must not add or drop characters (ignoring spaces)."""
    squash = lambda cells: "".join("".join(cells).split())
    return squash(original) == squash(fixed)

class TableContinuationAnalyzer(Analyzer):
    """
    Finds cross-page table continuations.

    1. Candidates (code): consecutive tables in reading order, consecutive pages,
       same trusted column count.
    2. Every candidate is judged by the LLM from headers (if any) and rows.
    3. Emits a TABLE_CONTINUATION relation for each verdict above `emit_floor`;
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
        accept_at: float = 0.8,
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

        # Prompts are built here (touches the IR); only the LLM calls run in threads.
        jobs = [(c, self._messages(document_ir, c)) for c in todo]
        with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as pool:
            verdicts = list(pool.map(lambda j: self._ask(j[1], continuation_schema()), jobs))

        for (c, _), data in zip(jobs, verdicts):
            p = self._probability(data)
            if p is None:
                logger.warning("no usable verdict for %s -> %s", c.prev.id, c.nxt.id)
                continue
            status = ("likely" if p >= self.accept_at
                    else "needs_review" if p >= self.review_at else "unlikely")
            logger.info("continuation %s -> %s: %.2f (%s)", c.prev.id, c.nxt.id, p, status)
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
                        "n_cols": c.n_cols,
                        "has_header": [bool(c.prev_table.header), bool(c.next_table.header)],
                    },
                )
            )
        return document_ir

    def _messages(self, ir: DocumentIR, c: Candidate) -> list[dict]:
        k = self.sample_rows
        return build_continuation_messages(
            prev_header=texts(c.prev_table.header),
            prev_tail=texts(c.prev_table.rows[-k:]),
            next_header=texts(c.next_table.header),
            next_head=texts(c.next_table.rows[:k]),
            n_cols=c.n_cols,
            between=between_snippets(ir, c),
        )

    @staticmethod
    def _probability(data: Optional[dict]) -> Optional[float]:
        if not data or not isinstance(data.get("is_continuation"), bool):
            return None
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        return conf if data["is_continuation"] else 1.0 - conf

    def _ask(self, messages, schema) -> Optional[dict]:
        for attempt in range(1, self.attempts + 1):
            try:
                data = self.provider.generate_json(messages, schema)  # type: ignore[union-attr]
                if isinstance(data, dict):
                    return data
            except LLMError as e:
                logger.warning("LLM attempt %d/%d failed: %s", attempt, self.attempts, e)
        return None
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
            table.id,
            issues.expected_cols,
            len(issues.bad_header_indices),
            len(issues.bad_row_indices),
        )

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

    # ---- header ------------------------------------------------------------
    def _repair_header(
        self, table: ExtractedTable, n: int
    ) -> tuple[Optional[list[TableCell]], Optional[str]]:
        header = table.header

        # Rule: extra trailing placeholder columns -> drop them (keeps original cells/confidence).
        if len(header) > n and all(_is_placeholder(c.content) for c in header[n:]):
            return header[:n], "rule"

        # An empty header would force the model to invent every column name.
        if self.provider is None or not header:
            return None, None

        sample = [_texts(r) for r in table.rows if len(r) == n][: self.max_sample_rows]
        data = self._ask(build_header_messages(_texts(header), sample, n), header_schema(n))
        value = data.get("header") if data else None
        if _is_str_list(value, n):
            return _cells(value), "llm"
        logger.warning("header repair failed for table %s", table.id)
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

        data = self._ask(build_row_messages(header_levels, _texts(row)), row_schema(n))
        value = data.get("row") if data else None
        if _is_str_list(value, n) and _same_content(_texts(row), value):
            return _cells(value), "llm"
        logger.warning("row repair rejected (invalid shape or content changed)")
        return None, None

    # ---- provider call -----------------------------------------------------
    def _ask(self, messages, schema) -> Optional[dict]:
        for attempt in range(1, self.attempts + 1):
            try:
                data = self.provider.generate_json(messages, schema)  # type: ignore[union-attr]
                if isinstance(data, dict):
                    return data
            except LLMError as e:
                logger.warning("LLM attempt %d/%d failed: %s", attempt, self.attempts, e)
        return None