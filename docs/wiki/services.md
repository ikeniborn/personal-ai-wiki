# Services

## Overview
The `services/` layer holds request-scoped business logic between [[api#App wiring]] and [[db#Repo pattern]]. Each service takes an `AsyncSession`, instantiates its own repos + [[storage#Backends]], and is the **single commit boundary** — repos never commit. Services own articles, chat, query, sources, ingest writes, maintenance/jobs, graph, domains, users, settings/setup, provider-config resolution, retention, a query-answer cache (Phase 7), and a cache seam.

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
`query.py` — one-shot Q&A (no session). `prepare` resolves the provider, merges global + per-domain `RetrievalConfig`, builds chat + embedding providers, runs [[vector#Hybrid search]] retrieval, and returns `Prepared` (messages `None` when no passages). `complete`/`answer` invoke the LLM and map the result to a `QueryAnswer`, falling back to `DONT_KNOW`. Read-only — it never commits. The `prepare`/`complete`/`answer` SSE path is **not** cached; callers that want cache-first lookup use `QueryCacheService.lookup` before calling `prepare`, and `QueryCacheService.upsert` after to store the result.

## SourceService
`sources.py` — source registration (files, urls, bulk zips). `upload_text`/`upload` validate the upload (`validate_text_upload`/`validate_source_upload`), compute a SHA-256 checksum, store bytes via `PostgresStorage` (`large=` for >256 KiB), create the `Source` row, and commit. `list` returns sources for a domain. See [[security#Uploads]] and [[ingest#Loaders]].

- `upload_url(*, domain_id, url)` — registers a `url` source: it runs `validate_url` ([[security#SSRF guard]]) up front (host allowlist + IP deny-ranges), stores the URL string itself as the blob (`content_type="text/uri-list"`), and persists a `Source` with `type="url"` and the `url` column set. The page is fetched lazily at ingest time, not here.
- `upload_bulk(*, domain_id, zip_bytes)` — explodes a zip into many sources in one commit: it first runs `inspect_zip` ([[security#Zip guard]]), then re-opens the archive and, per member, skips directories / EPUB `mimetype` / `META-INF/`, validates each body with `validate_source_upload`, **silently skips** members that fail validation or whose checksum was already seen (dedupe within the batch), stores the rest and creates one `Source` each. Returns the created list; the [[api#Sources router]] then fans out one ingest job per source.

## ingest_write.upsert_article
`ingest_write.py` — a module-level helper (not a class) used by the ingest worker to write an AI-authored article. It puts the markdown blob, looks up an existing `(domain_id, slug)` article, then creates or bumps it plus an `ArticleRevision` (`origin="ai"`), returning `(article, created)`. **It does not commit** — the [[jobs#Worker jobs]] job owns the surrounding transaction.

## MaintenanceService & JobService
Both enqueue background work via [[jobs#Queue]] after committing a `Job` row. `maintenance.py` runs lint/fix/format/reindex: it resolves the per-domain `MaintenanceConfig`, gates each op against `enabled_ops` (422 if disabled), creates the job, commits, then `enqueue_*`. `jobs.py` `JobService` handles `start_ingest`, `init_domain` (builds a structure plan via [[harness#Ops]] and fans out one ingest job per topic), and `cancel`.

## GraphService
`graph.py` — read-only subgraph extraction for the [[graph#Subgraph]] view. `config_for` resolves the effective `GraphConfig` (global ⊕ per-domain `config["graph"]`). `subgraph` validates the root belongs to the domain, clamps `depth` to `[0, max_depth]`, filters `types` against `cfg.link_types`, and delegates to `GraphRepo.subgraph`, returning a `SubgraphPayload`.

## DomainService & UserService
`domains.py` — `DomainService.create` slugifies the name, creates the `Domain` with `source_prefix`/`wiki_prefix`, and commits; `list` enumerates domains. `users.py` — `UserService.create` argon2-hashes the password (`hash_password`) and creates the `User`, committing once; `list` enumerates users. Both are thin wrappers over their repos. See [[security#Passwords]] and [[web#Admin UI sections]].

Phase 9c adds four new `UserService` methods, each a single commit boundary:

- `get(user_id)` — loads the `User` by id via `UserRepo`; raises `ProblemError(404)` if not found.
- `set_role(*, user_id, role)` — validates `role ∈ USER_ROLES` (raises `ProblemError(422)` on invalid), then delegates to `UserRepo.set_role` and commits.
- `delete(*, user_id)` — loads the user (404 guard), calls `UserRepo.count_admins` and raises `ProblemError(409)` if the target is the last admin, then delegates to `UserRepo.delete` and commits.
- `set_ui_language(*, user_id, lang)` — validates `lang ∈ ("en", "ru")` (raises `ProblemError(422)`; note: validates against the literal tuple, **not** by importing `paw.api.web.i18n`, to keep `services` a clean lower layer), loads `chat_prefs`, merges `{"ui_language": lang}` into a copy, and calls `UserRepo.set_chat_prefs` then commits.

Supporting `UserRepo` methods added: `set_role(user_id, role)`, `delete(user_id)`, `count_admins()`, `set_chat_prefs(user_id, prefs)`.

## SettingsService & SetupService
`settings.py` — `SettingsService` reads/writes the raw singleton settings JSON blob (`get`/`update`, committing on update). `setup.py` — `SetupService` drives first-run bootstrap: `needs_setup` is true when no users exist; `complete` creates the admin user, seeds the settings row, persists the provider config and ensures the embedding column, all committed atomically in one transaction. See [[architecture#Config layering (env ⊕ DB)]].

## ProviderSettingsService (config resolution)
`provider_settings.py` — the typed accessor over the DB settings blob and the source of global LLM config used by chat, query, graph and maintenance. `get_*` methods deserialize each key (`PROVIDER_KEY`, `WIKI_KEY`, `RETRIEVAL_KEY`, `CHAT_KEY`, `GRAPH_KEY`, `MAINTENANCE_KEY`, `EMBEDDING_KEY`) into [[providers#Config models]], returning defaults when absent. Other services layer per-domain `domains.config` overrides on top of these globals.

- `persist_provider` Fernet-encrypts the API key and stages the write **without** committing; `set_provider`/`update_provider` add the commit. `update_provider` keeps **both** managed vector columns (`chunks.embedding` and `query_cache.embedding`) at the provider dim — a dim change rebuilds whichever column already exists at a stale width, clears stale cache entries, and bumps `bump_embedding_version`.
- `get_query_cache()` deserializes `QUERY_CACHE_KEY` into a `QueryCacheConfig` (returning defaults when absent); see [[providers#Config models]].
- `get_embedding_version` feeds retrieval cache keys.

## Retention (pure logic)
`retention.py` — no DB, no session: pure functions over `ChatConfig` + per-user `chat_prefs`. `resolve_retention` layers null-aware user prefs onto global defaults to yield a `Retention` (`history_depth`, `max_sessions`, `max_age_days`); `select_sessions_to_prune` picks session ids beyond the recency cap or older than the age cutoff. Used by `ChatService` to window history and by GC to prune old sessions.

## cache_seam
`cache_seam.py` — `mark_cache_stale(session, *, domain_id, article_ids)` marks every `query_cache` entry that depends on any of `article_ids` as stale, via `QueryCacheRepo.mark_stale_for_articles`. It runs **inside the caller's write transaction** and only flushes (no commit), so invalidation is atomic with the article write. Article writers (ingest/fix/format) call it after upserting an article; a later read will serve the cached answer with a "may be outdated" flag + Refresh prompt. See [[db#Repo pattern]] for QueryCacheRepo.

## QueryCacheService
`services/query_cache.py` — per-domain query-answer cache introduced in Phase 7. Holds `QueryCacheRepo` + a `SecretBox`; optionally wires Redis for query-embedding caching via `with_redis(redis)`.

- `config(domain_id)` — resolves `QueryCacheConfig` (global ⊕ per-domain `config["query_cache"]`) via `ProviderSettingsService.get_query_cache()`.
- `lookup(*, domain_id, question, cfg)` — checks cache before retrieval: first an exact-norm fast-path (`QueryCacheRepo.get_by_norm`), then semantic ANN (`ann_nearest`) if the embedding column exists, accepted when cosine similarity ≥ `cfg.sim_threshold` (`passes_threshold`). Returns a `CacheHit` (fields: `id`, `answer_md`, `refs`, `passages`, `stale`) or `None` on a miss.
- `upsert(...)` — stores a new or refreshed answer: embeds the question, ensures the `query_cache.embedding` column at the current provider dim, upserts the cache row, then records per-article-revision dependencies via `QueryCacheRepo.set_deps`. Commits once.
- `touch(cache_id)` — increments `hit_count` and sets `last_hit_at = now()` (via `QueryCacheRepo.touch`) then commits; called on a fresh hit to keep the TTL/recency window current.
- `suggest(*, domain_id, q, top_k)` — prefix-based query suggestions from `QueryCacheRepo.suggest`.

## query_cache helpers
`services/query_cache.py` also exposes pure module-level helpers used by `QueryCacheService` and routers:

- `normalize_query(q)` — lowercases, trims, and collapses internal whitespace; produces the exact-match key stored as `query_norm`.
- `passes_threshold(distance, sim_threshold)` — converts pgvector cosine distance (`1 − similarity`) to a similarity float and returns `True` when it meets `sim_threshold`.
- `dep_article_ids(refs)` — extracts deduplicated, order-preserving article UUIDs from an answer's `refs` list; used to build the dependency rows in `upsert`.
