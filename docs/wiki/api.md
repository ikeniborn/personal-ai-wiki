# API & Web

## Overview

`paw` exposes a thin async FastAPI layer: JSON routers mounted under `/api/v1` (auth, domains, sources, articles, setup, settings, users, jobs, query, chat, graph, maintenance) plus an HTMX web UI. Handlers stay thin — they validate, call a [[services#How services are wired]] method, and serialize. `deps.py` injects the DB session, current user/role, redis and CSRF guards; errors raise `ProblemError` rendered as RFC 9457 problem+json; cursor pagination is a tiny base64 helper.

## App wiring

`main.py::create_app()` builds the `FastAPI` app, installs error handlers, adds a CSP middleware that stamps a strict `Content-Security-Policy` on every response, and registers `GET /health`. Every JSON router is mounted with `prefix="/api/v1"`; the HTMX `web_routes.router` mounts at root and `/static` serves assets. See [[architecture#Layered dependencies (no cycles)]].

- One include loop adds all twelve API routers under `/api/v1`.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'`.
- `app = create_app()` is the module-level ASGI entrypoint (`uvicorn paw.main:app`).

## Dependency helpers (deps.py)

`deps.py` holds the FastAPI dependency-injection primitives every handler reuses. `db()` yields an `AsyncSession`; `current_user` resolves the session cookie against the redis-backed `SessionStore`; `require_role(*roles)` is the RBAC gate; `require_csrf` enforces double-submit on writes. See [[security#Sessions]] and [[security#CSRF]].

- `get_redis()` — lazy process-global `redis.asyncio` client (mirrors the cached-singleton pattern in [[architecture#Lazy process-global singletons]]).
- `get_session_store()` — wraps redis with `session_ttl_seconds`.
- `current_user` — reads `paw_session` cookie, looks up the id via `SessionStore`, loads `User` via `UserRepo`, else raises 401.
- `require_role(*roles)` — depends on `current_user`, raises 403 unless `user.role` is allowed.
- `require_csrf` — no-op for GET/HEAD/OPTIONS; otherwise compares `paw_csrf` cookie vs `x-csrf-token` header via `verify_token`.

## Errors (problem+json)

`errors.py` defines `ProblemError(status, title, detail, type_)` and `install_error_handlers(app)`, which serialize every `ProblemError` to an RFC 9457 `application/problem+json` body (`{type, title, status, detail?}`). A second handler maps SQLAlchemy `IntegrityError` to a 409 Conflict ("resource already exists"), so unique-constraint violations surface as clean conflicts. See [[api#Errors (problem+json)]].

- Handlers across routers raise `ProblemError` directly (401 unauthorized, 403 forbidden, 404 not found, 422 upload/validation).

## Pagination

`pagination.py` is an opaque-cursor helper: `encode_cursor(sort_value, ident)` base64-urlsafe-encodes `"{sort_value}|{ident}"`, and `decode_cursor` reverses it, raising `ValueError("invalid cursor")` on malformed input. Routers build the cursor from the last row's sort key plus its id.

- Used by `domains` (created-at cursor) and `chat` session listing (`last_active_at` cursor); each over-fetches `limit+1` to compute `next_cursor`.

## Auth router

`/api/v1/auth` (`auth.py` + schemas in `api/auth.py`) issues and clears the login session. `POST /login` verifies the email/password (argon2 `verify_password`), creates a server-side session, sets `paw_session` (HttpOnly) and `paw_csrf` cookies, and returns `{id, email, role}`. `POST /logout` deletes the session and both cookies. See [[security#Passwords]].

## Setup & settings routers

`setup.py` bootstraps the first admin and the LLM provider on a fresh install; `settings.py` manages global app settings and the provider connection (admin-only). Both delegate to [[services#SettingsService & SetupService]] / [[services#SettingsService & SetupService]] and [[services#ProviderSettingsService (config resolution)]].

- `GET /setup/status` → `{needs_setup}`; `POST /setup` creates the admin + provider config.
- `GET/PUT /settings` — read/update global `app_settings` (admin, CSRF-guarded write).
- `POST /settings/provider` — set base URL, API key, chat/embedding/vision models + `embedding_dim`.

## Users & domains routers

`users.py` (`/users`, admin-only) lists and creates users; `domains.py` (`/domains`) lists and creates wiki domains. Both are thin wrappers over [[services#DomainService & UserService]] / [[services#DomainService & UserService]], with `require_csrf` + `require_role` on writes.

- `GET /users` (admin) · `POST /users` (admin) — create with role (`viewer` default).
- `GET /domains` (any role, cursor-paginated) · `POST /domains` (admin/editor).

## Sources router

`sources.py` (`/domains/{domain_id}/sources`) handles file uploads. `POST` reads the `UploadFile`, hands bytes to [[services#SourceService]], which validates and stores them; an `UploadRejected` is mapped to a 422 `ProblemError`. Returns `{id, filename, type}`. See [[security#Uploads]] and [[security#Uploads]].

## Articles router

`articles.py` is the wiki CRUD surface backed by [[services#ArticleService]], with optimistic-concurrency revisions. Markdown is rendered server-side via `render_markdown(resolve_wikilinks(...))` using the domain slug map. See [[services#The commit-boundary rule]].

- `POST /domains/{domain_id}/articles` (201) — create from `{slug, title, markdown}`.
- `GET /articles/{article_id}` — returns `ArticleDetail` with rendered `html`.
- `PUT /articles/{article_id}` — update guarded by `expected_rev` (optimistic lock).
- `POST /articles/{article_id}/rollback` — restore a prior `rev_no`.

## Jobs router

`jobs.py` enqueues and observes background work via [[services#MaintenanceService & JobService]] and [[jobs#Queue]]. Long operations return `202 Accepted` with a `job_id`; progress streams over SSE. See [[jobs#Progress]].

- `POST /domains/{domain_id}/ingest` (202) — start ingest of a source.
- `POST /domains/{domain_id}/init` — seed topics, returns `{topics: [{topic, job_id}]}`.
- `GET /jobs/{job_id}` — status/kind/article_id/error/log; 404 if missing.
- `GET /jobs/{job_id}/events` — `text/event-stream` progress feed.
- `POST /jobs/{job_id}/cancel` (202) · `POST /admin/gc` (202, admin) — enqueue housekeeping.

## Maintenance router

`maintenance.py` runs domain-wide upkeep through [[services#MaintenanceService & JobService]], each returning `202` + `job_id`. Covers `lint` (find link/structure issues), `fix` (repair selected `issue_ids`), `format`, and `reindex` (rebuild embeddings/FTS). See [[harness#Ops]] and [[vector#Reindex]].

- `POST /domains/{domain_id}/lint` · `/fix` · `/format` · `/reindex`.

## Query router

`query.py` (`POST /domains/{domain_id}/query`) answers a single question over one domain via [[services#QueryService]] and the [[harness#Retrieve]] pipeline. When the request `Accept`s `text/event-stream` it streams tokens over SSE, then a final `done` event with refs+passages; otherwise it returns `QueryResult` (`answer_md`, `refs`, `passages`). `DONT_KNOW` is emitted when no context is found.

## Chat router

`chat.py` is the multi-turn conversational surface over [[services#ChatService]], with redis-wired streaming. `POST /chat` resolves or creates a session, prepares a turn, and either streams SSE tokens (ending in a `done` event with refs) or returns `ChatResponse`. Sessions are user-owned. See [[harness#Ops]].

- `POST /chat` — ask within a domain/session; SSE or JSON.
- `GET /chat/sessions` — cursor-paginated list of the caller's sessions.
- `GET /chat/{session_id}` — full message history; `DELETE` removes an owned session.

## Graph router

`graph.py` (`GET /graph`) returns a JSON subgraph for the Cytoscape page via [[services#GraphService]] and [[graph#Subgraph]]. Query params: `domain`, `root`, optional `depth` and `types` (CSV link-type allowlist — absent = full allowlist, empty = root only). Response carries `nodes` (`id/slug/title/summary`) and `edges` (`src/dst/type`).

## Web UI (HTMX)

`web/routes.py` serves the server-rendered HTMX app from Jinja2 templates: it guards pages on the session cookie (redirecting to `/login` or `/setup`), embeds the `paw_csrf` token into forms, and returns HTML partials that HTMX swaps in. Markdown is rendered with `render_markdown` + `resolve_wikilinks`. See [[security#CSRF]] and [[api#Web UI (HTMX)]].

- Pages: `/` dashboard, `/domains/{id}` (article tree + sources), `/domains/{id}/graph` (Cytoscape page seeded with a root), `/articles/{id}`, `/settings`, `/login`, `/setup`.
- Article tree comes from `ArticleService.domain_tree`; the article page renders body, metadata and history (rollback posts `HX-Refresh`).
- Domain actions `POST /domains/{id}/ingest|lint|format|reindex|fix` start jobs and return the `_job_drawer.html` SSE-wired progress partial; `/lint/{job_id}/results` lists issues with a fix form.
- Query: `/domains/{id}/query` page + `POST` returning a `_query_result.html` answer partial.
- Chat: `/chat` and `/chat/{session_id}` pages; `POST /chat` returns a `_chat_turn.html` partial with the new answer and refs.
