---
title: "Phase 7 — Query-cache + suggestions"
phase: 7
status: design
date: 2026-06-22
depends_on: [3, 6]
---

# Phase 7 — Query-cache + suggestions

**Goal / vertical value:** cut LLM + retrieval load by reusing answers. A domain-shared
cache serves repeated/semantically-near queries without the LLM; article writes eagerly
mark dependent entries stale; stale hits are served with a flag + Refresh; as-you-type
suggestions surface the team's popular queries (FAQ effect).

See `…paw-00-overview-design.md`. References point into LLD (`§6/§7`).

## In scope

- **DB (LLD §2):** `query_cache` (`domain_id`, `query_norm`, `query_embedding vector(dim)`,
  `answer_md`, `refs`, `passages`, `model`, `prompt_version`, `stale`, `hit_count`,
  `last_hit_at`; `UNIQUE(domain_id, query_norm)`; HNSW on `query_embedding`; index
  `(domain_id, stale)`). `query_cache_articles` (`cache_id`, `article_id`, `rev`; index
  `article_id`). Created with the current embedding `dim`.
- **Lookup (LLD §6, before retrieval):** exact-norm fast-path on `query_norm`
  (lower/trim/collapse-ws), then **semantic ANN** on `query_embedding`
  (cosine ≥ `sim_threshold`). **Fresh hit → return `answer_md` + `refs` without LLM.**
  Miss → full Phase 3 path → `upsert` into cache with dependencies (`query_cache_articles`:
  cited `article_id` + `rev`).
- **Eager stale-marking (LLD §7):** implement the stale hook seam exposed by Phase 2/6 —
  an article write (ingest/fix/format) sets `query_cache.stale=true` for entries depending
  on that `article_id`, in the same upsert transaction.
- **Stale handling (LLD §6):** stale hit → return with a "may be outdated" flag + a
  **Refresh** action (`POST …/query?refresh=1` bypasses + recomputes + clears `stale`),
  optionally a background refresh.
- **Suggestions (LLD §6/§9):** `GET /domains/{id}/suggest?q=` — as-you-type FTS/ANN over
  `query_norm`, ranked by `hit_count` (team-shared).
- **GC (LLD §7):** extend `gc_housekeeping` with TTL cleanup of expired cache entries.
- **Web UI:** Query screen gains the **suggestions dropdown** (`hx-get /suggest`, 300ms
  delay) and the **stale badge + Refresh** button on cached answers.

## Out of scope (deferred)

Chat is **never** cached (LLD §6) · scheduled GC cron (backlog) · reranking (backlog).

## Data model touched

Adds `query_cache` + `query_cache_articles`. Reads `articles`/`article_revisions` for
dependency revs. Cascade: `domain → query_cache`; `article → query_cache_articles`;
`query_cache → query_cache_articles`.

## Key flows (LLD §12)

- **Query w/ cache:** cache-lookup (exact + ANN) before retrieval → fresh hit returns
  without LLM; miss → Embed → hybrid+RRF → BFS → assemble → answer → upsert cache (+deps).
- **Freshness:** article write marks dependent entries `stale=true`; on read, fresh →
  return; stale → return + flag + refresh. TTL cleanup in `gc_housekeeping`.

## Config (LLD §10)

`query_cache` block: `enabled`, `sim_threshold`, `ttl`, `suggest_top_k`. Global +
per-domain.

## Security

Cache is per-domain (no cross-domain leakage). Suggestions are team-shared within a domain
by design. Cached content is sanitized on render like any answer.

## Acceptance criteria (verifiable)

1. A repeated identical query is served from cache (exact-norm) with no LLM call
   (verified via stub-LLM call count = 0 on the second request).
2. A paraphrased query within `sim_threshold` hits via ANN; below threshold it misses and
   recomputes.
3. Editing a cited article (ingest/fix/format) sets `stale=true` on dependent entries in
   the same transaction.
4. A stale hit returns the cached answer flagged "may be outdated"; Refresh recomputes,
   updates the entry, and clears `stale`.
5. `GET /suggest?q=` returns popular matching queries ranked by `hit_count`.
6. `gc_housekeeping` removes entries past TTL.

## Tests (LLD §11)

- **Unit:** query normalization; sim-threshold decision; dependency extraction from refs.
- **Integration (testcontainers + stub-LLM):** exact + ANN lookup; mark-stale on article
  write (transactional); TTL GC; LLM-call-count asserts hit vs miss.
- **API (httpx):** query cache hit/miss, `?refresh=1`, `/suggest`.
- **E2E:** query → cached → edit cited article → stale flag → refresh → fresh.

## Risks / notes

- Stale-marking must be transactional with the article upsert (LLD §7) — reuse the seam,
  don't add a separate eventual path.
- `query_embedding` is dim-locked like `chunks.embedding`; a dim change must reindex/clear
  the cache too (tie into the Phase 6 reindex path).
