# API & Web

## Overview

`paw` exposes a thin async FastAPI layer: JSON routers mounted under `/api/v1` (auth, domains, sources, articles, setup, settings, users, api-keys, jobs, query, chat, graph, maintenance) plus an HTMX web UI and an MCP server at `/mcp`. Handlers stay thin — they validate, call a [[services#How services are wired]] method, and serialize. `deps.py` injects the DB session, current user/role, redis and CSRF guards; errors raise `ProblemError` rendered as RFC 9457 problem+json; cursor pagination is a tiny base64 helper. The query router (Phase 7) adds a cache layer and a `suggest` endpoint for as-you-type completions. Phase 8 adds the api-keys router and the [[mcp#Auth & mount]] endpoint.

## App wiring

`main.py::create_app()` builds the `FastAPI` app, installs error handlers, adds a CSP middleware that stamps a strict `Content-Security-Policy` on every response, and registers `GET /health`. Every JSON router is mounted with `prefix="/api/v1"`; the HTMX `web_routes.router` mounts at root and `/static` serves assets. See [[architecture#Layered dependencies (no cycles)]].

- One include loop adds all thirteen API routers under `/api/v1` (auth, domains, sources, articles, setup, settings, users, api-keys, jobs, query, chat, graph, maintenance).
- `app.mount("/mcp", mcp_asgi)` mounts the MCP ASGI app; `app.add_middleware(MCPAuthMiddleware)` wraps the whole app with Bearer api-key auth. `mcp.streamable_http_app()` must be called before `mcp.session_manager` is accessed; the lifespan runs `mcp.session_manager.run()`. See [[mcp#Auth & mount]].
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; object-src 'none'`. See [[security#Headers]].
- `app = create_app()` is the module-level ASGI entrypoint (`uvicorn paw.main:app`).
- Observability endpoints: `GET /health` (liveness), `GET /health?ready=1` + `GET /ready` (readiness: DB + Redis, `503` when degraded), `GET /metrics` (Prometheus). `MetricsMiddleware` records HTTP RED metrics by route template. See [[observability#Health & readiness]].

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

`users.py` (`/users`, admin-only) lists, creates, updates and deletes users; `domains.py` (`/domains`) lists and creates wiki domains. Both are thin wrappers over [[services#DomainService & UserService]], with `require_csrf` + `require_role` on writes. See [[web#API]] for i18n-related endpoints.

- `GET /users` (admin) · `POST /users` (admin) — create with role (`viewer` default).
- `PATCH /users/{user_id}` (admin + CSRF) — body `{"role"}`; `UserService` validates `role ∈ USER_ROLES`, raises `ProblemError(422)` on invalid role; returns `UserOut`.
- `DELETE /users/{user_id}` (admin + CSRF) — 204 on success; `UserService` raises `ProblemError(409)` on last-admin guard, 404 if not found.
- `POST /users/me/ui-language` (CSRF + `current_user`) — body `{"ui_language"}`; validates `lang ∈ ("en", "ru")` in `UserService`; returns 204 + `HX-Refresh: true`. See [[web#UI language switch]].
- `GET /domains` (any role, cursor-paginated) · `POST /domains` (admin/editor).

## Sources router

`sources.py` (`/domains/{domain_id}/sources`) handles file uploads. `POST` reads the `UploadFile`, hands bytes to [[services#SourceService]], which validates and stores them; `UploadRejected` **and** `SsrfRejected` are mapped to a 422 `ProblemError`. Returns `{id, filename, type}`. See [[security#Uploads]].

- `POST /bulk` (201, admin/editor + CSRF) — accepts one zip `UploadFile`, calls `SourceService.upload_bulk` to register every valid member as a source ([[services#SourceService]]), then fans out one ingest job per source via `JobService.start_ingest`. Returns `BulkOut {sources: [{id, filename, type}], job_ids: [...]}`; a bad archive surfaces as a 422. The HTMX bulk-upload form on the domain page posts here.

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

`query.py` (`POST /domains/{domain_id}/query`) answers a single question over one domain via [[services#QueryService]] and the [[harness#Retrieve]] pipeline. The JSON path is now cache-aware (Phase 7): when a matching cache entry exists it is served immediately with no LLM call, and the response includes `stale: bool` and `cached: bool` fields. Pass `?refresh=1` to bypass the cache, recompute, and clear the stale flag. The SSE path (`Accept: text/event-stream`) streams tokens and a `done` event with refs+passages — it reads from the cache too but always writes back a fresh entry after streaming. `DONT_KNOW` is emitted when no context is found.

- `POST /domains/{domain_id}/query` — JSON response: `{answer_md, refs, passages, stale, cached}`; `?refresh=1` forces recompute.
- `GET /domains/{domain_id}/suggest?q=` — as-you-type suggestions ranked by `hit_count`; returns `{suggestions: [str]}`; top-k governed by `cfg.suggest_top_k`. See [[services#QueryService]].

## Chat router

`chat.py` is the multi-turn conversational surface over [[services#ChatService]], with redis-wired streaming. `POST /chat` resolves or creates a session, prepares a turn, and either streams SSE tokens (ending in a `done` event with refs) or returns `ChatResponse`. Sessions are user-owned. See [[harness#Ops]].

- `POST /chat` — ask within a domain/session; SSE or JSON.
- `GET /chat/sessions` — cursor-paginated list of the caller's sessions.
- `GET /chat/{session_id}` — full message history; `DELETE` removes an owned session.

## Graph router

`graph.py` (`GET /graph`) returns a JSON subgraph for the Cytoscape page via [[services#GraphService]] and [[graph#Subgraph]]. Query params: `domain`, `root`, optional `depth` and `types` (CSV link-type allowlist — absent = full allowlist, empty = root only). Response carries `nodes` (`id/slug/title/summary`) and `edges` (`src/dst/type`).

## Api-keys router

`api_keys.py` (`/api/v1/api-keys`) lets users manage personal API keys for MCP access. All write endpoints require `require_csrf`; all endpoints require `current_user` (session auth). The full token is shown exactly once at issue time and never returned again. See [[security#API keys]] and [[services#How services are wired]].

- `POST /api-keys` (201) — body `{scopes: ["read"]}` (default `["read"]`); response `{id, prefix, key, scopes}` — `key` is the full `paw_<prefix>.<secret>` token.
- `GET /api-keys` — list caller's keys as `[{id, prefix, scopes, created_at, last_used, revoked_at}]`; secret never returned.
- `DELETE /api-keys/{key_id}` (204) — revokes a key owned by the caller; 404 if not found or not owned.

## Web UI (HTMX)

`web/routes.py` serves the server-rendered HTMX app from Jinja2 templates: it guards pages on the session cookie (redirecting to `/login` or `/setup`), embeds the `paw_csrf` token into forms, and returns HTML partials that HTMX swaps in. Markdown is rendered with `render_markdown` + `resolve_wikilinks`. See [[security#CSRF]], [[web#page_ctx seam]], and [[web#UI language switch]].

- Pages: `/` dashboard, `/domains/{id}` (article tree + sources), `/domains/{id}/graph` (Cytoscape page seeded with a root), `/articles/{id}`, `/settings`, `/login`, `/setup`.
- **i18n seam:** `page_ctx(request, user, app_settings, **extra)` injects `{"user", "csrf", "ui_lang", "t"}` into every converted page context; `t` is a `functools.partial` bound to the resolved UI language. English-bound globals on `templates.env.globals` keep unconverted routes rendering safely. See [[web#page_ctx seam]].
- **Language switcher:** `base.html` `{% if user %}` block POSTs to `/api/v1/users/me/ui-language`; responds with `HX-Refresh: true`. CSP-safe: `static/app.js` delegated `change` listener calls `form.requestSubmit()` (no inline handlers). See [[web#UI language switch]].
- **One-shot API key reveal:** `POST /api-keys/issue` (root-mounted web route, csrf-guarded) issues a key and renders `_apikey_issued.html` showing the full token once. See [[web#Admin UI sections]] and [[security#API keys]].
- Article tree comes from `ArticleService.domain_tree`; the article page renders body, metadata and history (rollback posts `HX-Refresh`).
- Domain actions `POST /domains/{id}/ingest|lint|format|reindex|fix` start jobs and return the `_job_drawer.html` SSE-wired progress partial; `/lint/{job_id}/results` lists issues with a fix form.
- Query: `/domains/{id}/query` page + `POST` returning a `_query_result.html` answer partial. Phase 7 adds an as-you-type suggestions dropdown driven by `GET /domains/{id}/suggest?q=` via `hx-get` with ~300ms debounce (`_suggestions.html` partial). When the served answer is stale, the result partial renders a "may be outdated" badge and a Refresh form that re-posts with `refresh=1`. Cached markdown is sanitized at render via `render_markdown`.
- Chat: `/chat` and `/chat/{session_id}` pages; `POST /chat` returns a `_chat_turn.html` partial with the new answer and refs.
