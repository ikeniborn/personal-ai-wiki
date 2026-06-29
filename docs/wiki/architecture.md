# Architecture

## Overview

`paw` ships as **one Docker image, two processes** — an `api` (uvicorn) and a `worker` (arq) — backed by Postgres+`pgvector` and Redis. `create_app()` wires routers, a CSP middleware, `MetricsMiddleware`, and the `/health` (liveness/readiness) + `/metrics` endpoints. Code is layered acyclically (api/web → services → repos/storage; worker → jobs → harness → leaves). Config layers env ⊕ DB; lazy process-global singletons are reset per test. Built in vertical phases; observability is centralised in `paw.obs` (see [[observability#Overview]]).

## Two processes, one image

A single image (`Dockerfile`, `build: .` in [docker-compose.yml]) runs two long-lived processes sharing the same code and env: the `api` serves HTTP, the `worker` runs background jobs. Both depend on healthy `postgres` + `redis` and on `init` (alembic migrate) completing first.

- **`api`** — `uvicorn paw.main:app --host 0.0.0.0 --port 8000`, fronted by Traefik (TLS via Let's Encrypt). Its health check hits `/health`.
- **`worker`** — `arq paw.worker.WorkerSettings`, consuming jobs from Redis. See [[jobs#Worker jobs]].
- **`init`** — one-shot `alembic upgrade head`; `api`/`worker` wait on `service_completed_successfully`.
- **Infra** — `traefik` (v3.2 router/TLS), `postgres` (`pgvector/pgvector:pg16`), `redis` (`redis:7-alpine`, appendonly).

The two processes share only Postgres and Redis state; they do not call each other directly. The `api` enqueues jobs onto Redis, the `worker` drains them.

## create_app() wiring

`main.py::create_app()` builds the `FastAPI("Personal AI Wiki", version="0.1.0")` instance and is the single composition root for HTTP. The module-level `app = create_app()` is what uvicorn imports.

It performs, in order:
1. `install_error_handlers(app)` — RFC 9457 `problem+json` responses (see [[api#Errors (problem+json)]]).
2. An `@app.middleware("http")` named `csp` that stamps a strict `Content-Security-Policy` (`default-src 'self'`, `script-src 'self'`, `img-src 'self' data:`) on every response — part of [[security#Headers]].
3. A `GET /health` liveness probe returning `{"status": "ok"}` (used by the compose health check); readiness is split out to `/health?ready=1` and the `/ready` alias (DB + Redis checks, `503` when degraded), and `/metrics` exposes Prometheus exposition — see [[observability#Health & readiness]].
4. Mounts every API router under `/api/v1`: `auth`, `domains`, `sources`, `articles`, `setup`, `settings`, `users`, `jobs`, `query`, `chat`, `graph`, `maintenance` — see [[api#App wiring]].
5. Mounts the HTMX UI router (`api/web/routes.py`) at root and `StaticFiles` at `/static`.
6. Adds `MetricsMiddleware`, which records HTTP RED metrics keyed by the route template — see [[observability#HTTP RED middleware]].

## Layered dependencies (no cycles)

Dependencies flow one way; lower layers never import higher ones, so there are no import cycles. Two top-level entry chains converge on the same leaves (`db`, `config`).

```
api / web        →  services  →  db.repos, storage, vector, graph
worker  →  jobs  →  harness    →  providers, ingest, vector, graph
                       ↓
                  db, config            (leaves)
```

- **api / web** — thin handlers; no business logic.
- **services** — request-scoped logic and the single commit boundary (see [[services#The commit-boundary rule]]).
- **db.repos** — query/persist per aggregate, never commit (see [[db#Models and tables]]).
- **storage** — `StorageBackend` Protocol; depend on the Protocol, not `PostgresStorage` (see [[storage#Backends]]).
- **harness** — the agentic tool-calling loop the worker drives (see [[harness#The agentic loop]]).
- **leaves** — `config.py` and `db/session.py` are imported by everything and import nothing internal.

## Config layering (env ⊕ DB)

Configuration is layered: immutable infra/secrets live in env, everything user-tunable lives in the database and admin UI. The env layer is `config.py::Settings` (a `pydantic-settings` `BaseSettings`, `env_file=".env"`); DB layers override per scope.

The precedence, narrowest wins:

```
env  ⊕  app_settings.config  ⊕  domains.config  ⊕  users.chat_prefs
```

- **`env`** — `Settings`: `database_url`, `redis_url`, `session_secret`, `fernet_key` (no defaults, all required), plus byte/TTL limits (`max_upload_bytes`, `max_request_bytes`, `session_ttl_seconds`) and the Phase-9b hardening knobs (`url_allowlist` comma-separated host suffixes, `max_url_bytes`, `max_unzip_bytes`, `max_unzip_entries`, `max_compression_ratio`) consumed by the [[security#SSRF guard]] and [[security#Zip guard]].
- **`app_settings.config`** — global defaults, a singleton DB row.
- **`domains.config`** — per-domain overrides.
- **`users.chat_prefs`** — per-user chat preferences.

DB layers are read through `ProviderSettingsService` and returned as typed models from `providers/config.py` — see [[providers#Config models]] and [[services#SettingsService & SetupService]].

## Lazy process-global singletons

Expensive, process-wide resources are created once on first use and cached in module globals, so each process holds a single engine, sessionmaker and Redis client. Because they are process-global, tests must reset them to avoid bleed across cases.

- `config.py::get_settings()` — `@lru_cache`, returns one `Settings`.
- `db/session.py::_engine` / `_sessionmaker` — lazily built by `get_engine()` / `get_sessionmaker()`; `expire_on_commit=False`, `pool_pre_ping=True`. `get_session()` yields a session from the sessionmaker (see [[db#Async sessions and singletons]]).
- `deps.py::_redis` — the shared `redis.asyncio` client (see [[api#Dependency helpers (deps.py)]]).
- `worker.py::_LazyRedisSettings` — a descriptor that builds `arq` `RedisSettings` from `get_settings().redis_url` on attribute access, so the URL is read lazily, not at import.

**Test reset:** the `wired_settings` fixture clears `get_settings.cache_clear()` and sets `_engine` / `_sessionmaker` / `_redis` back to `None` after pointing env at the test containers. Mirror this if you add another cached global.

## Tech stack

Python 3.12, managed with `uv`; async top to bottom. All DB/IO is async (`asyncpg`, `redis.asyncio`) and `pytest` runs in `asyncio_mode = auto`, so tests are plain `async def`.

- **Web** — FastAPI (≥0.115) + uvicorn; Jinja2 + HTMX UI with vendored Cytoscape for the graph page.
- **Data** — async SQLAlchemy 2.0 (`Mapped[...]`), `alembic` migrations, PostgreSQL 16 + `pgvector`.
- **Jobs** — Redis (`redis>=5.2`) + `arq` task queue (see [[jobs#Worker jobs]]).
- **LLM** — `openai` SDK against any OpenAI-compatible endpoint (see [[providers#Config models]] and [[harness#The agentic loop]]).
- **Ingest** — `pymupdf` (pdf), `mammoth` (docx), `trafilatura` + `markdownify` (html), `mistune` (md) — see [[ingest#Loaders]].
- **Security/util** — `argon2-cffi` (passwords), `cryptography` (Fernet secrets), `nh3` (HTML sanitize), `python-multipart`, `email-validator` (see [[security#Headers]]).
- **Tooling** — `ruff` (pinned `0.15.18`, selects `E,F,I,UP,B`, line length 100), `mypy --strict`, `pytest` + `testcontainers` (real Postgres/Redis), `httpx`.

## Vertical-phase build status

The system was built as **vertical phases**, each a working end-to-end slice rather than a horizontal layer; specs/plans live under `docs/superpowers/`.

- **Phases 1–6 — merged:** (1) walking skeleton, (2) ingest, (3) retrieval/query, (4) chat, (5) graph + article editing, (6) maintenance (lint/fix/format/reindex). The `worker.py` functions — `ingest_domain`, `lint_domain`, `fix_issues`, `format_articles`, `reindex_domain`, `gc_housekeeping`, plus `heartbeat` and startup `reconcile_jobs` — reflect this scope.
- **Phase 7 — merged:** query cache (see [[services#QueryCacheService]]).
- **Phase 8 — merged:** read-only MCP server + api-keys (see [[mcp#Overview]]).
- **Phase 9 — in progress:** ops + hardening, split into sub-plans; **9a observability** (this `paw.obs` work — metrics, health split, LLM cost, Langfuse, compose profile) is implemented (see [[observability#Overview]]). **9b hardening** (SSRF guard, zip guard, URL loader, vision, bulk) is implemented (see [[security#SSRF guard]]). **9c admin-ui/i18n** (user management, API-key issuance UI, UI language switcher) is implemented (see [[web]]). Sub-plan 9d (backups/deploy) follows.
- **Phase 10 — design-only:** Apache AGE + GraphRAG — spec exists, no code yet.
