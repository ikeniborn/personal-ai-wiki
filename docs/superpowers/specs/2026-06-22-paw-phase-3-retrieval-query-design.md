---
title: "Phase 3 — Retrieval / Query (RAG)"
phase: 3
status: design
date: 2026-06-22
depends_on: [2]
---

# Phase 3 — Retrieval / Query (RAG)

**Goal / vertical value:** ask a question against a domain → get a **cited answer**
streamed token-by-token, grounded only in retrieved context; empty context → an honest
"don't know". Adds hybrid retrieval (vector + FTS + graph BFS) and the read-only query op.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## In scope

- **Hybrid search (LLD §6):** `vector/search.py` — vector arm (ANN cosine `embedding <=> q`,
  filtered by `domain_id` + `embedding_version`) + FTS arm
  (`websearch_to_tsquery('simple', q)` + `ts_rank_cd`); **RRF** merge
  `score = Σ weight_i / (rrf_k + rank_i)` → top-N (weights from config).
- **Graph BFS (LLD §6):** `graph/traverse.py` — recursive **outgoing-only**, cycle-safe
  (`CYCLE ... SET`), bounded `max_depth` from seed article ids.
- **Context assembly (LLD §6):** seed passages (slug + `heading_path` + citations) +
  neighbor `summary` chunks + cross-links, **token-budgeted by fused score**.
  **Query→entity** extraction → entity-boost via `chunk_entities`. **Query-embedding
  cache** (Redis/in-process; this is the embedding cache, not the answer cache).
- **Query op (LLD §4):** `harness/ops/query.py` + query prompt; tool-allowlist = **read
  only** (`search_wiki`, `get_article`, `list_articles`); `harness/tools.py:search_wiki`
  implemented here (hybrid + BFS + assembly), reused by MCP in Phase 8. **Empty context →
  "don't know"** (no fabrication).
- **API (LLD §8):** `POST /domains/{id}/query` — sync JSON `QueryResult{answer_md, refs[],
  passages[]}`; **SSE stream** of answer tokens when `Accept: text/event-stream` (via
  `ChatProvider.stream()`).
- **Web UI:** dedicated **Query** screen (frame C, 🔍): query box, streamed answer render
  (progressive + sanitized), **source chips** (refs/passages). As-you-type **suggestions**,
  **stale badge + Refresh**, and answer caching arrive in Phase 7.

## Out of scope (deferred)

Answer cache + suggestions + stale/refresh (Phase 7) · chat threads/history (Phase 4) ·
reindex job (Phase 6) · reranking (backlog) · MCP transport (Phase 8). No new tables.

## Data model touched

Reads `chunks` (vector + tsv), `links`, `entities`/`chunk_entities`, `articles`,
`citations` from Phase 2. No schema changes.

## Key flows (LLD §12)

Query (sync/stream): embed query (cached) → hybrid (vector + FTS → RRF) → BFS expand →
assemble token-budgeted context → LLM answer (streamed) with refs. Same retrieval path
(up to context assembly, no LLM) will back MCP `search_wiki` in Phase 8.

## Config (LLD §10)

`top_k` (k1/k2 arms), RRF weights + `rrf_k`, `bfs_depth`, context token-budget,
entity-boost weight, `fts_regconfig`. Global defaults + per-domain overrides
(`domains.config.retrieval`).

## Security

Read-only op (no write tools in query context). Retrieved passages remain untrusted data
in the prompt (delimiters). No secrets in context.

## Acceptance criteria (verifiable)

1. After ingesting a fixture corpus, a question returns an answer citing specific
   articles + passages (`refs`/`passages` non-empty, pointing at real chunks).
2. A question with no relevant context returns a "don't know" answer and empty refs (no
   hallucinated citation).
3. `Accept: text/event-stream` streams answer tokens incrementally; the same request
   without it returns the full JSON `QueryResult`.
4. Hybrid retrieval blends vector + FTS via RRF (a term-exact query and a paraphrase both
   surface the right chunk); BFS pulls in linked-article context.
5. Entity-boost raises ranking of chunks tagged with a query entity (measurable on a
   crafted fixture).

## Tests (LLD §11)

- **Unit:** RRF merge ranking math; BFS builder (cycle-safe, depth bound); token-budget
  context assembler; query→entity extraction.
- **Integration (testcontainers + stub-LLM):** hybrid search returns expected ordering on
  a seeded corpus; BFS expansion; embedding-version filter excludes stale chunks.
- **API (httpx):** `/query` sync JSON shape; `/query` SSE stream; empty-context path.
- **E2E:** ingest fixtures → query → cited answer; off-topic query → "don't know".

## Risks / notes

- Reranking is a documented extension point (LLD §6, backlog) — leave a seam in assembly.
- Query-embedding cache here is distinct from the Phase 7 `query_cache` answer cache; keep
  the names separate to avoid confusion.
