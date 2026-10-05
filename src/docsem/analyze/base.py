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

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from ..ir.document import DocumentIR

logger = logging.getLogger(__name__)


class Analyzer(ABC):
    """
    Base class for all DocumentIR analysis stages.

    Subclasses implement `run()`. Class-level declarations let the
    pipeline validate ordering and mutation scope without
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
    def run(
        self,
        document_ir: DocumentIR
    ) -> DocumentIR:
        """
        Execute this analyzer.

        `document_ir` is the complete IR, including relations from earlier analyzers.
        Return the updated IR without removing existing relations.
        """
        raise NotImplementedError


class AnalyzerPipeline:
    """Run analyzers sequentially, passing each the complete updated IR."""

    def __init__(self, analyzers: list[Analyzer]):
        self._analyzers = list(analyzers)

    def run(
        self,
        document_ir: DocumentIR
    ) -> DocumentIR:
        logger.debug("running %d analyzers", len(self._analyzers))
        completed: set[str] = set()
        for analyzer in self._analyzers:
            missing = set(analyzer.requires) - completed
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(
                    f"Analyzer {analyzer.name!r} requires earlier analyzer(s): {names}"
                )
            document_ir = analyzer.run(document_ir)
            completed.add(analyzer.name)
        return document_ir
