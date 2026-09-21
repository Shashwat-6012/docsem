# Design Guide: Semantic Grouping of Extracted Document Content

## Goal

Given raw chunks extracted from a document (tables, paragraphs, headings, etc.,
possibly split across pages), determine which chunks belong to the same
semantic unit — e.g. a table split across 3 pages should be recognized as
**one logical table**, not three unrelated ones.

No LLM required. This is an embedding + similarity/clustering problem.

---

## High-Level Flow

```
[1] Input Extraction
        │
[2] Chunk Normalization
        │
[3] Chunk Typing / Classification
        │
[4] Structural Pre-Filtering (cheap, rule-based)
        │
[5] Serialization (chunk → embeddable text)
        │
[6] Embedding Generation
        │
[7] Similarity Scoring / Clustering
        │
[8] Grouping Decision (thresholding + chaining)
        │
[9] Group Merge & Reconstruction
        │
[10] Validation / Confidence Scoring
        │
[11] Output: Grouped Semantic Units
```

Each stage below: what it does, why it exists, and concrete implementation notes.

---

## Step 1: Input Extraction

**What:** Run your existing extraction step (whatever library/tool you use —
PDF parser, OCR, layout model, etc.) to pull raw content off each page.

**Output of this step should include, per chunk, at minimum:**
- `chunk_id`
- `page_number`
- `chunk_type` (if your extractor already classifies it — table / paragraph / heading / image-caption / unknown)
- `bounding_box` (x/y coordinates) — critical for later adjacency checks
- `raw_content` (text, or structured rows/cols if it's a table)

**Why bounding box matters:** a table ending near the bottom margin of page N
and resuming near the top margin of page N+1 is a strong non-semantic signal
you'll want later — don't discard it at extraction time even if you don't use
it immediately.

---

## Step 2: Chunk Normalization

**What:** Standardize chunk representation into one consistent internal
schema, regardless of extractor quirks.

- Normalize whitespace, encoding issues, broken unicode.
- For tables: enforce a consistent internal representation (e.g. list of
  header strings + list of row lists), even if the extractor gives you HTML,
  markdown, or raw cell coordinates.
- For text: strip layout artifacts (page numbers, running headers/footers)
  that leaked into content.

**Why:** everything downstream assumes one clean schema. Skipping this causes
silent bugs later (e.g. a table with a phantom empty header column).

---

## Step 3: Chunk Typing / Classification

**What:** Confirm/assign a `chunk_type` to every chunk: `table`, `paragraph`,
`heading`, `list`, `caption`, `unknown`.

- If your extractor already tags types reliably, just validate them.
- If not, use simple heuristics first (row/column structure → table; short +
  bold/larger font → heading) before reaching for anything ML-based.

**Why this is its own step:** grouping logic differs by type. You should
never compare a table to a paragraph for continuation purposes — type is a
hard gate before any similarity math happens.

---

## Step 4: Structural Pre-Filtering (cheap, rule-based)

**What:** Before any embedding is computed, eliminate obviously-unrelated
chunk pairs using free, deterministic checks. This is the single highest
leverage step for both speed and accuracy.

Rules depend on type:

**For tables:**
- Column count must match (or be a plausible subset, if headers might repeat differently)
- Page adjacency: `page_b == page_a + 1` (or `+2` if you want to tolerate a skipped blank/image page)
- Header text similarity (exact or near-exact match) — repeated headers across split tables are common in PDF exports

**For paragraphs:**
- Same section/heading ancestry (if you're tracking document structure)
- Page adjacency (less strict — paragraphs can be semantically related even pages apart)

**Why this step exists:** embeddings are the expensive, fuzzy part of this
pipeline. Only ambiguous cases that pass the cheap filters should reach it.
This also reduces false positives — two tables with wildly different column
counts should never be merged regardless of what an embedding says.

---

## Step 5: Serialization (chunk → embeddable text)

**What:** Convert each chunk into a plain string suitable for an embedding
model. Serialization strategy differs meaningfully by type — this is the
part people most often get wrong by treating tables like paragraphs.

**Tables:**
- Header signature: `"Product | Quantity | Price | Total"`
- Boundary rows matter most: for continuation checks, serialize the **last
  row** of the earlier chunk and the **first row** of the later chunk
  separately — you're checking whether content flows, not whether the whole
  tables are similar.

**Paragraphs:**
- Use raw text directly, optionally trimmed to a reasonable length (very
  long paragraphs may need truncation or summarization before embedding,
  depending on your embedding model's token limit).

**Headings:**
- Raw heading text; consider embedding with a bit of following context
  (first line of the section) to disambiguate generic headings like
  "Overview" that recur across a document.

**Why separate functions per type:** a single generic serializer produces
noisy, low-signal embeddings. Type-specific serialization is what makes the
embedding step actually useful.

---

## Step 6: Embedding Generation

**What:** Run the serialized text through an embedding model to get a fixed-length vector.

**Model choice:**
- Local, free, good default: `sentence-transformers` — `all-MiniLM-L6-v2`
  (fast, 384-dim) or `all-mpnet-base-v2` (slower, higher quality)
- Hosted APIs (OpenAI/Cohere/Voyage) if you need higher quality or longer
  context, at the cost of a network dependency and per-call cost (still
  cheap relative to LLM calls)

**Practical notes:**
- Batch your embedding calls — don't embed one chunk at a time in a loop if
  you can batch a page or a document's worth at once.
- Cache embeddings per chunk if your pipeline might re-run on the same
  document — recomputing is wasted cost.

---

## Step 7: Similarity Scoring / Clustering

**What:** Compare embeddings to decide relatedness. Pick the approach based
on the actual question you're answering:

**Pairwise similarity (continuation detection):**
- Use when the question is "does chunk B continue chunk A?"
- Cosine similarity between the two relevant embeddings (e.g. last-row vs
  first-row for tables)
- Only compare chunks that already passed Step 4's structural filter — don't
  do all-pairs comparison across the whole document

**Clustering (topic/section grouping):**
- Use when the question is "which of these N chunks belong together?"
  without predefined pairs
- **Agglomerative (hierarchical) clustering** — good default; doesn't
  require specifying cluster count in advance, works naturally with cosine
  distance
- **HDBSCAN** — better if group sizes vary a lot or some chunks legitimately
  belong to no group (outliers)
- Avoid k-means — requires a fixed cluster count, which you won't know ahead
  of time for arbitrary documents

---

## Step 8: Grouping Decision (thresholding + chaining)

**What:** Turn similarity scores into actual group assignments.

- Set a similarity threshold (start around 0.80–0.85 cosine similarity for
  sentence-transformers models, then tune on real examples — these scores
  are compressed relative to intuition, so don't assume a 0.5 means
  "unrelated")
- **Chain continuations**: don't just check chunk N vs N+1 — if N+1
  continues N, and N+2 continues N+1, keep extending the same group. This is
  what makes the design generalize to tables split across *any* number of
  pages, not just two.
- For clustering-based grouping, this step is just reading off cluster
  labels — no separate thresholding needed if `distance_threshold` was set
  in Step 7.

---

## Step 9: Group Merge & Reconstruction

**What:** Once chunks are grouped, reconstruct the actual merged content.

- **Tables:** concatenate rows in page order, drop repeated header rows
  after the first occurrence, preserve column order/types.
- **Paragraphs/sections:** concatenate text in original document order,
  re-attach the group to its original heading if tracked.

**Why this is separate from Step 8:** grouping decisions and content
reconstruction are different concerns — keep them as separate functions so
you can test/debug them independently (e.g. you can inspect "did I group
these correctly?" before worrying about "did I merge them correctly?").

---

## Step 10: Validation / Confidence Scoring

**What:** Attach a confidence score to each grouping decision, and flag
low-confidence groups for review rather than silently trusting the pipeline.

- Store the similarity score(s) that led to each merge decision alongside
  the output — useful for debugging and for building a "review queue" of
  uncertain cases.
- Spot-check against a small hand-labeled set of known multi-page tables
  from your real documents to calibrate the threshold from Step 8 — don't
  guess this number, measure it.

---

## Step 11: Output

**What:** Final structured output — one entry per semantic group, with:
- The reconstructed content
- The list of original chunk IDs that were merged
- Confidence/score metadata from Step 10
- Original page range spanned

This is what downstream consumers of your pipeline should actually use —
never the raw per-page chunks.

---

## Summary Diagram

```
Extract → Normalize → Classify → Structural Filter → Serialize
   → Embed → Score/Cluster → Threshold+Chain → Merge → Validate → Output
```

## Key Design Principles Recap

1. **Structural/rule-based checks come before embeddings**, not after —
   cheaper and reduces false positives.
2. **Serialization is type-specific** — tables and paragraphs need different
   treatment before embedding.
3. **Chaining, not just pairwise comparison**, is what generalizes the
   solution to N-page splits instead of hardcoding 2-page lookahead.
4. **Grouping and reconstruction are separate steps** — keep them
   independently testable.
5. **Thresholds must be tuned on real data**, not assumed.
