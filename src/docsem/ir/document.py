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
   to drop. See IRRelation docstring for the full contract.
5. Table continuation is tracked at the FRAGMENT level: each per-page
   table fragment keeps its own page-local order_index. table_chain()
   resolves which fragments belong together without collapsing them into
   one position — providers aren't trusted to have handled cross-page
   tables correctly, so the IR keeps fragments addressable individually.
6. Reading order only — no logical sections yet. `IRNode.section_id` is a
   hook for that later without a schema migration.
7. order_index is a pure field, not mirrored as relations. bbox +
   order_index on each node is already sufficient for reconstruction —
   a node's position doesn't need a redundant edge-list encoding, so
   ReadingOrderAnalyzer produces zero IRRelations; only genuine
   relationships between distinct nodes (continuation, duplication)
   become relations.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union
from ..extraction.base import ExtractedBlock, ExtractedTable, ExtractionResult

# Import your existing shared types
# from extraction_types import ExtractedBlock, ExtractedTable, ExtractionResult, BlockType


# ---------- Node layer ----------

class NodeKind(str, Enum):
    BLOCK = "block"
    TABLE = "table"


@dataclass
class IRNode:
    """
    A thin, stable-id wrapper around one raw ExtractedBlock or ExtractedTable.

    This is the ONLY new "content" object in the IR — and it holds no
    content itself. `source` is a direct reference to the original object,
    so nothing is copied or transformed. Think of IRNode as an address,
    not a container.
    """
    id: str                          # stable id, e.g. "p3_b12" or "p3_t2"
    kind: NodeKind
    page: int                        # 1-indexed, denormalized from source.bbox for convenience
    source: Union["ExtractedBlock", "ExtractedTable"]

    # Reading order: a node's rank in document-level top-to-bottom order.
    # Populated by the structure pass. None until that pass runs.
    order_index: Optional[int] = None

    # Hook for future logical sections (e.g. "introduction", "appendix_a").
    # Not populated by any pass yet — reserved so adding sections later
    # doesn't require touching this dataclass again.
    section_id: Optional[str] = None

    # Convenience flags, DERIVED from relations at IR-build time (not
    # authoritative — the relations list is the source of truth; these
    # just save consumers from walking the relation list for common checks).
    # Both point BACKWARD (to the earlier node), matching the direction
    # contract on IRRelation: later occurrence points at the one it repeats
    # or continues, never the other way around.
    is_duplicate_of: Optional[str] = None       # -> earlier node's id, if THIS is the later, droppable copy
    is_continuation_of: Optional[str] = None    # -> earlier fragment's id, if this table fragment continues it

    # Scratch space for detection passes (e.g. cached normalized text,
    # cached embedding vector, cached column signature for tables).
    # Not authoritative content — safe to recompute, safe to ignore.
    metadata: dict = field(default_factory=dict)


# ---------- Relation layer ----------

class RelationType(str, Enum):
    TABLE_CONTINUATION = "table_continuation"   # B is the next page-fragment of A (same logical table)
    DUPLICATE = "duplicate"                     # B repeats A's content (block<->block or table<->table only)


class DuplicateMethod(str, Enum):
    NEAR_EXACT = "near_exact"   # normalized string match / edit distance
    SEMANTIC = "semantic"       # embedding similarity or LLM judgment


@dataclass
class IRRelation:
    """
    A single directed, confidence-scored edge between two node ids.

    This is where all the "intelligence" of the IR lives. Nodes are dumb
    pointers; relations are the analysis output. A consumer can filter
    relations by type/confidence/method without ever touching raw content.

    DIRECTION CONTRACT (must be honored by every detector):
    - source_id is always the EARLIER node (lower order_index / earlier page).
    - target_id is always the LATER node.
    - For DUPLICATE: target_id is the copy to drop (later occurrence loses,
      per spec). canonical_blocks() and IRNode.is_duplicate_of both assume
      this direction — reversing it silently flips which copy survives.
    - For TABLE_CONTINUATION: target_id is the next page-fragment of the
      same logical table.
    - DUPLICATE additionally requires both ends to share a NodeKind
      (block<->block or table<->table). Block<->table duplicates are out
      of scope by design — that's a "restates" relationship, not a dup,
      and isn't modeled yet.
    """
    type: RelationType
    source_id: str                   # earlier node (see direction contract above)
    target_id: str                   # later node
    confidence: float                # single score, 0.0-1.0
    method: Optional[str] = None     # e.g. DuplicateMethod value, or detector name
    metadata: dict = field(default_factory=dict)  # free-form: e.g. {"matched_columns": 4}


# ---------- Document-level container ----------

@dataclass
class ChunkUnit:
    """
    One chunk-ready piece of the document, as returned by
    DocumentIR.chunking_units(). This is the actual answer to "what
    should a chunker treat as one thing" — a chunker consumes a list of
    these directly and should not need to touch IRNode/IRRelation at all.

    For a BLOCK unit: `text` holds the block's content directly.
    For a TABLE unit: `header`/`rows` hold the FULLY MERGED table — if
    the logical table spanned 3 page-fragments, all 3 fragments' rows
    are already concatenated here in the right order. `merged=True`
    tells you this unit is the result of joining >1 fragment, in case
    you want to treat merged vs. single-fragment tables differently
    (e.g. surface merge confidence in a UI, or log low-confidence merges
    for review).

    `node_ids` preserves full provenance — every source IRNode.id that
    contributed to this unit, in order. Nothing is lost; you can always
    walk back to the raw ExtractedBlock/ExtractedTable objects via
    DocumentIR.node(id).source for any id in this list.
    """
    node_ids: list[str]
    kind: NodeKind
    order_index: Optional[int]
    merged: bool = False
    text: Optional[str] = None                    # populated for BLOCK units
    header: Optional[list] = None                 # populated for TABLE units
    rows: Optional[list] = None                   # populated for TABLE units


@dataclass
class DocumentIR:
    """
    The single semantic representation of a document.

    Wraps one or more raw ExtractionResult objects (one per page, or one
    combined multi-page result — whichever your pipeline produces) without
    modifying them. Adds:
      - nodes: id-addressable view over every block/table
      - relations: confidence-scored edges (continuation, duplicate, order)
      - a couple of derived views for convenience (see methods below)
    """
    source: "ExtractionResult"       # untouched raw extraction
    nodes: list[IRNode] = field(default_factory=list)
    relations: list[IRRelation] = field(default_factory=list)

    # --- derived, read-only views (computed on demand, not stored) ---

    def node(self, node_id: str) -> Optional[IRNode]:
        return next((n for n in self.nodes if n.id == node_id), None)

    def reading_order(self) -> list[IRNode]:
        """Nodes sorted by order_index (falls back to page+bbox if unset)."""
        return sorted(
            self.nodes,
            key=lambda n: (
                n.order_index if n.order_index is not None else float("inf"),
                n.page,
            ),
        )

    def relations_of(self, node_id: str, type: Optional[RelationType] = None) -> list[IRRelation]:
        rels = [r for r in self.relations if r.source_id == node_id or r.target_id == node_id]
        if type:
            rels = [r for r in rels if r.type == type]
        return rels

    def table_chain(self, node_id: str) -> list[str]:
        """
        Walk TABLE_CONTINUATION edges from a starting table FRAGMENT node
        and return the full chain of fragment ids in page order
        (A -> B -> C across pages). Each fragment keeps its own
        page-local order_index — this method resolves which fragments
        belong to the same logical table without collapsing them into a
        single position or merging their rows. Row-stitching and
        confidence-threshold decisions are left to the caller (e.g. a
        separate materialize_table(chain) that concatenates rows only for
        hops above whatever confidence bar the caller trusts).
        """
        chain = [node_id]
        current = node_id
        while True:
            nxt = next(
                (r.target_id for r in self.relations
                 if r.type == RelationType.TABLE_CONTINUATION and r.source_id == current),
                None,
            )
            if nxt is None:
                break
            chain.append(nxt)
            current = nxt
        return chain

    def canonical_blocks(self, min_confidence: float = 0.0) -> list[IRNode]:
        """
        Reading-order nodes with later-occurrence duplicates (blocks OR
        tables) filtered out above the confidence threshold — keeps the
        first/earliest occurrence per the direction contract on
        IRRelation. Duplicates below the threshold are kept: the caller
        decided not to trust that call.

        Despite the name, this filters both BLOCK and TABLE kinds; kept
        as one method since "canonical, deduped reading order" is a single
        concept for a consumer regardless of node kind.
        """
        return [
            n for n in self.reading_order()
            if not (
                n.is_duplicate_of
                and any(
                    r.type == RelationType.DUPLICATE
                    and r.target_id == n.id
                    and r.confidence >= min_confidence
                    for r in self.relations
                )
            )
        ]

    def chunking_units(
        self,
        min_continuation_confidence: float = 0.5,
        min_duplicate_confidence: float = 0.5,
    ) -> list["ChunkUnit"]:
        """
        THE method a chunker should actually call. Returns the document
        as a flat, ordered list of chunk-ready units:

        - Every logical table appears ONCE, as a single ChunkUnit holding
          ALL of its fragments' rows already concatenated in the right
          order — a chunker never sees "table, part 2 of 3" as a
          standalone thing to embed on its own. Continuation hops below
          min_continuation_confidence are NOT merged (kept as a separate,
          lower-confidence unit instead) — see `merged` on each unit to
          tell which case you got.
        - Every duplicate block/table at or above
          min_duplicate_confidence is dropped entirely — a chunker never
          re-embeds the same footer 12 times.
        - Order matches reading order, using the position of each unit's
          FIRST fragment (so a merged table sits where it starts, not
          scattered across every page it touched).

        This is the one method whose output should look "obviously
        useful" without the caller re-deriving anything from relations.
        table_chain() and canonical_blocks() remain available for
        lower-level inspection/debugging, but chunking_units() is what a
        downstream pipeline should be built against.
        """
        canonical = self.canonical_blocks(min_confidence=min_duplicate_confidence)
        canonical_ids = {n.id for n in canonical}

        units: list[ChunkUnit] = []
        consumed_table_ids: set[str] = set()

        for n in canonical:
            if n.id in consumed_table_ids:
                continue  # already folded into an earlier unit's chain

            if n.kind != NodeKind.TABLE:
                units.append(
                    ChunkUnit(
                        node_ids=[n.id],
                        kind=NodeKind.BLOCK,
                        text=n.source.content,
                        merged=False,
                        order_index=n.order_index,
                    )
                )
                continue

            # Table: walk its continuation chain, but only fold in hops
            # that clear the confidence bar AND weren't dropped as
            # duplicates above (a "continuation" into a node that was
            # actually a reprinted duplicate shouldn't be merged as data).
            chain = self._confident_chain(n.id, min_continuation_confidence, canonical_ids)
            for cid in chain:
                consumed_table_ids.add(cid)

            fragments = [self.node(cid).source for cid in chain]
            merged_header = fragments[0].header
            merged_rows = [row for frag in fragments for row in frag.rows]

            units.append(
                ChunkUnit(
                    node_ids=chain,
                    kind=NodeKind.TABLE,
                    header=merged_header,
                    rows=merged_rows,
                    merged=len(chain) > 1,
                    order_index=n.order_index,
                )
            )

        return sorted(units, key=lambda u: u.order_index if u.order_index is not None else float("inf"))

    def _confident_chain(
        self, start_id: str, min_confidence: float, canonical_ids: set[str]
    ) -> list[str]:
        """
        Like table_chain(), but stops early at the first hop below
        min_confidence, and never follows a hop into a node that was
        already dropped as a duplicate (canonical_ids gate).
        """
        chain = [start_id]
        current = start_id
        while True:
            hop = next(
                (r for r in self.relations
                 if r.type == RelationType.TABLE_CONTINUATION and r.source_id == current),
                None,
            )
            if hop is None or hop.confidence < min_confidence or hop.target_id not in canonical_ids:
                break
            chain.append(hop.target_id)
            current = hop.target_id
        return chain