"""
DocumentIR — a semantic layer over raw per-page ExtractionResult.

Design principles (per spec):
1. WRAP, don't replace: raw blocks/tables are untouched. DocumentIR adds
   node ids, relations, and ordering on top. Nothing is deleted or rewritten.
2. One confidence score per relation (no per-signal breakdown, for now).
3. Two dedup kinds: near_exact and semantic, modeled as the same relation
   type (DUPLICATE) with a different `method`. Dedup is same-kind only:
   block<->block or table<->table, never block<->table.
4. Direction contract: source_id = earlier node, target_id = later node,
   always. For DUPLICATE, the later occurrence (target) is the one flagged
   to drop. See Relation docstring for the full contract.
5. Table continuation is tracked at the FRAGMENT level: each per-page
   table fragment keeps its own page-local order_index. table_chain()
   resolves which fragments belong together without collapsing them into
   one position — providers aren't trusted to have handled cross-page
   tables correctly, so the IR keeps fragments addressable individually.
6. Reading order only — no logical sections yet. `Node.section_id` is a
   hook for that later without a schema migration.
7. order_index is a pure field, not mirrored as relations. bbox +
   order_index on each node is already sufficient for reconstruction —
   a node's position doesn't need a redundant edge-list encoding, so
   ReadingOrderAnalyzer produces zero Relations; only genuine
   relationships between distinct nodes (continuation, duplication)
   become relations.
8. Nodes hold NO raw object. `Node.source` is a NodeSource(type, id)
   pointer; raw content is resolved through DocumentIR.resolve(). This
   keeps a single owner for content (ExtractionResult) and lets nodes be
   serialized/compared without dragging raw payloads along.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Union

from ..extraction.base import ExtractedBlock, ExtractedTable, ExtractionResult


# ---------- Node layer ----------

class NodeKind(str, Enum):
    BLOCK = "block"
    TABLE = "table"


@dataclass(frozen=True)
class NodeSource:
    """
    Pointer to the raw object a Node represents: just a kind and an id.

    Resolve it via DocumentIR.resolve(node) (or DocumentIR.raw(node_id))
    rather than holding the raw object on the Node itself.
    """
    type: NodeKind
    id: str                          # ExtractedBlock.id or ExtractedTable.id


@dataclass
class Node:
    """
    A thin, stable-id wrapper that points at one raw ExtractedBlock or
    ExtractedTable via `source` (type + id). It holds no content itself.
    """
    id: str                          # stable id, e.g. "p3_b12" or "p3_t2"
    source: NodeSource
    page: int                        # 1-indexed

    # Reading order: a node's rank in document-level top-to-bottom order.
    # Populated by the structure pass. None until that pass runs.
    order_index: Optional[int] = None

    # Hook for future logical sections (e.g. "introduction", "appendix_a").
    section_id: Optional[str] = None

    # Scratch space for detection passes (cached normalized text, embedding
    # vectors, column signatures). Not authoritative; safe to recompute.
    metadata: dict = field(default_factory=dict)

    @property
    def kind(self) -> NodeKind:
        """Convenience alias: the kind lives on the source pointer."""
        return self.source.type

    @property
    def is_block(self) -> bool:
        return self.source.type == NodeKind.BLOCK

    @property
    def is_table(self) -> bool:
        return self.source.type == NodeKind.TABLE


# ---------- Relation layer ----------

class RelationType(str, Enum):
    TABLE_CONTINUATION = "table_continuation"   # B is the next page-fragment of A (same logical table)
    DUPLICATE = "duplicate"                     # B repeats A's content (block<->block or table<->table only)


class DuplicateMethod(str, Enum):
    NEAR_EXACT = "near_exact"   # normalized string match / edit distance
    SEMANTIC = "semantic"       # embedding similarity or LLM judgment


@dataclass
class Relation:
    """
    A single directed, confidence-scored edge between two node ids.

    DIRECTION CONTRACT (must be honored by every detector):
    - source_id is always the EARLIER node (lower order_index / earlier page).
    - target_id is always the LATER node.
    - For DUPLICATE: target_id is the copy to drop (later occurrence loses).
      canonical_nodes() assumes this direction — reversing it silently flips
      which copy survives.
    - For TABLE_CONTINUATION: target_id is the next page-fragment of the
      same logical table.
    - DUPLICATE additionally requires both ends to share a NodeKind
      (block<->block or table<->table). Block<->table duplicates are out
      of scope by design.
    """
    type: RelationType
    source_id: str
    target_id: str
    confidence: float                # single score, 0.0-1.0
    method: Optional[str] = None     # e.g. DuplicateMethod value, or detector name
    metadata: dict = field(default_factory=dict)


# ---------- Custom Collection Classes (Inheriting from list) ----------
# Query helpers for nodes and relations. Every helper returns the same
# collection type so calls can be chained:
#     ir.nodes.on_page(3).tables().ordered()

class NodeList(list):
    """A list of Nodes with built-in query helpers."""

    def _wrap(self, items: Iterable[Node]) -> "NodeList":
        return NodeList(items)

    # --- lookup ---

    def get(self, node_id: str) -> Optional[Node]:
        """Get a single node by its ID."""
        return next((n for n in self if n.id == node_id), None)

    def by_source(self, source_id: str, kind: Optional[NodeKind] = None) -> Optional[Node]:
        """Find the node that points at a given raw block/table id."""
        return next(
            (n for n in self
             if n.source.id == source_id and (kind is None or n.source.type == kind)),
            None,
        )

    def ids(self) -> list[str]:
        return [n.id for n in self]

    # --- filters by kind ---

    def blocks(self) -> "NodeList":
        """Return nodes that point at blocks."""
        return self._wrap(n for n in self if n.source.type == NodeKind.BLOCK)

    def tables(self) -> "NodeList":
        """Return nodes that point at tables."""
        return self._wrap(n for n in self if n.source.type == NodeKind.TABLE)

    def by_kind(self, kind: NodeKind) -> "NodeList":
        """Return nodes of a specific kind."""
        return self._wrap(n for n in self if n.source.type == kind)

    # --- filters by position ---

    def on_page(self, page: int) -> "NodeList":
        """Return nodes on a specific page (1-indexed)."""
        return self._wrap(n for n in self if n.page == page)

    def on_pages(self, start: int, end: int) -> "NodeList":
        """Return nodes on pages start..end inclusive."""
        return self._wrap(n for n in self if start <= n.page <= end)

    def in_section(self, section_id: str) -> "NodeList":
        """Return nodes assigned to a logical section."""
        return self._wrap(n for n in self if n.section_id == section_id)

    # --- ordering / pass state ---

    def ordered(self) -> "NodeList":
        """
        Nodes sorted by (order_index, page). Nodes with no order_index yet
        sort last, ordered by page only.
        """
        return self._wrap(sorted(
            self,
            key=lambda n: (
                n.order_index if n.order_index is not None else float("inf"),
                n.page,
            ),
        ))

    def unordered(self) -> "NodeList":
        """Return nodes the structure pass has not assigned an order_index to."""
        return self._wrap(n for n in self if n.order_index is None)

    def with_metadata(self, key: str) -> "NodeList":
        """Return nodes whose scratch metadata contains `key`."""
        return self._wrap(n for n in self if key in n.metadata)

    def excluding(self, ids: Iterable[str]) -> "NodeList":
        """Return nodes whose id is not in `ids`."""
        skip = set(ids)
        return self._wrap(n for n in self if n.id not in skip)


class RelationList(list):
    """A list of Relations with built-in query helpers."""

    def _wrap(self, items: Iterable[Relation]) -> "RelationList":
        return RelationList(items)

    # --- filters by type ---

    def by_type(self, relation_type: RelationType) -> "RelationList":
        """Return relations of a specific type."""
        return self._wrap(r for r in self if r.type == relation_type)

    def continuations(self) -> "RelationList":
        """Return TABLE_CONTINUATION relations."""
        return self.by_type(RelationType.TABLE_CONTINUATION)

    def duplicates(self) -> "RelationList":
        """Return DUPLICATE relations."""
        return self.by_type(RelationType.DUPLICATE)

    # --- filters by endpoint ---

    def involving(self, node_id: str) -> "RelationList":
        """Return relations where the node is either source or target."""
        return self._wrap(r for r in self if r.source_id == node_id or r.target_id == node_id)

    def from_node(self, node_id: str) -> "RelationList":
        """Return relations where the node is the source (earlier end)."""
        return self._wrap(r for r in self if r.source_id == node_id)

    def to_node(self, node_id: str) -> "RelationList":
        """Return relations where the node is the target (later end)."""
        return self._wrap(r for r in self if r.target_id == node_id)

    # --- filters by score / origin ---

    def above(self, min_confidence: float) -> "RelationList":
        """Return relations with confidence >= min_confidence."""
        return self._wrap(r for r in self if r.confidence >= min_confidence)

    def below(self, max_confidence: float) -> "RelationList":
        """Return relations with confidence < max_confidence (e.g. for review queues)."""
        return self._wrap(r for r in self if r.confidence < max_confidence)

    def by_method(self, method: Union[str, DuplicateMethod]) -> "RelationList":
        """Return relations produced by a specific method/detector."""
        value = method.value if isinstance(method, Enum) else method
        return self._wrap(r for r in self if r.method == value)

    # --- derived views ---

    def target_ids(self) -> set[str]:
        """Set of all target node ids (for DUPLICATE: the ids flagged to drop)."""
        return {r.target_id for r in self}

    def next_of(self, node_id: str) -> Optional[Relation]:
        """First outgoing relation from `node_id` in this list, or None."""
        return next((r for r in self if r.source_id == node_id), None)

    def sorted_by_confidence(self, descending: bool = True) -> "RelationList":
        return self._wrap(sorted(self, key=lambda r: r.confidence, reverse=descending))


# ---------- Document-level container ----------

@dataclass
class DocumentIR:
    """
    The single semantic representation of a document.

    Wraps one raw ExtractionResult without modifying it. Adds:
      - nodes: id-addressable pointers (NodeSource) over every block/table
      - relations: confidence-scored edges (continuation, duplicate)
      - derived views (reading order, canonical nodes, chunking units)
    """
    source: "ExtractionResult"       # untouched raw extraction
    nodes: NodeList = field(default_factory=NodeList)
    relations: RelationList = field(default_factory=RelationList)

    def __post_init__(self):
        # Wrap plain lists so callers can pass either.
        self.nodes = NodeList(self.nodes)
        self.relations = RelationList(self.relations)

    # ---------- node access ----------

    def resolve(self, node: Union[Node, str]) -> Union["ExtractedBlock", "ExtractedTable", None]:
        """
        Resolve a Node (or node id) to its raw ExtractedBlock/ExtractedTable
        by looking up `node.source.id` in the underlying ExtractionResult.
        Returns None if the node or its raw object can't be found.
        """
        if isinstance(node, str):
            node = self.node(node)
            if node is None:
                return None
        if node.source.type == NodeKind.BLOCK:
            return self.source.blocks.get(node.source.id)
        return self.source.tables.get(node.source.id)

    def raw(self, node_id: str) -> Union["ExtractedBlock", "ExtractedTable", None]:
        """Alias for resolve(node_id), reads better at call sites."""
        return self.resolve(node_id)

    # ---------- derived views ----------

    def reading_order(self) -> NodeList:
        """Nodes sorted by (order_index, page); unordered nodes sort last."""
        return self.nodes.ordered()

    def relations_of(self, node_id: str, type: Optional[RelationType] = None) -> RelationList:
        """All relations touching a node, optionally filtered by type."""
        rels = self.relations.involving(node_id)
        return rels.by_type(type) if type else rels

    def table_chain(
        self,
        start_id: str,
        min_confidence: float = 0.0,
        allowed_ids: Optional[set[str]] = None,
    ) -> list[str]:
        """
        Full chain of fragment ids (A -> B -> C across pages) starting at a
        table fragment, following every TABLE_CONTINUATION edge regardless
        of confidence. Fragments keep their own page-local order_index; no
        rows are merged here.
        """
        hops = self._continuations_by_source()
        chain = [start_id]
        seen = {start_id}
        current = start_id
        while True:
            hop = hops.get(current)
            if hop is None or hop.confidence < min_confidence:
                break
            if allowed_ids is not None and hop.target_id not in allowed_ids:
                break
            if hop.target_id in seen:
                break
            chain.append(hop.target_id)
            seen.add(hop.target_id)
            current = hop.target_id
        return chain
