# Services

## Overview
The `services/` layer holds request-scoped business logic between [[api#App wiring]] and [[db#Repo pattern]]. Each service takes an `AsyncSession`, instantiates its own repos + [[storage#Backends]], and is the **single commit boundary** — repos never commit. Services own articles, chat, query, sources, ingest writes, maintenance/jobs, graph, domains, users, settings/setup, provider-config resolution, retention and a cache seam.

## The commit-boundary rule
A service issues exactly one `session.commit()` per logical operation; repositories and `PostgresStorage` only stage writes. This keeps multi-write operations atomic — e.g. `ArticleService.update` puts a blob, mutates the `Article` row, and adds an `ArticleRevision`, then commits once. Helpers meant to compose into a larger transaction (`ingest_write.upsert_article`, `ProviderSettingsService.persist_provider`) deliberately do **not** commit, leaving the boundary to their caller. See [[architecture#Layered dependencies (no cycles)]].

## How services are wired
Every service's `__init__(self, session)` stores the `AsyncSession` as `self._s` and builds its collaborators from it: repos (`ArticleRepo`, `DomainRepo`, `JobRepo`, …) and, where blobs are involved, `PostgresStorage(session)`. Some take extras — `QueryService`/`ChatService` accept a `fernet_key` and build a `SecretBox`; `ProviderSettingsService` accepts an optional `box`. `with_redis(redis)` injects a Redis handle for retrieval caching. Routers construct services via [[api#Dependency helpers (deps.py)]].

## ArticleService
`articles.py` — CRUD + history for wiki articles. `create`/`update`/`rollback` write markdown to [[storage#Backends]], mutate the `Article`, append an `ArticleRevision` (`origin="user"`), and commit once. `update` enforces optimistic concurrency via `expected_rev`, raising 409 on a stale `current_rev`.

- `get_body` decodes the stored markdown; `get_meta` gathers backlinks, outgoing links, citations and revisions.
- `domain_tree` builds a parent/child tree from typed links via `build_tree`; `slug_map` returns slug→id.

## ChatService
`chat.py` — multi-turn chat over a domain, split into prepare/complete/record so the LLM call sits outside the DB transaction. `resolve_session` reuses an owned `ChatSession` or creates+commits a new one. `prepare_turn` resolves provider + per-domain `WikiConfig`/`RetrievalConfig` overrides, windows history by retention depth, runs [[harness#Retrieve]] `retrieve`, and builds messages (or `None` → don't-know).

- `complete_turn` calls the [[providers#Chat provider]] chat; `record_turn` persists the user+assistant messages with refs/model/usage meta and commits.
- `delete_owned`/`get_owned` enforce per-user ownership (404 otherwise).

## QueryService
`query.py` — one-shot Q&A (no session). `prepare` resolves the provider, merges global + per-domain `RetrievalConfig`, builds chat + embedding providers, runs [[vector#Hybrid search]] retrieval, and returns `Prepared` (messages `None` when no passages). `complete`/`answer` invoke the LLM and map the result to a `QueryAnswer`, falling back to `DONT_KNOW`. Read-only — it never commits.

## SourceService
`sources.py` — source-file uploads. `upload_text`/`upload` validate the upload (`validate_text_upload`/`validate_source_upload`), compute a SHA-256 checksum, store bytes via `PostgresStorage` (`large=` for >256 KiB), create the `Source` row, and commit. `list` returns sources for a domain. See [[security#Uploads]] and [[ingest#Loaders]].

## ingest_write.upsert_article
`ingest_write.py` — a module-level helper (not a class) used by the ingest worker to write an AI-authored article. It puts the markdown blob, looks up an existing `(domain_id, slug)` article, then creates or bumps it plus an `ArticleRevision` (`origin="ai"`), returning `(article, created)`. **It does not commit** — the [[jobs#Worker jobs]] job owns the surrounding transaction.

## MaintenanceService & JobService
Both enqueue background work via [[jobs#Queue]] after committing a `Job` row. `maintenance.py` runs lint/fix/format/reindex: it resolves the per-domain `MaintenanceConfig`, gates each op against `enabled_ops` (422 if disabled), creates the job, commits, then `enqueue_*`. `jobs.py` `JobService` handles `start_ingest`, `init_domain` (builds a structure plan via [[harness#Ops]] and fans out one ingest job per topic), and `cancel`.

## GraphService
`graph.py` — read-only subgraph extraction for the [[graph#Subgraph]] view. `config_for` resolves the effective `GraphConfig` (global ⊕ per-domain `config["graph"]`). `subgraph` validates the root belongs to the domain, clamps `depth` to `[0, max_depth]`, filters `types` against `cfg.link_types`, and delegates to `GraphRepo.subgraph`, returning a `SubgraphPayload`.

## DomainService & UserService
`domains.py` — `DomainService.create` slugifies the name, creates the `Domain` with `source_prefix`/`wiki_prefix`, and commits; `list` enumerates domains. `users.py` — `UserService.create` argon2-hashes the password (`hash_password`) and creates the `User`, committing once; `list` enumerates users. Both are thin wrappers over their repos. See [[security#Passwords]].

## SettingsService & SetupService
`settings.py` — `SettingsService` reads/writes the raw singleton settings JSON blob (`get`/`update`, committing on update). `setup.py` — `SetupService` drives first-run bootstrap: `needs_setup` is true when no users exist; `complete` creates the admin user, seeds the settings row, persists the provider config and ensures the embedding column, all committed atomically in one transaction. See [[architecture#Config layering (env ⊕ DB)]].

## ProviderSettingsService (config resolution)
`provider_settings.py` — the typed accessor over the DB settings blob and the source of global LLM config used by chat, query, graph and maintenance. `get_*` methods deserialize each key (`PROVIDER_KEY`, `WIKI_KEY`, `RETRIEVAL_KEY`, `CHAT_KEY`, `GRAPH_KEY`, `MAINTENANCE_KEY`, `EMBEDDING_KEY`) into [[providers#Config models]], returning defaults when absent. Other services layer per-domain `domains.config` overrides on top of these globals.

- `persist_provider` Fernet-encrypts the API key and stages the write **without** committing; `set_provider`/`update_provider` add the commit (and `update_provider` rebuilds the embedding column + bumps `bump_embedding_version` on a dimension change).
- `get_embedding_version` feeds retrieval cache keys.

## Retention (pure logic)
`retention.py` — no DB, no session: pure functions over `ChatConfig` + per-user `chat_prefs`. `resolve_retention` layers null-aware user prefs onto global defaults to yield a `Retention` (`history_depth`, `max_sessions`, `max_age_days`); `select_sessions_to_prune` picks session ids beyond the recency cap or older than the age cutoff. Used by `ChatService` to window history and by GC to prune old sessions.

## cache_seam (Phase 7 stub)
`cache_seam.py` — `mark_domain_cache_stale(session, domain_id)` is a deliberate no-op seam. Article writers (Fix/Format) call it on every write so Phase 7 can implement query-answer cache invalidation against the `query_cache` table without touching the writer code paths.
