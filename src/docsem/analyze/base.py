"""
Shared contract for every DocumentIR analysis stage.

Naming: these are called "analyzers," not "passes." "Pass" is fine as
loose conceptual language (a full traversal over the node list), but as
a class name it doesn't say what the object does. "Analyzer" reads
naturally for all three stages, including reading-order — "analyze the
layout to assign order" is an honest description, whereas "detector"
would be odd for something that doesn't detect a relationship so much
as compute a property.

Every analyzer:
- takes the current list of IRNode objects (already built, in whatever
  order) plus the relations accumulated by earlier analyzers
- may mutate node fields that are its own to own (see `owns_fields`)
- returns the NEW relations it produces (never removes existing ones —
  an analyzer should only ever add to the graph, not edit another
  analyzer's output)

This makes the DocumentIR builder a simple, uniform loop:

    relations = []
    for analyzer in [ReadingOrderAnalyzer(), TableContinuationAnalyzer(), DuplicateAnalyzer()]:
        relations += analyzer.run(nodes, relations)

Ordering of that list matters (see ReadingOrderAnalyzer.requires), but
the loop itself doesn't need to know why.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import ClassVar

from ..ir.document import IRNode, IRRelation


class Analyzer(ABC):
    """
    Base class for all DocumentIR analysis stages.

    Subclasses implement `run()`. Two class-level declarations exist so
    a pipeline runner can validate ordering and mutation scope without
    reading each analyzer's implementation:

    - `requires`: names of other analyzers (by `name`) that must have
      already run. The pipeline runner checks this before calling
      `run()` and raises rather than silently producing wrong results
      from missing order_index, etc.
    - `owns_fields`: which IRNode attributes this analyzer is allowed to
      write. Documentation + a cheap guard rail — not deep enforcement,
      but enough to catch an analyzer accidentally overwriting another
      analyzer's field during development.
    """

    name: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ()
    owns_fields: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def run(self, nodes: list[IRNode], relations: list[IRRelation]) -> list[IRRelation]:
        """
        Execute this analyzer.

        `nodes` — the full node list built so far. May be mutated ONLY on
        the fields listed in `owns_fields` (e.g. ReadingOrderAnalyzer may
        write `order_index`, nothing else).

        `relations` — every relation produced by analyzers that ran
        before this one. Read-only: an analyzer should never mutate or
        remove entries from this list, only return new ones of its own.

        Returns: the list of new IRRelation objects this analyzer
        produced. The pipeline runner appends these to the running total.
        """
        raise NotImplementedError


class AnalyzerPipeline:
    """
    Runs a sequence of Analyzers in order, checking `requires` before
    each one and accumulating relations. This is the thing you actually
    call to build a DocumentIR's relations from a node list.

    Deliberately dumb — no parallelism, no retry logic. Pass 2 and 3
    don't depend on each other (per the design discussion), so if you
    want concurrency later, that's a different runner; this one just
    guarantees correctness of the `requires` ordering.
    """

    def __init__(self, analyzers: list[Analyzer]):
        self._analyzers = analyzers
        self._validate_ordering()

    def _validate_ordering(self) -> None:
        completed: set[str] = set()
        for analyzer in self._analyzers:
            missing = [r for r in analyzer.requires if r not in completed]
            if missing:
                raise ValueError(
                    f"{analyzer.name} requires {missing} to run first, "
                    f"but the pipeline order given doesn't satisfy that."
                )
            completed.add(analyzer.name)

    def run(self, nodes: list[IRNode]) -> list[IRRelation]:
        relations: list[IRRelation] = []
        for analyzer in self._analyzers:
            new_relations = analyzer.run(nodes, relations)
            relations.extend(new_relations)
        return relations