# Vector & Retrieval

## Overview

The `vector` package turns chunked articles into searchable knowledge. `embed.py` writes chunk embeddings via an [[providers#Embedding provider]]; `embed_cache.py` memoizes query embeddings in Redis. `search.py` runs hybrid retrieval — a pgvector arm plus an FTS arm fused by Reciprocal Rank Fusion, boosted by entity matches and expanded along the [[graph#Traverse]] graph. `managed.py` provisions the `chunks.embedding` column + HNSW index at runtime; `reindex.py` re-embeds in batches.

## Embeddings

`embed.py::embed_and_write` is the write path: it embeds a list of [[ingest#Chunking]] `ChunkSpec`s, then persists each chunk and its vector through `ChunkRepo`, tagging both with an `embedding_version`.

- `vectors = await embedder.embed([s.text for s in specs])` — one batched call to the [[providers#Embedding provider]].
- Per spec it calls `repo.create(...)` then `repo.set_embedding(chunk_id, vector, embedding_version)`; `zip(..., strict=True)` guards spec/vector misalignment.
- Returns the new chunk ids. Used by the ingest op (`harness/ops/ingest.py`), which first calls `ensure_embedding_column` — see [[vector#Managed embedding column]] and [[harness#Retrieve]].

## Embedding cache

`embed_cache.py::embed_query_cached` returns the embedding of a *query* string, served from Redis when present. It is distinct from the Phase 7 answer cache and only caches the query vector, never corpus chunks.

- Key: `paw:qembed:<sha256(model:embedding_version:query)>`; value is the JSON vector, TTL `_TTL_SECONDS = 3600`.
- `redis=None` bypasses the cache and embeds directly. On a miss it embeds, then `redis.set(..., ex=...)`.
- Keying on `model` + `embedding_version` means a provider/version change naturally invalidates stale entries.

## Hybrid search

`search.py::hybrid_search` fuses two ranked lists with Reciprocal Rank Fusion, applies an optional entity-match boost, and returns the top-`n` `Hit`s `(chunk_id, article_id, score)`. Graph-BFS expansion happens in the caller (`harness/retrieve.py`), seeded from these hits.

- **Vector arm** (`vector_arm`): orders chunks in the domain by pgvector cosine distance `c.embedding <=> CAST(:q AS vector)`, filtered to the active `embedding_version`, limit `cfg.k1`. The query vector is rendered by `_vector_literal` (rejects non-finite floats).
- **FTS arm** (`fts_arm`): `websearch_to_tsquery(:cfg::regconfig, :q)` matched against `c.tsv`, ranked by `ts_rank_cd`, limit `cfg.k2`.
- **Fusion** (`rrf_merge`): `score(id) = Σ weightᵢ / (rrf_k + rankᵢ)` over `[(ids, vector_weight), (ids, fts_weight)]`; ties broken by id string for determinism.
- **Entity boost:** when `boost_entity_ids` is given, `ChunkRepo.tagged_with` finds fused chunks carrying those entities and adds `cfg.entity_boost`, then re-sorts. `query_entities` derives them via `match_entity_names` (case-insensitive substring) — see [[graph#Links]].
- The vector arm is **skipped** when `embedding_dim(session) is None` (no managed column yet); FTS still runs, so empty/un-embedded corpora degrade gracefully.

## Managed embedding column

`db/managed.py` owns the `chunks.embedding` column *outside* alembic, because the vector dimension depends on the configured [[providers#Embedding provider]] and is only known at runtime. See [[db#Managed chunks columns]].

- `ensure_embedding_column(session, dim)` runs `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(dim)` then `CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ... USING hnsw (embedding vector_cosine_ops)`. `dim` is validated as a positive int and interpolated (DDL type modifiers cannot bind).
- `rebuild_embedding_column(session, dim)` is the **destructive** path for a dimension change: drop index → drop column → re-add at new `dim` → recreate index; all chunks must then be re-embedded (the warning the settings UI shows).
- `embedding_dim(session)` reads `pg_attribute.atttypmod` for `chunks.embedding` (pgvector stores the dim directly, no VARLENA offset) and returns it or `None`. `hybrid_search` uses this to decide whether to run the vector arm.

## Reindex

`reindex.py::reindex_domain_chunks` re-embeds a domain's chunks in batches up to a `target_version`, advancing each chunk's `embedding_version` as it goes. It is driven by the reindex job (`jobs/tasks.py`), usually after a provider/dimension change.

- `plan_batches(total, batch_size)` splits the stale count into batch sizes (raises on non-positive `batch_size`).
- Each iteration: `fetch_stale_batch` → `embedder.embed(texts)` → `set_embedding(chunk_id, vector, target_version)`; loop ends early when no stale rows remain.
- An optional `on_batch(done, total)` callback reports progress (wired to [[jobs#Progress]]); returns the count re-embedded.
