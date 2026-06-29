# Database Layer

## Overview

The `db` package defines SQLAlchemy 2.0 `Mapped[...]` models on `db/base.py::Base`, using UUID PKs (`gen_random_uuid()`), `timezone=True` timestamps and Postgres types (`CITEXT`, `JSONB`, `UUID`, `Enum`). Each aggregate has a thin repo under `db/repos/` that only queries/persists and **never commits** — the [[services#The commit-boundary rule]] owns commit. `session.py` exposes lazy process-global engine/sessionmaker singletons; `managed.py` adds the `chunks.embedding vector(dim)` + HNSW index at runtime (not in alembic), and from Phase 7 the parallel `query_cache.query_embedding vector(dim)` + HNSW. Alembic holds the SQL baseline.

## Declarative Base

All models subclass `db/base.py::Base`, a `DeclarativeBase` carrying a `MetaData` with an explicit `NAMING_CONVENTION` (`ix`/`uq`/`ck`/`fk`/`pk` templates). Deterministic constraint names keep alembic diffs and `lint`/`status` output stable.

- `Base.metadata` is `target_metadata` for [[db#Alembic migrations]] (`alembic/env.py`).
- Models live in `db/models.py`; tables register on import (`from paw.db import models`).

## Column conventions

Models use the SQLAlchemy 2.0 `Mapped[...]` / `mapped_column(...)` style with Postgres-native column types imported from `sqlalchemy.dialects.postgresql`.

- **PKs:** `UUID(as_uuid=True)`, `server_default=func.gen_random_uuid()` (pgcrypto).
- **Timestamps:** `DateTime(timezone=True)` (`timestamptz`), `server_default=func.now()`.
- **`CITEXT`** for case-insensitive `users.email` (unique).
- **`JSONB`** (`server_default="{}"`/`"[]"`) for `chat_prefs`, `app_settings.settings`, `domains.config`, `jobs.log`, `audit_log.meta`, `chat_messages.meta`, `api_keys.scopes`.
- **`Enum`** types: `user_role`, `source_status`, `rev_origin`, `job_status` — created as Postgres `ENUM`s in the baseline.

## Models and tables

`db/models.py` maps the core aggregates plus join/audit tables. UUID PKs, `timezone=True` timestamps and FKs with `ON DELETE CASCADE` / `SET NULL` throughout.

- **users / api_keys** — accounts (role enum, argon2 `pw_hash`, `chat_prefs`) and API keys; see [[security#Sessions]].
- **app_settings** — singleton row (`id` Boolean PK, always `TRUE`) holding global config JSONB; see [[db#App settings singleton]].
- **domains** — tenant boundary (`name` unique, `source_prefix`, `wiki_prefix`, `config`).
- **blobs** — `bytea` payloads for the [[storage#Backends]] `PostgresStorage` backend.
- **sources** — uploads (`type`, `checksum`, `status` enum); `UNIQUE(domain_id, checksum)` dedupes per domain.
- **articles / article_revisions** — wiki pages (`UNIQUE(domain_id, slug)`, `current_rev`) and their immutable revisions (`origin` ai|user); body bytes live in storage via `storage_ref`.
- **entities + article_entities + chunk_entities** — named entities (`UNIQUE(domain_id, name)`) and their M:N join to articles/chunks.
- **links** — typed article→article edges (`UNIQUE(src, dst, type)`); see [[graph#Links]].
- **citations** — quote/locator anchoring an article to a `source` (`SET NULL` on source delete).
- **chunks** — retrieval units (`kind`, `ord`, `heading_path`, `text`, `embedding_version`); see [[db#Managed chunks columns]].
- **chat_sessions / chat_messages** — chat history (`last_active_at`, role/content/meta); see [[api#Chat router]].
- **jobs** — background work rows (`kind`, `status` enum, `log`, `heartbeat_at`); see [[jobs#Worker jobs]].
- **audit_log** — append-only action trail; see [[audit#Recorded events]].
- **query_cache** — per-domain answer cache (Phase 7). Columns: `query_norm text`, `answer_md text`, `refs jsonb` (serialized `Ref` list), `passages jsonb` (serialized `Passage` list), `model`, `prompt_version`, `stale bool`, `hit_count int`, `last_hit_at timestamptz`, `created_at timestamptz`. `UNIQUE(domain_id, query_norm)`; index on `(domain_id, stale)`. FK `domain_id → domains ON DELETE CASCADE`. `query_embedding vector(dim)` is a managed column — **not** ORM-mapped (see [[db#Managed chunks columns]]).
- **query_cache_articles** — dependency join between a cache entry and the articles it consumed, carrying `rev int` (the article revision at answer time). Composite PK `(cache_id, article_id)`; both FKs `ON DELETE CASCADE`. An index on `article_id` lets `mark_stale_for_articles` run without a seq-scan. See [[db#Query cache repo]].

## App settings singleton

`AppSettings` is a one-row table: its `id` is a `Boolean` PK defaulting to `true` with a `CHECK (id)` (baseline), so at most one row exists. `SettingsRepo.get()` selects `WHERE id IS TRUE`; `upsert()` updates that row in place or inserts `id=True`.

- Layered config: env ⊕ `app_settings` ⊕ `domains.config` ⊕ `users.chat_prefs` (see [[architecture#Config layering (env ⊕ DB)]]).

## Repo pattern

`db/repos/*` provides one repo per aggregate (`users`, `domains`, `sources`, `articles`, `chunks`, `entities`, `links`, `citations`, `chat`, `jobs`, `settings`). A repo takes an `AsyncSession` in `__init__` and exposes async query/persist methods only.

- **Query/persist only — never commit.** Repos `add`/`flush`/`execute` but issue no `commit`; the [[services#The commit-boundary rule]] commits once per operation so multi-write atomicity holds.
- `flush()` (not commit) emits SQL so `RETURNING`/PK values are available mid-transaction.
- Many read methods return frozen dataclass DTOs (`PassageRow`, `SummaryRow`, `CitationView`, `LinkedArticle`) rather than ORM objects, decoupling callers from the mapping.
- `EntityRepo.upsert` and `ChunkRepo.tag_entity` do read-then-insert idempotency; `tag_*` joins use composite-PK association rows.

## Raw SQL in repos

A few repos drop to `text()` SQL where ORM mapping is awkward — chiefly for columns intentionally **not** ORM-mapped on `Chunk` (`tsv`, `embedding`).

- `ChunkRepo.create` inserts with `to_tsvector('english', :txt)` populating `tsv`; `set_embedding` casts a vector literal into `embedding`.
- `ChunkRepo.fetch_passages`/`fetch_summaries`/`count_stale`/`fetch_stale_batch` use raw SQL joins and `embedding_version` filters feeding [[vector#Reindex]].
- `JobRepo.append_log` does `log = log || CAST(:e AS jsonb)`; `reconcile_stuck` fails stale-heartbeat jobs (see [[jobs#Cancellation & reconcile]]).

## Async sessions and singletons

`db/session.py` holds the async engine and sessionmaker as lazy process-global singletons (`_engine`, `_sessionmaker`), matching the [[architecture#Lazy process-global singletons]] pattern.

- `get_engine()` builds `create_async_engine(database_url, pool_pre_ping=True)` once, with asyncpg `connect_args` for Apache AGE: `server_settings={"search_path": 'ag_catalog,"$user",public'}` (so `cypher()` resolves at connection startup) and `statement_cache_size=0` (the prepared-statement cache collides with AGE's `cypher(cstring)` parse hook). See [[graph#AGE graph engine]].
- `get_sessionmaker()` builds `async_sessionmaker(engine, expire_on_commit=False)` once.
- `get_session()` is an async-generator dependency yielding a session per request/op.
- Tests reset these globals to `None` (mirror that for any new cached global).

## Managed chunks columns

`chunks.embedding` and `query_cache.query_embedding` are **not** in alembic and **not** ORM-mapped — their `vector(dim)` width depends on the configured embedding provider, so `db/managed.py` creates both at runtime. See [[vector#Managed embedding column]].

- `ensure_embedding_column(session, dim)` — `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(dim)` + `CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ... USING hnsw (embedding vector_cosine_ops)`.
- `rebuild_embedding_column(session, dim)` — destructive: drops index+column, re-adds at the new `dim`, recreates the HNSW index; existing embeddings are lost and chunks must be re-embedded (see [[vector#Reindex]]).
- `embedding_dim(session)` — reads the live width from `pg_attribute.atttypmod`.
- `embedding_version` (an ORM-mapped int, default `1`) marks which provider/version embedded each chunk; reindex re-embeds rows whose version lags the target.
- `ensure_query_cache_embedding_column(session, dim)` — same pattern as `ensure_embedding_column` but targets `query_cache.query_embedding vector(dim)` + `ix_query_cache_embedding_hnsw` HNSW index. Called alongside its chunks counterpart at startup.
- `rebuild_query_cache_embedding_column(session, dim)` — **more destructive than the chunks counterpart**: issues `TRUNCATE query_cache CASCADE` before dropping and re-adding the column, because every stored answer embedding is invalidated by a dim change. Existing cache entries must be regenerated on the next query miss.
- `query_cache_embedding_dim(session)` — reads the live width from `pg_attribute.atttypmod` for `query_cache.query_embedding`.

## Query cache repo

`db/repos/query_cache.py::QueryCacheRepo` handles all `query_cache` / `query_cache_articles` I/O. All reads and writes are scoped by `domain_id`; `delete_expired` takes an optional `domain_id` (the housekeeping job calls it once per domain). All methods use raw `text()` SQL because `query_embedding` is not ORM-mapped. Results are returned as frozen `CacheRow` dataclasses; `refs` and `passages` JSONB columns are deserialized with `json.loads` inside the `_row` helper.

- `get_by_norm(domain_id, query_norm)` — exact-match lookup on the `UNIQUE(domain_id, query_norm)` index; returns `CacheRow | None`.
- `ann_nearest(domain_id, query_vector)` — ANN lookup via `query_embedding <=> CAST(:q AS vector) ORDER BY … LIMIT 1`; returns `(CacheRow, dist) | None`. Only considers rows where `query_embedding IS NOT NULL`.
- `upsert(domain_id, query_norm, answer_md, refs, passages, model, prompt_version, query_vector)` — `INSERT … ON CONFLICT (domain_id, query_norm) DO UPDATE` re-stamps `answer_md`, `refs`, `passages`, `model`, `prompt_version`, and resets `stale = false`; then sets `query_embedding` in a follow-up `UPDATE`. Returns the cache entry `UUID`. See [[services#cache_seam]].
- `set_deps(cache_id, deps)` — replaces the `query_cache_articles` rows for a cache entry: deletes existing deps, then bulk-inserts `(cache_id, article_id, rev)` tuples.
- `touch(cache_id)` — increments `hit_count` and sets `last_hit_at = now()` on a cache hit.
- `mark_stale_for_articles(domain_id, article_ids)` — sets `stale = true` on every cache entry whose `query_cache_articles` row references any of the given article IDs; returns the affected row count. Called by [[services#cache_seam]] when articles are updated.
- `suggest(domain_id, q, limit)` — `SELECT query_norm … WHERE query_norm ILIKE :pat ESCAPE '\' ORDER BY hit_count DESC`; LIKE metacharacters (`% _ \`) in `q` are escaped so they match literally. Used for autocomplete.
- `delete_expired(cutoff, domain_id=None)` — `DELETE … WHERE COALESCE(last_hit_at, created_at) < :cutoff` (plus `AND domain_id = :d` when given); the housekeeping job calls it once per domain so per-domain TTL overrides are honored (see [[jobs#Worker jobs]]).

## Alembic migrations

Alembic owns the structural baseline; `alembic/env.py` runs async (`create_async_engine`) with `target_metadata = Base.metadata`. The `init` container runs `alembic upgrade head` before `api`/`worker` start (see [[architecture#Two processes, one image]]).

- `0001_baseline` — extensions (`vector`, `pgcrypto`, `citext`), enum types, and the core tables (`users`, `api_keys`, `app_settings`, `domains`, `blobs`, `sources`, `articles`, `article_revisions`, `audit_log`).
- `0002_phase2_ingest` — `job_status` enum + `entities`, `article_entities`, `links`, `citations`, `chunks` (incl. `tsv tsvector` + GIN index, **no** `embedding`), `chunk_entities`, `jobs`.
- `0003_phase4_chat` — `chat_sessions`, `chat_messages` + their indexes.
- `0004_phase5_backlink_index` — `ix_links_dst_article_id` for backlink lookups.
- `0005_phase7_query_cache` — `query_cache` and `query_cache_articles` tables with their FK constraints and indexes (see [[db#Models and tables]]). **No** `query_embedding` column here — that is added by the managed migration (see [[db#Managed chunks columns]]).
- Tests apply this baseline once per session against a `pgvector/pgvector:pg16` container.
