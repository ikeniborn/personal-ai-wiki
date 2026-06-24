# Database Layer

## Overview

The `db` package defines SQLAlchemy 2.0 `Mapped[...]` models on `db/base.py::Base`, using UUID PKs (`gen_random_uuid()`), `timezone=True` timestamps and Postgres types (`CITEXT`, `JSONB`, `UUID`, `Enum`). Each aggregate has a thin repo under `db/repos/` that only queries/persists and **never commits** — the [[services#The commit-boundary rule]] owns commit. `session.py` exposes lazy process-global engine/sessionmaker singletons; `managed.py` adds the `chunks.embedding vector(dim)` + HNSW index at runtime (not in alembic). Alembic holds the SQL baseline.

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

- `get_engine()` builds `create_async_engine(database_url, pool_pre_ping=True)` once.
- `get_sessionmaker()` builds `async_sessionmaker(engine, expire_on_commit=False)` once.
- `get_session()` is an async-generator dependency yielding a session per request/op.
- Tests reset these globals to `None` (mirror that for any new cached global).

## Managed chunks columns

`chunks.embedding` is **not** in alembic and **not** ORM-mapped — its `vector(dim)` width depends on the configured embedding provider, so `db/managed.py` creates it at runtime. See [[vector#Managed embedding column]].

- `ensure_embedding_column(session, dim)` — `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(dim)` + `CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ... USING hnsw (embedding vector_cosine_ops)`.
- `rebuild_embedding_column(session, dim)` — destructive: drops index+column, re-adds at the new `dim`, recreates the HNSW index; existing embeddings are lost and chunks must be re-embedded (see [[vector#Reindex]]).
- `embedding_dim(session)` — reads the live width from `pg_attribute.atttypmod`.
- `embedding_version` (an ORM-mapped int, default `1`) marks which provider/version embedded each chunk; reindex re-embeds rows whose version lags the target.

## Alembic migrations

Alembic owns the structural baseline; `alembic/env.py` runs async (`create_async_engine`) with `target_metadata = Base.metadata`. The `init` container runs `alembic upgrade head` before `api`/`worker` start (see [[architecture#Two processes, one image]]).

- `0001_baseline` — extensions (`vector`, `pgcrypto`, `citext`), enum types, and the core tables (`users`, `api_keys`, `app_settings`, `domains`, `blobs`, `sources`, `articles`, `article_revisions`, `audit_log`).
- `0002_phase2_ingest` — `job_status` enum + `entities`, `article_entities`, `links`, `citations`, `chunks` (incl. `tsv tsvector` + GIN index, **no** `embedding`), `chunk_entities`, `jobs`.
- `0003_phase4_chat` — `chat_sessions`, `chat_messages` + their indexes.
- `0004_phase5_backlink_index` — `ix_links_dst_article_id` for backlink lookups.
- Tests apply this baseline once per session against a `pgvector/pgvector:pg16` container.
