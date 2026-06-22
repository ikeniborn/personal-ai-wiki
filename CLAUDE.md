# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`paw` (Personal AI Wiki) — self-hosted, team-scale RAG wiki. Users upload sources
(md/pdf/docx/html/epub/url/images); an LLM harness extracts topics and generates wiki
articles with entities, links and citations; articles are chunked/embedded for hybrid
retrieval (vector + FTS + graph BFS). Built as **9 vertical phases** (see Docs below).
Phase 1 (walking skeleton) + Phase 2 (ingest) are the current focus.

Stack: Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL 16 +
`pgvector` · Redis + `arq` · Jinja2 + HTMX. One image, two processes (`api` uvicorn,
`worker` arq). Deployed via Docker Compose + Traefik.

## Commands

Dependency management is `uv`; never call `pip`/`pytest` directly — go through `uv run`.

```bash
uv sync --dev                       # install deps + dev group into .venv
uv run ruff check .                 # lint (also: ruff check --fix .)
uv run mypy src                     # type check (strict mode)
uv run pytest -q                    # full test suite
uv run pytest tests/unit -q         # one layer (unit | integration | api | e2e)
uv run pytest tests/unit/test_config.py::test_env_overrides   # single test
uv run pytest -k sanitize           # by keyword
```

CI (`.github/workflows/ci.yml`) runs exactly: `ruff check .` → `mypy src` → `pytest -q`.
All three must pass before a PR merges.

**Tests need a running Docker daemon.** `integration`/`api`/`e2e` layers spin up real
Postgres (`pgvector/pgvector:pg16`) and Redis containers via `testcontainers`. Only the
`unit` layer runs without Docker. The alembic baseline is applied once per session against
the container (`tests/conftest.py::_migrate`); tables are truncated after each test.

### Run locally / migrations

```bash
cp .env.example .env                # then fill SESSION_SECRET (32+ bytes) + FERNET_KEY
docker compose up                   # traefik + postgres + redis + init(migrate) + api + worker
uv run alembic upgrade head         # apply migrations against $DATABASE_URL
uv run alembic revision --autogenerate -m "msg"   # new migration
uv run uvicorn paw.main:app --reload              # api only (needs PG + Redis reachable)
uv run arq paw.worker.WorkerSettings              # worker only
```

Required env (validated by `pydantic-settings` in `config.py`, no defaults): `DATABASE_URL`
(`postgresql+asyncpg://…`), `REDIS_URL`, `SESSION_SECRET`, `FERNET_KEY` (44-char Fernet key).

## Architecture

### Layered dependencies (no cycles)

```
api/web/mcp  →  services  →  db.repos, storage   (+ future: vector, graph, jobs, harness)
                                       ↓
                                  db, config        (leaves)
```

- **`api/routers/*`** — thin FastAPI handlers, mounted under `/api/v1`; `api/web/*` serves the
  HTMX UI; `main.py::create_app()` wires routers, the CSP middleware and `/health`.
- **`services/*`** — business logic. A service owns the `AsyncSession`, instantiates its repo +
  storage, and is the **single commit boundary** (see Atomicity below).
- **`db/repos/*`** — query/persist only; one repo per aggregate (`users`, `domains`, `sources`,
  `articles`, `settings`). Repos **never commit**.
- **`db/models.py`** — SQLAlchemy `Mapped[...]` models on `db/base.py::Base`. UUID PKs,
  `timezone=True` timestamps, Postgres types (`CITEXT`, `JSONB`, `UUID`, `Enum`).
- **`storage/`** — `StorageBackend` Protocol (`base.py`); `PostgresStorage` stores bytes in the
  `blobs` table and returns a `storage_ref` string. The Protocol exists so an object-store
  backend can drop in later; depend on the Protocol, not the concrete class.

### Key conventions

- **Atomicity:** the service layer issues exactly one `session.commit()` per operation. Repos
  and storage must not commit — a stray commit breaks multi-write atomicity (regression fixed
  in `f5fc4cc`). When adding writes, batch them and commit once in the service.
- **Config layering** (`config.py` + DB): `env` ⊕ `app_settings` (global defaults, singleton DB
  row) ⊕ `domains.config` (per-domain) ⊕ `users.chat_prefs` (per-user). Infra/secrets live in
  env; user-tunable settings live in the DB and admin UI.
- **Lazy singletons:** `get_settings()` (`lru_cache`), `deps.py::_redis`, and
  `db/session.py::_engine`/`_sessionmaker` are process-global. Tests reset them (`cache_clear()`,
  set to `None`) in the `wired_settings` fixture — mirror that if you add another cached global.
- **Security** (`security/*`, enforced via `api/deps.py`): Redis-backed server-side sessions
  (cookie `paw_session`, `SameSite=Lax`); `require_role(*roles)` RBAC; CSRF double-submit
  (`require_csrf`, exempt for GET/HEAD/OPTIONS); passwords `argon2`; secrets (LLM key, etc.)
  Fernet-encrypted at rest; uploads validated + HTML sanitized via `nh3`.
- **Errors:** raise `ProblemError(status, title, detail)` — rendered as RFC 9457
  `application/problem+json`. `IntegrityError` is auto-mapped to 409 Conflict.
- **Async everywhere:** all DB/IO is async (`asyncpg`, `redis.asyncio`); `pytest` runs in
  `asyncio_mode = auto` so test functions can be plain `async def`.
- **Worker:** `arq` jobs live in `worker.py::WorkerSettings.functions`; `heartbeat` writes
  `paw:worker:heartbeat` to Redis as a liveness marker.
