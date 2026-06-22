---
review:
  plan_hash: eb16ad7239420048
  spec_hash: f634c88ea5571423
  last_run: 2026-06-22
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: WARNING
      section: "Tests (CI)"
      section_hash: de18da5e7281d212
      text: "Spec Tests §CI requires `ruff` + `mypy` + `pytest` green, but no task/step adds a CI config (e.g. a workflow file) or a gating step. Lint/type commands appear only in the Conventions prose (line 92); the CI artifact is unmapped."
      verdict: fixed
      verdict_at: 2026-06-22
      resolution: "Task 1 Step 6 now creates `.github/workflows/ci.yml` running `ruff check` + `mypy src` + `pytest -q`, plus a local gate command; the ci.yml artifact is committed (Step 7). CI requirement now mapped."
    - id: F-002
      phase: coverage
      severity: WARNING
      section: "Web UI (409 banner)"
      section_hash: f2b2a86dde561f4e
      text: "Spec frame-C requires the article page to show a `409 reload banner`. The `.banner` CSS class exists (Task 20 theme), but `article.html` renders only Read/Edit tabs + revisions — no banner element or 409-handling markup, and no web test asserts it."
      verdict: fixed
      verdict_at: 2026-06-22
      resolution: "Task 19 adds CSP-safe `static/app.js` (htmx:responseError -> show #conflict-banner, no inline JS/eval) loaded in base.html; Task 20 `article.html` renders `id=conflict-banner` + an Edit form (hx-put, hidden expected_rev); Task 22 e2e asserts the banner element and hx-put are present."
    - id: F-003
      phase: coverage
      severity: INFO
      section: "API (settings)"
      section_hash: 3f5829d772b4b042
      text: "Task 18 implements GET/PUT /settings but no test asserts the settings round-trip (the only test_settings* are Task-2 env tests). Mirrors spec's accepted F-004; saved-but-unused this phase."
      verdict: accepted
      verdict_at: 2026-06-22
      resolution: "Body unchanged; mirrors the already-accepted spec finding F-004 (Connection fields saved-but-unused this phase). Accepted."
    - id: F-004
      phase: dependencies
      severity: WARNING
      section: "Task 5 / Task 6"
      section_hash: 2b28eaa684713c78
      text: "Task 5 (alembic baseline + integration test) consumes the `pg_container`/`pg_sync_url` fixtures introduced in Task 6 (M=5 uses artifact from N=6, N>M). The plan documents the inversion with an explicit `do Task 6 Step 3 first` callout, so it is mitigated but the strict M<N ordering is violated for the verify substep."
      verdict: fixed
      verdict_at: 2026-06-22
      resolution: "The shared Postgres/migration fixtures (pg_container, pg_async_url, pg_sync_url, _migrate, db_session) were moved into Task 5 as new Step 3b, so Task 5's own test no longer depends on Task 6. Task 6 Step 3 now just reuses them (no conftest change). Strict M<N ordering holds."
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-1-skeleton-design.md
---

# Personal AI Wiki — Phase 1 (Walking Skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boot the whole stack with `docker compose up`, let an admin complete a first-run wizard, log in, create a domain, upload a markdown source, manually author an article, and see it rendered — with **no LLM anywhere**.

**Architecture:** A `src`-layout Python package `paw` exposing one FastAPI app (REST `/api/v1` + server-rendered web `/`) and one arq worker process, sharing PostgreSQL (SQLAlchemy 2.0 async + asyncpg) and Redis (sessions + worker queue). Alembic baseline migration creates the core schema (no vector/chunk tables yet). Auth is server-side Redis sessions with argon2 passwords and RBAC; security baseline is CSRF double-submit, `nh3` sanitization, Fernet secret helper, RFC 9457 errors, cursor pagination. Deployment is Docker Compose behind Traefik with a one-shot alembic init container.

**Tech Stack:** Python 3.12 · uv · FastAPI · SQLAlchemy 2.0 (asyncpg) · Alembic · pydantic-settings · redis.asyncio · arq · Jinja2 + HTMX (vendored) · mistune · nh3 · argon2-cffi · cryptography (Fernet) · pytest + pytest-asyncio · httpx · testcontainers · ruff · mypy · Docker Compose + Traefik.

**Spec:** `docs/superpowers/specs/2026-06-22-paw-phase-1-skeleton-design.md`
**Overview:** `docs/superpowers/specs/2026-06-22-paw-00-overview-design.md`
**LLD reference:** `docs/reports/lld-personal-ai-wiki.html`

---

## File Structure

Created in this phase (the target tree subset from LLD §1; later phases add `harness/`, `ingest/`, `vector/`, `graph/`, `mcp/`, etc.):

```
pyproject.toml                      # uv project, deps, ruff/mypy/pytest config
Dockerfile                          # one image, two entrypoints (api, worker)
docker-compose.yml                  # traefik, api, worker, postgres, redis, init
.env.example                        # env template (DATABASE_URL, REDIS_URL, secrets)
alembic.ini
alembic/
  env.py                            # async alembic env
  versions/0001_baseline.py         # extensions + enums + core tables
src/paw/
  __init__.py
  config.py                         # pydantic-settings (env layer)
  main.py                           # FastAPI app factory
  worker.py                         # arq WorkerSettings + heartbeat task
  db/
    __init__.py
    base.py                         # DeclarativeBase + metadata naming convention
    session.py                      # async engine + session factory + get_session dep
    models.py                       # users, api_keys, app_settings, domains, blobs,
                                    #   sources, articles, article_revisions, audit_log
    repos/
      __init__.py
      domains.py articles.py sources.py users.py settings.py
  storage/
    __init__.py
    base.py                         # StorageBackend Protocol
    postgres.py                     # PostgresStorage (blobs.bytea + Large Object)
  security/
    __init__.py
    passwords.py                    # argon2 hash/verify
    secrets.py                      # Fernet encrypt/decrypt helper
    sanitize.py                     # nh3 allowlist render-sanitize
    csrf.py                         # double-submit token issue/verify
    sessions.py                     # Redis-backed session store
    uploads.py                      # magic-byte + ext + size guard (md/txt)
  audit/
    __init__.py
    log.py                          # write audit_log rows
  services/
    __init__.py
    domains.py articles.py sources.py settings.py users.py setup.py
  api/
    __init__.py
    deps.py                         # current_user, require_role, get_session, csrf
    auth.py                         # login/logout
    errors.py                       # RFC 9457 problem+json handlers
    pagination.py                   # cursor/keyset helpers
    routers/
      __init__.py
      auth.py domains.py sources.py articles.py settings.py users.py setup.py
    web/
      __init__.py
      routes.py                     # server-rendered pages
      templates/                    # Jinja2 (frame C + screens)
      static/                       # vendored htmx + theme css
tests/
  conftest.py                       # pg/redis testcontainers, app client fixtures
  unit/ ...                         # sanitize, passwords, secrets, csrf, pagination, storage
  api/  ...                         # auth, rbac, csrf, domains, sources, articles
  e2e/  test_walking_skeleton.py
```

**Decomposition note:** files are split by responsibility (security primitives are one file each so they are independently testable; repos isolate SQL from services; routers isolate HTTP from services). Files that change together live together (`api/routers/*` next to `api/deps.py`).

---

## Conventions for every task

- **TDD:** write the failing test, run it (see it fail), implement minimally, run it (see it pass), commit.
- **Async everywhere:** SQLAlchemy async sessions, `pytest.mark.asyncio` (set `asyncio_mode = "auto"` so the marker is implicit).
- **Commits:** Conventional Commits, English. Branch already on `master`; commit directly per step (the user committed specs to master).
- **Run tests with:** `uv run pytest ...` and lint with `uv run ruff check .` / `uv run mypy src`.

---

### Task 1: Project scaffold + FastAPI factory + health endpoint

**Files:**
- Create: `pyproject.toml`
- Create: `src/paw/__init__.py`
- Create: `src/paw/main.py`
- Test: `tests/unit/test_health.py`
- Create: `tests/conftest.py` (minimal app-only fixture; DB/Redis fixtures added in Task 6/11)

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "paw"
version = "0.1.0"
description = "Personal AI Wiki"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic-settings>=2.6",
    "redis>=5.2",
    "arq>=0.26",
    "jinja2>=3.1",
    "python-multipart>=0.0.12",
    "mistune>=3.0",
    "nh3>=0.2.18",
    "argon2-cffi>=23.1",
    "cryptography>=43.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "testcontainers[postgres,redis]>=4.8",
    "ruff>=0.7",
    "mypy>=1.13",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/paw"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "src"
explicit_package_bases = true
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_health.py
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


async def test_health_ok():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.main'`.

- [ ] **Step 4: Write minimal implementation**

```python
# src/paw/__init__.py
__version__ = "0.1.0"
```

```python
# src/paw/main.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

```python
# tests/conftest.py
# Shared fixtures grow in later tasks (Task 6 adds Postgres, Task 11 adds Redis).
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_health.py -v`
Expected: PASS.

- [ ] **Step 6: Add CI workflow (ruff + mypy + pytest gating)**

```yaml
# .github/workflows/ci.yml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --dev
      - run: uv run ruff check .
      - run: uv run mypy src
      - run: uv run pytest -q
```

Run (locally, to confirm the same gate is green before pushing):
```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
```
Expected: all three succeed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/paw/__init__.py src/paw/main.py tests/conftest.py tests/unit/test_health.py .github/workflows/ci.yml
git commit -m "feat: scaffold paw package with FastAPI factory, health endpoint and CI"
```

---

### Task 2: Config (pydantic-settings, env layer)

**Files:**
- Create: `src/paw/config.py`
- Create: `.env.example`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config.py
from paw.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/paw")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("FERNET_KEY", "k" * 44)
    s = Settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_url == "redis://redis:6379/0"
    assert s.max_upload_bytes == 10 * 1024 * 1024  # default


def test_settings_missing_required(monkeypatch):
    for k in ("DATABASE_URL", "REDIS_URL", "SESSION_SECRET", "FERNET_KEY"):
        monkeypatch.delenv(k, raising=False)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.config'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    session_secret: str
    fernet_key: str

    # limits (env layer; LLD §10)
    max_upload_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 12 * 1024 * 1024
    session_ttl_seconds: int = 60 * 60 * 24 * 7


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

```bash
# .env.example
DATABASE_URL=postgresql+asyncpg://paw:paw@postgres:5432/paw
REDIS_URL=redis://redis:6379/0
# 32+ byte random string for signing session ids
SESSION_SECRET=change-me-to-a-long-random-string
# Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=change-me-fernet-key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/paw/config.py .env.example tests/unit/test_config.py
git commit -m "feat: add env-layer settings via pydantic-settings"
```

---

### Task 3: DB base + async session

**Files:**
- Create: `src/paw/db/__init__.py`
- Create: `src/paw/db/base.py`
- Create: `src/paw/db/session.py`
- Test: `tests/unit/test_db_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_db_base.py
from paw.db.base import Base


def test_naming_convention_present():
    # Alembic-friendly constraint naming so autogenerate/migrations are stable.
    nc = Base.metadata.naming_convention
    assert nc["pk"] == "pk_%(table_name)s"
    assert "fk" in nc and "ix" in nc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_db_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.base'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/db/__init__.py
```

```python
# src/paw/db/base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

```python
# src/paw/db/session.py
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from paw.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_db_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/__init__.py src/paw/db/base.py src/paw/db/session.py tests/unit/test_db_base.py
git commit -m "feat: add SQLAlchemy declarative base and async session factory"
```

---

### Task 4: Core DB models (LLD §2 subset)

**Files:**
- Create: `src/paw/db/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from paw.db.base import Base
from paw.db import models  # noqa: F401  (registers tables on Base.metadata)


def test_core_tables_registered():
    tables = set(Base.metadata.tables)
    assert {
        "users",
        "api_keys",
        "app_settings",
        "domains",
        "blobs",
        "sources",
        "articles",
        "article_revisions",
        "audit_log",
    } <= tables
    # No vector/chunk tables in Phase 1.
    assert "chunks" not in tables
    assert "entities" not in tables


def test_article_unique_slug_per_domain():
    cols = {c.name for c in Base.metadata.tables["articles"].columns}
    assert {"id", "domain_id", "slug", "title", "storage_ref", "current_rev"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.models'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/db/models.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from paw.db.base import Base

USER_ROLES = ("admin", "editor", "viewer")
SOURCE_STATUS = ("uploaded", "extracted", "ingested", "failed")
REV_ORIGIN = ("ai", "user")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        Enum(*USER_ROLES, name="user_role"), nullable=False, server_default="viewer"
    )
    chat_prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    prefix: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = _created_at()
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSettings(Base):
    __tablename__ = "app_settings"
    # Singleton row (id always TRUE).
    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default="true")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class Domain(Base):
    __tablename__ = "domains"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    wiki_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


class Blob(Base):
    __tablename__ = "blobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("domain_id", "checksum"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*SOURCE_STATUS, name="source_status"), nullable=False, server_default="uploaded"
    )
    created_at: Mapped[datetime] = _created_at()


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("domain_id", "slug"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    current_rev: Mapped[int] = mapped_column(nullable=False, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revisions: Mapped[list[ArticleRevision]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class ArticleRevision(Base):
    __tablename__ = "article_revisions"
    __table_args__ = (UniqueConstraint("article_id", "rev_no"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    rev_no: Mapped[int] = mapped_column(nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    origin: Mapped[str] = mapped_column(Enum(*REV_ORIGIN, name="rev_origin"), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    article: Mapped[Article] = relationship(back_populates="revisions")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


# BigInteger imported for future large-object oid columns (Task 6 uses raw SQL for LO).
_ = BigInteger
_ = String
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/models.py tests/unit/test_models.py
git commit -m "feat: add core SQLAlchemy models (LLD §2 subset)"
```

---

### Task 5: Alembic baseline migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_baseline.py`
- Modify: `tests/conftest.py` (add shared Postgres + migration fixtures, Step 3b)
- Test: `tests/integration/test_migration.py`

> Uses a Postgres testcontainer. This task adds the shared Postgres + migration fixtures to `tests/conftest.py` (Step 3b) so both this test and Task 6's storage tests can use them.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_migration.py
import sqlalchemy as sa
from sqlalchemy import create_engine


def test_baseline_creates_core_tables(pg_sync_url):
    # pg_sync_url: psycopg/sync URL to a fresh container with the baseline applied.
    engine = create_engine(pg_sync_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {"users", "domains", "articles", "article_revisions", "sources",
            "blobs", "api_keys", "app_settings", "audit_log"} <= tables
    assert "chunks" not in tables  # vector tables are Phase 2
    # extensions present
    with engine.connect() as conn:
        exts = {r[0] for r in conn.execute(sa.text("SELECT extname FROM pg_extension"))}
    assert {"vector", "pgcrypto", "citext"} <= exts
    engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_migration.py -v`
Expected: FAIL — fixture `pg_sync_url` missing / migration absent.

- [ ] **Step 3: Write minimal implementation**

```ini
# alembic.ini
[alembic]
script_location = alembic
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
qualname =
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = INFO
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

```python
# alembic/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from paw.config import get_settings
from paw.db.base import Base
from paw.db import models  # noqa: F401  (register tables)

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

```python
# alembic/versions/0001_baseline.py
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE TYPE user_role AS ENUM ('admin','editor','viewer')")
    op.execute("CREATE TYPE source_status AS ENUM ('uploaded','extracted','ingested','failed')")
    op.execute("CREATE TYPE rev_origin AS ENUM ('ai','user')")

    op.execute("""
    CREATE TABLE users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      email citext UNIQUE NOT NULL, pw_hash text NOT NULL,
      role user_role NOT NULL DEFAULT 'viewer',
      chat_prefs jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE api_keys (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      prefix text NOT NULL, hash text NOT NULL, scopes jsonb NOT NULL DEFAULT '[]',
      created_at timestamptz NOT NULL DEFAULT now(), last_used timestamptz, revoked_at timestamptz)
    """)
    op.execute("CREATE INDEX ix_api_keys_prefix ON api_keys(prefix)")
    op.execute("""
    CREATE TABLE app_settings (
      id boolean PRIMARY KEY DEFAULT true CHECK (id), settings jsonb NOT NULL DEFAULT '{}')
    """)
    op.execute("""
    CREATE TABLE domains (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      name text NOT NULL UNIQUE, source_prefix text NOT NULL, wiki_prefix text NOT NULL,
      config jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE blobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      data bytea NOT NULL, content_type text, created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
    CREATE TABLE sources (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      storage_ref text NOT NULL, filename text, type text NOT NULL, url text,
      checksum text NOT NULL,
      status source_status NOT NULL DEFAULT 'uploaded',
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, checksum))
    """)
    op.execute("""
    CREATE TABLE articles (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      slug text NOT NULL, title text NOT NULL, storage_ref text NOT NULL, summary text,
      current_rev int NOT NULL DEFAULT 1,
      updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (domain_id, slug))
    """)
    op.execute("""
    CREATE TABLE article_revisions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      rev_no int NOT NULL, storage_ref text NOT NULL,
      author_id uuid REFERENCES users(id) ON DELETE SET NULL,
      origin rev_origin NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (article_id, rev_no))
    """)
    op.execute("""
    CREATE TABLE audit_log (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid REFERENCES users(id) ON DELETE SET NULL,
      action text NOT NULL, target_type text, target_id uuid,
      meta jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now())
    """)


def downgrade() -> None:
    for t in ("audit_log", "article_revisions", "articles", "sources", "blobs",
              "domains", "app_settings", "api_keys", "users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for e in ("rev_origin", "source_status", "user_role"):
        op.execute(f"DROP TYPE IF EXISTS {e}")
```

- [ ] **Step 3b: Add shared Postgres + migration fixtures to `tests/conftest.py`**

```python
# tests/conftest.py  (append)
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_async_url(pg_container: PostgresContainer) -> str:
    return pg_container.get_connection_url()


@pytest.fixture(scope="session")
def pg_sync_url(pg_container: PostgresContainer) -> str:
    # psycopg2 sync URL for alembic/inspection helpers
    return pg_container.get_connection_url().replace("+asyncpg", "+psycopg2")


@pytest.fixture(scope="session", autouse=True)
def _migrate(pg_async_url: str, monkeypatch_session: pytest.MonkeyPatch) -> None:
    # Apply the alembic baseline once per session against the container.
    monkeypatch_session.setenv("DATABASE_URL", pg_async_url)
    monkeypatch_session.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch_session.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch_session.setenv("FERNET_KEY", "k" * 44)
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session(pg_async_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(pg_async_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic/env.py alembic/versions/0001_baseline.py tests/conftest.py tests/integration/test_migration.py
git commit -m "feat: add alembic baseline migration and shared DB test fixtures"
```

---

### Task 6: Storage backend (Protocol + PostgresStorage) + DB test fixtures

**Files:**
- Create: `src/paw/storage/__init__.py`
- Create: `src/paw/storage/base.py`
- Create: `src/paw/storage/postgres.py`
- Test: `tests/integration/test_storage.py`

> Reuses the `db_session` / Postgres fixtures added to `tests/conftest.py` in Task 5 Step 3b.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_storage.py
import pytest

from paw.storage.postgres import PostgresStorage


async def test_blob_roundtrip(db_session):
    store = PostgresStorage(db_session)
    ref = await store.put(b"hello small", content_type="text/plain")
    assert ref.startswith("blob:")
    assert await store.exists(ref) is True
    assert await store.get(ref) == b"hello small"
    await store.delete(ref)
    assert await store.exists(ref) is False


async def test_large_object_roundtrip(db_session):
    store = PostgresStorage(db_session)
    big = b"x" * (2 * 1024 * 1024)
    ref = await store.put(big, content_type="application/octet-stream", large=True)
    assert ref.startswith("lo:")
    chunks = [c async for c in store.open(ref)]
    assert b"".join(chunks) == big
    await store.delete(ref)


async def test_get_missing_raises(db_session):
    store = PostgresStorage(db_session)
    with pytest.raises(KeyError):
        await store.get("blob:00000000-0000-0000-0000-000000000000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_storage.py -v`
Expected: FAIL — module + `db_session` fixture missing.

- [ ] **Step 3: Reuse the Postgres fixtures from Task 5**

The `pg_container`, `pg_async_url`, `db_session`, and `_migrate` fixtures were added to
`tests/conftest.py` in Task 5 Step 3b. No conftest change is needed here —
`test_storage.py` uses the `db_session` fixture directly.

- [ ] **Step 4: Write minimal implementation**

```python
# src/paw/storage/__init__.py
from paw.storage.base import StorageBackend
from paw.storage.postgres import PostgresStorage

__all__ = ["StorageBackend", "PostgresStorage"]
```

```python
# src/paw/storage/base.py
from collections.abc import AsyncIterator
from typing import Protocol


class StorageBackend(Protocol):
    async def put(self, data: bytes, *, content_type: str | None = None,
                  large: bool = False) -> str: ...
    async def get(self, ref: str) -> bytes: ...
    def open(self, ref: str) -> AsyncIterator[bytes]: ...
    async def delete(self, ref: str) -> None: ...
    async def exists(self, ref: str) -> bool: ...
```

```python
# src/paw/storage/postgres.py
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CHUNK = 1024 * 1024


class PostgresStorage:
    """Small payloads -> blobs.bytea (ref 'blob:<uuid>').
    Large payloads -> PostgreSQL Large Object (ref 'lo:<oid>')."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def put(self, data: bytes, *, content_type: str | None = None,
                  large: bool = False) -> str:
        if large:
            row = await self._s.execute(text("SELECT lo_from_bytea(0, :d) AS oid"), {"d": data})
            oid = row.scalar_one()
            await self._s.commit()
            return f"lo:{oid}"
        row = await self._s.execute(
            text("INSERT INTO blobs (data, content_type) VALUES (:d, :ct) RETURNING id"),
            {"d": data, "ct": content_type},
        )
        bid = row.scalar_one()
        await self._s.commit()
        return f"blob:{bid}"

    async def get(self, ref: str) -> bytes:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            row = await self._s.execute(text("SELECT data FROM blobs WHERE id = :id"), {"id": ident})
            val = row.scalar_one_or_none()
            if val is None:
                raise KeyError(ref)
            return bytes(val)
        if kind == "lo":
            row = await self._s.execute(text("SELECT lo_get(:oid)"), {"oid": int(ident)})
            return bytes(row.scalar_one())
        raise ValueError(f"unknown ref: {ref}")

    async def open(self, ref: str) -> AsyncIterator[bytes]:
        data = await self.get(ref)
        for i in range(0, len(data), _CHUNK):
            yield data[i : i + _CHUNK]

    async def delete(self, ref: str) -> None:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            await self._s.execute(text("DELETE FROM blobs WHERE id = :id"), {"id": ident})
        elif kind == "lo":
            await self._s.execute(text("SELECT lo_unlink(:oid)"), {"oid": int(ident)})
        else:
            raise ValueError(f"unknown ref: {ref}")
        await self._s.commit()

    async def exists(self, ref: str) -> bool:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            row = await self._s.execute(
                text("SELECT 1 FROM blobs WHERE id = :id"), {"id": ident}
            )
            return row.scalar_one_or_none() is not None
        if kind == "lo":
            row = await self._s.execute(
                text("SELECT 1 FROM pg_largeobject_metadata WHERE oid = :oid"), {"oid": int(ident)}
            )
            return row.scalar_one_or_none() is not None
        raise ValueError(f"unknown ref: {ref}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_storage.py tests/integration/test_migration.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/paw/storage tests/integration/test_storage.py
git commit -m "feat: add storage backend (bytea + large object)"
```

---

### Task 7: Password hashing (argon2)

**Files:**
- Create: `src/paw/security/__init__.py`
- Create: `src/paw/security/passwords.py`
- Test: `tests/unit/test_passwords.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_passwords.py
from paw.security.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password("correct horse", h) is True


def test_verify_rejects_wrong():
    h = hash_password("correct horse")
    assert verify_password("battery staple", h) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_passwords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.passwords'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/security/__init__.py
```

```python
# src/paw/security/passwords.py
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_passwords.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/security/__init__.py src/paw/security/passwords.py tests/unit/test_passwords.py
git commit -m "feat: add argon2 password hashing"
```

---

### Task 8: Secrets helper (Fernet)

**Files:**
- Create: `src/paw/security/secrets.py`
- Test: `tests/unit/test_secrets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_secrets.py
from cryptography.fernet import Fernet

from paw.security.secrets import SecretBox


def test_encrypt_decrypt_roundtrip():
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("sk-provider-123")
    assert token != "sk-provider-123"
    assert box.decrypt(token) == "sk-provider-123"


def test_decrypt_tampered_raises():
    import pytest
    from cryptography.fernet import InvalidToken

    box = SecretBox(Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        box.decrypt("not-a-valid-token")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.secrets'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/security/secrets.py
from cryptography.fernet import Fernet


class SecretBox:
    """Encrypt/decrypt provider secrets at rest (Fernet, key from env). LLD §11."""

    def __init__(self, key: str) -> None:
        self._f = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_secrets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/security/secrets.py tests/unit/test_secrets.py
git commit -m "feat: add Fernet secret box for at-rest encryption"
```

---

### Task 9: Sanitize + markdown render

**Files:**
- Create: `src/paw/security/sanitize.py`
- Test: `tests/unit/test_sanitize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sanitize.py
from paw.security.sanitize import render_markdown


def test_renders_markdown_to_html():
    html = render_markdown("# Title\n\nSome **bold** text.")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html


def test_strips_script_tags():
    html = render_markdown("ok\n\n<script>alert(1)</script>")
    assert "<script>" not in html
    assert "alert(1)" not in html or "&lt;script&gt;" not in html  # no executable script


def test_strips_event_handlers():
    html = render_markdown('<a href="x" onclick="evil()">link</a>')
    assert "onclick" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sanitize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.sanitize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/security/sanitize.py
import mistune
import nh3

_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "strong", "em", "del", "blockquote", "code", "pre",
    "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}

_md = mistune.create_markdown(plugins=["table", "strikethrough"])


def render_markdown(text: str) -> str:
    raw_html = _md(text)
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sanitize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/security/sanitize.py tests/unit/test_sanitize.py
git commit -m "feat: add markdown render + nh3 sanitize allowlist"
```

---

### Task 10: CSRF double-submit token

**Files:**
- Create: `src/paw/security/csrf.py`
- Test: `tests/unit/test_csrf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_csrf.py
from paw.security.csrf import issue_token, verify_token


def test_valid_token_verifies():
    secret = "s" * 32
    t = issue_token(secret)
    assert verify_token(secret, t, t) is True


def test_mismatched_cookie_and_header_fails():
    secret = "s" * 32
    a = issue_token(secret)
    b = issue_token(secret)
    assert verify_token(secret, a, b) is False


def test_tampered_token_fails():
    secret = "s" * 32
    t = issue_token(secret)
    assert verify_token(secret, t, t + "x") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_csrf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.csrf'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/security/csrf.py
import hashlib
import hmac
import secrets


def issue_token(secret: str) -> str:
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{nonce}.{sig}"


def _valid(secret: str, token: str) -> bool:
    nonce, _, sig = token.partition(".")
    if not nonce or not sig:
        return False
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig)


def verify_token(secret: str, cookie_token: str, header_token: str) -> bool:
    # double-submit: cookie and submitted token must match AND be authentic
    if not cookie_token or not header_token:
        return False
    if not hmac.compare_digest(cookie_token, header_token):
        return False
    return _valid(secret, cookie_token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_csrf.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/security/csrf.py tests/unit/test_csrf.py
git commit -m "feat: add CSRF double-submit token helpers"
```

---

### Task 11: Redis session store + Redis test fixture

**Files:**
- Create: `src/paw/security/sessions.py`
- Modify: `tests/conftest.py` (add Redis container + client fixture)
- Test: `tests/integration/test_sessions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_sessions.py
from paw.security.sessions import SessionStore


async def test_session_lifecycle(redis_client):
    store = SessionStore(redis_client, ttl_seconds=60)
    sid = await store.create("11111111-1111-1111-1111-111111111111")
    assert sid
    assert await store.get(sid) == "11111111-1111-1111-1111-111111111111"
    await store.delete(sid)
    assert await store.get(sid) is None


async def test_unknown_session_is_none(redis_client):
    store = SessionStore(redis_client, ttl_seconds=60)
    assert await store.get("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_sessions.py -v`
Expected: FAIL — module + `redis_client` fixture missing.

- [ ] **Step 3: Add Redis fixtures to `tests/conftest.py`**

```python
# tests/conftest.py  (append)
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as rc:
        yield rc


@pytest.fixture
async def redis_client(redis_container: RedisContainer) -> AsyncIterator["aioredis.Redis"]:
    host = redis_REDACTEDt_container_host_ip()
    port = redis_REDACTEDt_exposed_port(6379)
    client = aioredis.Redis(host=host, port=int(port), decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()
```

- [ ] **Step 4: Write minimal implementation**

```python
# src/paw/security/sessions.py
import secrets

import redis.asyncio as aioredis

_PREFIX = "session:"


class SessionStore:
    """Server-side sessions in Redis (cookie holds the opaque session id). LLD §8."""

    def __init__(self, client: aioredis.Redis, ttl_seconds: int) -> None:
        self._r = client
        self._ttl = ttl_seconds

    async def create(self, user_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        await self._r.set(_PREFIX + sid, user_id, ex=self._ttl)
        return sid

    async def get(self, sid: str) -> str | None:
        if not sid:
            return None
        return await self._r.get(_PREFIX + sid)

    async def delete(self, sid: str) -> None:
        await self._r.delete(_PREFIX + sid)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_sessions.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/paw/security/sessions.py tests/conftest.py tests/integration/test_sessions.py
git commit -m "feat: add Redis-backed server-side session store"
```

---

### Task 12: RFC 9457 errors + cursor pagination

**Files:**
- Create: `src/paw/api/__init__.py`
- Create: `src/paw/api/errors.py`
- Create: `src/paw/api/pagination.py`
- Test: `tests/unit/test_pagination.py`
- Test: `tests/unit/test_errors.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_pagination.py
from paw.api.pagination import decode_cursor, encode_cursor


def test_cursor_roundtrip():
    cur = encode_cursor("2026-06-22T10:00:00+00:00", "abc-id")
    ts, ident = decode_cursor(cur)
    assert ts == "2026-06-22T10:00:00+00:00"
    assert ident == "abc-id"


def test_bad_cursor_raises():
    import pytest

    with pytest.raises(ValueError):
        decode_cursor("!!!notbase64!!!")
```

```python
# tests/unit/test_errors.py
from paw.api.errors import ProblemError, problem_response


def test_problem_response_shape():
    exc = ProblemError(status=409, title="Conflict", detail="stale revision")
    resp = problem_response(exc)
    assert resp.status_code == 409
    assert resp.media_type == "application/problem+json"


def test_problem_error_defaults():
    exc = ProblemError(status=404, title="Not Found")
    assert exc.detail is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pagination.py tests/unit/test_errors.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/api/__init__.py
```

```python
# src/paw/api/pagination.py
import base64
import binascii


def encode_cursor(sort_value: str, ident: str) -> str:
    raw = f"{sort_value}|{ident}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise ValueError("invalid cursor") from e
    sort_value, _, ident = raw.partition("|")
    if not ident:
        raise ValueError("invalid cursor")
    return sort_value, ident
```

```python
# src/paw/api/errors.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ProblemError(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None,
                 type_: str = "about:blank") -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type_
        super().__init__(title)


def problem_response(exc: ProblemError) -> JSONResponse:
    body = {"type": exc.type, "title": exc.title, "status": exc.status}
    if exc.detail:
        body["detail"] = exc.detail
    return JSONResponse(
        status_code=exc.status, content=body, media_type="application/problem+json"
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _handle(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pagination.py tests/unit/test_errors.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/api/__init__.py src/paw/api/errors.py src/paw/api/pagination.py tests/unit/test_pagination.py tests/unit/test_errors.py
git commit -m "feat: add RFC 9457 errors and cursor pagination helpers"
```

---

### Task 13: Repositories + audit log

**Files:**
- Create: `src/paw/db/repos/__init__.py`
- Create: `src/paw/db/repos/users.py`
- Create: `src/paw/db/repos/domains.py`
- Create: `src/paw/db/repos/articles.py`
- Create: `src/paw/db/repos/sources.py`
- Create: `src/paw/db/repos/settings.py`
- Create: `src/paw/audit/__init__.py`
- Create: `src/paw/audit/log.py`
- Test: `tests/integration/test_repos.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_repos.py
from paw.db.repos.users import UserRepo
from paw.db.repos.domains import DomainRepo


async def test_user_create_and_get_by_email(db_session):
    repo = UserRepo(db_session)
    u = await repo.create(email="a@example.com", pw_hash="x", role="admin")
    await db_session.commit()
    got = await repo.get_by_email("a@example.com")
    assert got is not None and got.id == u.id and got.role == "admin"


async def test_domain_create_and_list(db_session):
    repo = DomainRepo(db_session)
    await repo.create(name="net", source_prefix="src/net", wiki_prefix="wiki/net")
    await db_session.commit()
    items = await repo.list()
    assert any(d.name == "net" for d in items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_repos.py -v`
Expected: FAIL — repo modules missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/db/repos/__init__.py
```

```python
# src/paw/db/repos/users.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import User


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, email: str, pw_hash: str, role: str = "viewer") -> User:
        u = User(email=email, pw_hash=pw_hash, role=role)
        self._s.add(u)
        await self._s.flush()
        return u

    async def get_by_email(self, email: str) -> User | None:
        res = await self._s.execute(select(User).where(User.email == email))
        return res.scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def count(self) -> int:
        from sqlalchemy import func

        res = await self._s.execute(select(func.count()).select_from(User))
        return int(res.scalar_one())

    async def list(self) -> list[User]:
        res = await self._s.execute(select(User).order_by(User.created_at))
        return list(res.scalars().all())
```

```python
# src/paw/db/repos/domains.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Domain


class DomainRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, name: str, source_prefix: str, wiki_prefix: str) -> Domain:
        d = Domain(name=name, source_prefix=source_prefix, wiki_prefix=wiki_prefix)
        self._s.add(d)
        await self._s.flush()
        return d

    async def get(self, domain_id: uuid.UUID) -> Domain | None:
        return await self._s.get(Domain, domain_id)

    async def list(self) -> list[Domain]:
        res = await self._s.execute(select(Domain).order_by(Domain.created_at))
        return list(res.scalars().all())
```

```python
# src/paw/db/repos/articles.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, ArticleRevision


class ArticleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, domain_id: uuid.UUID, slug: str, title: str,
                     storage_ref: str, summary: str | None = None) -> Article:
        a = Article(domain_id=domain_id, slug=slug, title=title,
                    storage_ref=storage_ref, summary=summary, current_rev=1)
        self._s.add(a)
        await self._s.flush()
        return a

    async def get(self, article_id: uuid.UUID) -> Article | None:
        return await self._s.get(Article, article_id)

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Article]:
        res = await self._s.execute(
            select(Article).where(Article.domain_id == domain_id).order_by(Article.slug)
        )
        return list(res.scalars().all())

    async def add_revision(self, *, article_id: uuid.UUID, rev_no: int, storage_ref: str,
                           author_id: uuid.UUID | None, origin: str) -> ArticleRevision:
        r = ArticleRevision(article_id=article_id, rev_no=rev_no, storage_ref=storage_ref,
                            author_id=author_id, origin=origin)
        self._s.add(r)
        await self._s.flush()
        return r

    async def list_revisions(self, article_id: uuid.UUID) -> list[ArticleRevision]:
        res = await self._s.execute(
            select(ArticleRevision)
            .where(ArticleRevision.article_id == article_id)
            .order_by(ArticleRevision.rev_no.desc())
        )
        return list(res.scalars().all())
```

```python
# src/paw/db/repos/sources.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Source


class SourceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, domain_id: uuid.UUID, storage_ref: str, filename: str | None,
                     type: str, checksum: str) -> Source:
        s = Source(domain_id=domain_id, storage_ref=storage_ref, filename=filename,
                   type=type, checksum=checksum)
        self._s.add(s)
        await self._s.flush()
        return s

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Source]:
        res = await self._s.execute(
            select(Source).where(Source.domain_id == domain_id).order_by(Source.created_at)
        )
        return list(res.scalars().all())

    async def get(self, source_id: uuid.UUID) -> Source | None:
        return await self._s.get(Source, source_id)

    async def delete(self, source: Source) -> None:
        await self._s.delete(source)
```

```python
# src/paw/db/repos/settings.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import AppSettings


class SettingsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> AppSettings | None:
        res = await self._s.execute(select(AppSettings).where(AppSettings.id.is_(True)))
        return res.scalar_one_or_none()

    async def upsert(self, settings: dict) -> AppSettings:
        row = await self.get()
        if row is None:
            row = AppSettings(id=True, settings=settings)
            self._s.add(row)
        else:
            row.settings = settings
        await self._s.flush()
        return row
```

```python
# src/paw/audit/__init__.py
```

```python
# src/paw/audit/log.py
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import AuditLog


async def record(session: AsyncSession, *, user_id: uuid.UUID | None, action: str,
                 target_type: str | None = None, target_id: uuid.UUID | None = None,
                 meta: dict | None = None) -> None:
    session.add(AuditLog(user_id=user_id, action=action, target_type=target_type,
                         target_id=target_id, meta=meta or {}))
    await session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_repos.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/repos src/paw/audit tests/integration/test_repos.py
git commit -m "feat: add repositories and audit log writer"
```

---

### Task 14: Auth dependencies + login/logout API wired into the app

**Files:**
- Create: `src/paw/api/deps.py`
- Create: `src/paw/api/auth.py`
- Create: `src/paw/api/routers/__init__.py`
- Create: `src/paw/api/routers/auth.py`
- Modify: `src/paw/main.py` (install error handlers + include auth router + app-state wiring)
- Test: `tests/api/test_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_auth.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


@pytest.fixture
async def client(pg_async_url, redis_container, db_session):
    # seed a user
    repo = UserRepo(db_session)
    await repo.create(email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin")
    await db_session.commit()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_login_sets_session_cookie(client):
    r = await client.post("/api/v1/auth/login",
                          json={"email": "admin@example.com", "password": "pw12345"})
    assert r.status_code == 200
    assert "paw_session" in r.cookies


async def test_login_wrong_password_401(client):
    r = await client.post("/api/v1/auth/login",
                          json={"email": "admin@example.com", "password": "WRONG"})
    assert r.status_code == 401


async def test_logout_clears_session(client):
    await client.post("/api/v1/auth/login",
                      json={"email": "admin@example.com", "password": "pw12345"})
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: FAIL — auth router + deps missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/api/deps.py
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.models import User
from paw.db.repos.users import UserRepo
from paw.db.session import get_session
from paw.security.csrf import verify_token
from paw.security.sessions import SessionStore

SESSION_COOKIE = "paw_session"
CSRF_COOKIE = "paw_csrf"
CSRF_HEADER = "x-csrf-token"

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def get_session_store() -> SessionStore:
    return SessionStore(get_redis(), ttl_seconds=get_settings().session_ttl_seconds)


async def db() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


async def current_user(
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> User:
    sid = request.cookies.get(SESSION_COOKIE, "")
    user_id = await store.get(sid)
    if not user_id:
        raise ProblemError(status=401, title="Unauthorized")
    user = await UserRepo(session).get(__import__("uuid").UUID(user_id))
    if user is None:
        raise ProblemError(status=401, title="Unauthorized")
    return user


def require_role(*roles: str):
    async def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise ProblemError(status=403, title="Forbidden",
                               detail=f"requires role in {roles}")
        return user

    return _dep


async def require_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not verify_token(get_settings().session_secret, cookie, header):
        raise ProblemError(status=403, title="CSRF validation failed")
```

```python
# src/paw/api/auth.py
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    id: str
    email: str
    role: str
```

```python
# src/paw/api/routers/__init__.py
```

```python
# src/paw/api/routers/auth.py
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.auth import LoginRequest, LoginResponse
from paw.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    db,
    get_session_store,
)
from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.repos.users import UserRepo
from paw.security.csrf import issue_token
from paw.security.passwords import verify_password
from paw.security.sessions import SessionStore

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    user = await UserRepo(session).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.pw_hash):
        raise ProblemError(status=401, title="Unauthorized", detail="bad credentials")
    sid = await store.create(str(user.id))
    csrf = issue_token(get_settings().session_secret)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=True)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=True)
    return LoginResponse(id=str(user.id), email=user.email, role=user.role)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    store: SessionStore = Depends(get_session_store),
) -> Response:
    sid = request.cookies.get(SESSION_COOKIE, "")
    if sid:
        await store.delete(sid)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return Response(status_code=204)
```

```python
# src/paw/main.py  (replace file)
from fastapi import FastAPI

from paw.api.errors import install_error_handlers
from paw.api.routers import auth as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")
    install_error_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router.router, prefix="/api/v1")
    return app


app = create_app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS (login sets cookie, wrong password 401, logout 204).

- [ ] **Step 5: Commit**

```bash
git add src/paw/api/deps.py src/paw/api/auth.py src/paw/api/routers/__init__.py src/paw/api/routers/auth.py src/paw/main.py tests/api/test_auth.py
git commit -m "feat: add auth deps (session/RBAC/CSRF) and login/logout API"
```

---

### Task 15: Domains service + API (CRUD, RBAC, CSRF)

**Files:**
- Create: `src/paw/services/__init__.py`
- Create: `src/paw/services/domains.py`
- Create: `src/paw/api/routers/domains.py`
- Modify: `src/paw/main.py` (include domains router)
- Test: `tests/api/test_domains.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_domains.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client, email, password):
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return client.cookies.get("paw_csrf")


@pytest.fixture
async def seeded(db_session):
    repo = UserRepo(db_session)
    await repo.create(email="REDACTED", pw_hash=hash_password("pw12345"), role="admin")
    await repo.create(email="REDACTED", pw_hash=hash_password("pw12345"), role="viewer")
    await db_session.commit()


@pytest.fixture
async def client(seeded, redis_container):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_admin_creates_domain(client):
    csrf = await _login(client, "REDACTED", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"},
                          headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    assert r.json()["name"] == "net"


async def test_viewer_cannot_create_domain(client):
    csrf = await _login(client, "REDACTED", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"},
                          headers={"x-csrf-token": csrf})
    assert r.status_code == 403


async def test_create_without_csrf_rejected(client):
    await _login(client, "REDACTED", "pw12345")
    r = await client.post("/api/v1/domains", json={"name": "net"})
    assert r.status_code == 403


async def test_list_domains_paginates(client):
    csrf = await _login(client, "REDACTED", "pw12345")
    for n in ("a", "b", "c"):
        await client.post("/api/v1/domains", json={"name": n}, headers={"x-csrf-token": csrf})
    r = await client.get("/api/v1/domains?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_domains.py -v`
Expected: FAIL — domains router missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/services/__init__.py
```

```python
# src/paw/services/domains.py
import re

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Domain
from paw.db.repos.domains import DomainRepo


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "domain"


class DomainService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = DomainRepo(session)

    async def create(self, name: str) -> Domain:
        slug = _slugify(name)
        d = await self._repo.create(
            name=name, source_prefix=f"src/{slug}", wiki_prefix=f"wiki/{slug}"
        )
        await self._s.commit()
        return d

    async def list(self) -> list[Domain]:
        return await self._repo.list()
```

```python
# src/paw/api/routers/domains.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.api.pagination import encode_cursor
from paw.db.models import User
from paw.services.domains import DomainService

router = APIRouter(prefix="/domains", tags=["domains"])


class DomainCreate(BaseModel):
    name: str


class DomainOut(BaseModel):
    id: str
    name: str


class DomainPage(BaseModel):
    items: list[DomainOut]
    next_cursor: str | None


@router.post("", status_code=201, response_model=DomainOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def create_domain(body: DomainCreate, session: AsyncSession = Depends(db)) -> DomainOut:
    d = await DomainService(session).create(body.name)
    return DomainOut(id=str(d.id), name=d.name)


@router.get("", response_model=DomainPage)
async def list_domains(
    limit: int = 50,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> DomainPage:
    items = await DomainService(session).list()
    page = items[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at.isoformat(), str(page[-1].id))
        if len(items) > limit else None
    )
    return DomainPage(
        items=[DomainOut(id=str(d.id), name=d.name) for d in page],
        next_cursor=next_cursor,
    )
```

```python
# src/paw/main.py  (add domains router include — insert after auth include)
    from paw.api.routers import domains as domains_router
    app.include_router(domains_router.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_domains.py -v`
Expected: PASS (admin 201, viewer 403, no-csrf 403, pagination).

- [ ] **Step 5: Commit**

```bash
git add src/paw/services/__init__.py src/paw/services/domains.py src/paw/api/routers/domains.py src/paw/main.py tests/api/test_domains.py
git commit -m "feat: add domains service and CRUD API with RBAC + CSRF"
```

---

### Task 16: Upload guard + sources service + API (md/txt)

**Files:**
- Create: `src/paw/security/uploads.py`
- Create: `src/paw/services/sources.py`
- Create: `src/paw/api/routers/sources.py`
- Modify: `src/paw/main.py` (include sources router)
- Test: `tests/unit/test_uploads.py`
- Test: `tests/api/test_sources.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_uploads.py
import pytest

from paw.security.uploads import UploadRejected, validate_text_upload


def test_accepts_markdown():
    validate_text_upload("note.md", b"# hello\n", max_bytes=1024)  # no raise


def test_rejects_bad_extension():
    with pytest.raises(UploadRejected):
        validate_text_upload("evil.exe", b"MZ...", max_bytes=1024)


def test_rejects_oversize():
    with pytest.raises(UploadRejected):
        validate_text_upload("note.md", b"x" * 2048, max_bytes=1024)


def test_rejects_non_utf8():
    with pytest.raises(UploadRejected):
        validate_text_upload("note.txt", b"\xff\xfe\x00binary", max_bytes=1024)
```

```python
# tests/api/test_sources.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, redis_container):
    await UserRepo(db_session).create(
        email="REDACTED", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "REDACTED", "password": "pw12345"})
        yield c


async def test_upload_md_source(client):
    csrf = client.cookies.get("paw_csrf")
    dom = (await client.post("/api/v1/domains", json={"name": "net"},
                             headers={"x-csrf-token": csrf})).json()
    files = {"file": ("intro.md", b"# Intro\n\nbody", "text/markdown")}
    r = await client.post(f"/api/v1/domains/{dom['id']}/sources",
                          files=files, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    assert r.json()["filename"] == "intro.md"


async def test_upload_rejects_exe(client):
    csrf = client.cookies.get("paw_csrf")
    dom = (await client.post("/api/v1/domains", json={"name": "net"},
                             headers={"x-csrf-token": csrf})).json()
    files = {"file": ("x.exe", b"MZbinary", "application/octet-stream")}
    r = await client.post(f"/api/v1/domains/{dom['id']}/sources",
                          files=files, headers={"x-csrf-token": csrf})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_uploads.py tests/api/test_sources.py -v`
Expected: FAIL — modules/router missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/security/uploads.py
ALLOWED_TEXT_EXT = {".md", ".txt", ".markdown"}


class UploadRejected(Exception):
    pass


def validate_text_upload(filename: str, data: bytes, *, max_bytes: int) -> None:
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_TEXT_EXT):
        raise UploadRejected(f"extension not allowed: {filename}")
    if len(data) > max_bytes:
        raise UploadRejected("file too large")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise UploadRejected("not valid UTF-8 text") from e
```

```python
# src/paw/services/sources.py
import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.models import Source
from paw.db.repos.sources import SourceRepo
from paw.security.uploads import validate_text_upload
from paw.storage.postgres import PostgresStorage


class SourceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = SourceRepo(session)
        self._store = PostgresStorage(session)

    async def upload_text(self, *, domain_id: uuid.UUID, filename: str, data: bytes,
                          content_type: str | None) -> Source:
        validate_text_upload(filename, data, max_bytes=get_settings().max_upload_bytes)
        checksum = hashlib.sha256(data).hexdigest()
        ref = await self._store.put(data, content_type=content_type or "text/markdown")
        ext = filename.rsplit(".", 1)[-1].lower()
        src = await self._repo.create(domain_id=domain_id, storage_ref=ref,
                                      filename=filename, type=ext, checksum=checksum)
        await self._s.commit()
        return src

    async def list(self, domain_id: uuid.UUID) -> list[Source]:
        return await self._repo.list_by_domain(domain_id)
```

```python
# src/paw/api/routers/sources.py
import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.api.errors import ProblemError
from paw.security.uploads import UploadRejected
from paw.services.sources import SourceService

router = APIRouter(prefix="/domains/{domain_id}/sources", tags=["sources"])


class SourceOut(BaseModel):
    id: str
    filename: str | None
    type: str


@router.post("", status_code=201, response_model=SourceOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def upload_source(
    domain_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
) -> SourceOut:
    data = await file.read()
    try:
        src = await SourceService(session).upload_text(
            domain_id=domain_id, filename=file.filename or "upload",
            data=data, content_type=file.content_type,
        )
    except UploadRejected as e:
        raise ProblemError(status=422, title="Upload rejected", detail=str(e)) from e
    return SourceOut(id=str(src.id), filename=src.filename, type=src.type)
```

```python
# src/paw/main.py  (add sources router include)
    from paw.api.routers import sources as sources_router
    app.include_router(sources_router.router, prefix="/api/v1")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_uploads.py tests/api/test_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/security/uploads.py src/paw/services/sources.py src/paw/api/routers/sources.py src/paw/main.py tests/unit/test_uploads.py tests/api/test_sources.py
git commit -m "feat: add text upload guard, sources service and upload API"
```

---

### Task 17: Articles service + API (create/update, optimistic lock 409, revisions, rollback)

**Files:**
- Create: `src/paw/services/articles.py`
- Create: `src/paw/api/routers/articles.py`
- Modify: `src/paw/main.py` (include articles router)
- Test: `tests/api/test_articles.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_articles.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def ctx(db_session, redis_container):
    await UserRepo(db_session).create(
        email="REDACTED", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "REDACTED", "password": "pw12345"})
        csrf = c.cookies.get("paw_csrf")
        dom = (await c.post("/api/v1/domains", json={"name": "net"},
                            headers={"x-csrf-token": csrf})).json()
        yield c, csrf, dom["id"]


async def test_create_and_get_article(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/articles",
                     json={"slug": "quic", "title": "QUIC", "markdown": "# QUIC\n\nbody"},
                     headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    art = r.json()
    assert art["current_rev"] == 1
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert g.status_code == 200
    assert "<h1>QUIC</h1>" in g.json()["html"]


async def test_update_optimistic_lock_conflict(ctx):
    c, csrf, dom = ctx
    art = (await c.post(f"/api/v1/domains/{dom}/articles",
                        json={"slug": "tcp", "title": "TCP", "markdown": "# TCP"},
                        headers={"x-csrf-token": csrf})).json()
    ok = await c.put(f"/api/v1/articles/{art['id']}",
                     json={"title": "TCP", "markdown": "# TCP v2", "expected_rev": 1},
                     headers={"x-csrf-token": csrf})
    assert ok.status_code == 200
    assert ok.json()["current_rev"] == 2
    stale = await c.put(f"/api/v1/articles/{art['id']}",
                        json={"title": "TCP", "markdown": "# TCP v3", "expected_rev": 1},
                        headers={"x-csrf-token": csrf})
    assert stale.status_code == 409


async def test_rollback_creates_new_revision(ctx):
    c, csrf, dom = ctx
    art = (await c.post(f"/api/v1/domains/{dom}/articles",
                        json={"slug": "tls", "title": "TLS", "markdown": "# TLS v1"},
                        headers={"x-csrf-token": csrf})).json()
    await c.put(f"/api/v1/articles/{art['id']}",
                json={"title": "TLS", "markdown": "# TLS v2", "expected_rev": 1},
                headers={"x-csrf-token": csrf})
    rb = await c.post(f"/api/v1/articles/{art['id']}/rollback",
                      json={"rev_no": 1}, headers={"x-csrf-token": csrf})
    assert rb.status_code == 200
    assert rb.json()["current_rev"] == 3
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert "TLS v1" in g.json()["html"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_articles.py -v`
Expected: FAIL — articles router missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/services/articles.py
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.storage.postgres import PostgresStorage


@dataclass
class ArticleBody:
    article: Article
    markdown: str


class ArticleService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = ArticleRepo(session)
        self._store = PostgresStorage(session)

    async def create(self, *, domain_id: uuid.UUID, slug: str, title: str,
                     markdown: str, author_id: uuid.UUID) -> Article:
        ref = await self._store.put(markdown.encode(), content_type="text/markdown")
        art = await self._repo.create(domain_id=domain_id, slug=slug, title=title,
                                      storage_ref=ref)
        await self._repo.add_revision(article_id=art.id, rev_no=1, storage_ref=ref,
                                      author_id=author_id, origin="user")
        await self._s.commit()
        return art

    async def get_body(self, article_id: uuid.UUID) -> ArticleBody:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        markdown = (await self._store.get(art.storage_ref)).decode()
        return ArticleBody(article=art, markdown=markdown)

    async def update(self, *, article_id: uuid.UUID, expected_rev: int, title: str,
                     markdown: str, author_id: uuid.UUID) -> Article:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        if art.current_rev != expected_rev:
            raise ProblemError(status=409, title="Conflict",
                               detail=f"stale revision (current={art.current_rev})")
        new_rev = art.current_rev + 1
        ref = await self._store.put(markdown.encode(), content_type="text/markdown")
        art.title = title
        art.storage_ref = ref
        art.current_rev = new_rev
        await self._repo.add_revision(article_id=art.id, rev_no=new_rev, storage_ref=ref,
                                      author_id=author_id, origin="user")
        await self._s.commit()
        return art

    async def rollback(self, *, article_id: uuid.UUID, rev_no: int,
                       author_id: uuid.UUID) -> Article:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        target = next((r for r in await self._repo.list_revisions(article_id)
                       if r.rev_no == rev_no), None)
        if target is None:
            raise ProblemError(status=404, title="Revision not found")
        new_rev = art.current_rev + 1
        art.storage_ref = target.storage_ref
        art.current_rev = new_rev
        await self._repo.add_revision(article_id=art.id, rev_no=new_rev,
                                      storage_ref=target.storage_ref,
                                      author_id=author_id, origin="user")
        await self._s.commit()
        return art

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Article]:
        return await self._repo.list_by_domain(domain_id)
```

```python
# src/paw/api/routers/articles.py
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf, require_role
from paw.db.models import User
from paw.security.sanitize import render_markdown
from paw.services.articles import ArticleService

router = APIRouter(tags=["articles"])


class ArticleCreate(BaseModel):
    slug: str
    title: str
    markdown: str


class ArticleUpdate(BaseModel):
    title: str
    markdown: str
    expected_rev: int


class RollbackRequest(BaseModel):
    rev_no: int


class ArticleOut(BaseModel):
    id: str
    slug: str
    title: str
    current_rev: int


class ArticleDetail(ArticleOut):
    html: str


@router.post("/domains/{domain_id}/articles", status_code=201, response_model=ArticleOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def create_article(domain_id: uuid.UUID, body: ArticleCreate,
                         user: User = Depends(current_user),
                         session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).create(
        domain_id=domain_id, slug=body.slug, title=body.title,
        markdown=body.markdown, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)


@router.get("/articles/{article_id}", response_model=ArticleDetail)
async def get_article(article_id: uuid.UUID, session: AsyncSession = Depends(db),
                      _: User = Depends(require_role("admin", "editor", "viewer"))) -> ArticleDetail:
    body = await ArticleService(session).get_body(article_id)
    return ArticleDetail(
        id=str(body.article.id), slug=body.article.slug, title=body.article.title,
        current_rev=body.article.current_rev, html=render_markdown(body.markdown),
    )


@router.put("/articles/{article_id}", response_model=ArticleOut,
            dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def update_article(article_id: uuid.UUID, body: ArticleUpdate,
                         user: User = Depends(current_user),
                         session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).update(
        article_id=article_id, expected_rev=body.expected_rev, title=body.title,
        markdown=body.markdown, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)


@router.post("/articles/{article_id}/rollback", response_model=ArticleOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def rollback_article(article_id: uuid.UUID, body: RollbackRequest,
                           user: User = Depends(current_user),
                           session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).rollback(
        article_id=article_id, rev_no=body.rev_no, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)
```

```python
# src/paw/main.py  (add articles router include)
    from paw.api.routers import articles as articles_router
    app.include_router(articles_router.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_articles.py -v`
Expected: PASS (create+render, optimistic-lock 409, rollback new revision).

- [ ] **Step 5: Commit**

```bash
git add src/paw/services/articles.py src/paw/api/routers/articles.py src/paw/main.py tests/api/test_articles.py
git commit -m "feat: add articles service and API (optimistic lock, revisions, rollback)"
```

---

### Task 18: Setup wizard + settings + users (admin)

**Files:**
- Create: `src/paw/services/setup.py`
- Create: `src/paw/services/settings.py`
- Create: `src/paw/services/users.py`
- Create: `src/paw/api/routers/setup.py`
- Create: `src/paw/api/routers/settings.py`
- Create: `src/paw/api/routers/users.py`
- Modify: `src/paw/main.py` (include the three routers)
- Test: `tests/api/test_setup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_setup.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, redis_container):
    # no users seeded -> needs setup
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_first_run_needs_setup(client):
    r = await client.get("/api/v1/setup/status")
    assert r.status_code == 200
    assert r.json()["needs_setup"] is True


async def test_complete_setup_creates_admin(client):
    r = await client.post("/api/v1/setup",
                          json={"email": "REDACTED", "password": "pw12345"})
    assert r.status_code == 201
    assert r.json()["role"] == "admin"
    # second call rejected
    r2 = await client.post("/api/v1/setup",
                           json={"email": "REDACTED", "password": "pw12345"})
    assert r2.status_code == 409
    status = await client.get("/api/v1/setup/status")
    assert status.json()["needs_setup"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_setup.py -v`
Expected: FAIL — setup router missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/services/setup.py
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import User
from paw.db.repos.settings import SettingsRepo
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


class SetupService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._users = UserRepo(session)
        self._settings = SettingsRepo(session)

    async def needs_setup(self) -> bool:
        return (await self._users.count()) == 0

    async def complete(self, *, email: str, password: str) -> User:
        if not await self.needs_setup():
            raise ProblemError(status=409, title="Already initialized")
        admin = await self._users.create(
            email=email, pw_hash=hash_password(password), role="admin"
        )
        await self._settings.upsert({})  # seed empty singleton
        await self._s.commit()
        return admin
```

```python
# src/paw/services/settings.py
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.settings import SettingsRepo


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = SettingsRepo(session)

    async def get(self) -> dict:
        row = await self._repo.get()
        return row.settings if row else {}

    async def update(self, settings: dict) -> dict:
        row = await self._repo.upsert(settings)
        await self._s.commit()
        return row.settings
```

```python
# src/paw/services/users.py
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import User
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = UserRepo(session)

    async def list(self) -> list[User]:
        return await self._repo.list()

    async def create(self, *, email: str, password: str, role: str) -> User:
        u = await self._repo.create(email=email, pw_hash=hash_password(password), role=role)
        await self._s.commit()
        return u
```

```python
# src/paw/api/routers/setup.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db
from paw.services.setup import SetupService

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupRequest(BaseModel):
    email: EmailStr
    password: str


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupResult(BaseModel):
    id: str
    email: str
    role: str


@router.get("/status", response_model=SetupStatus)
async def status(session: AsyncSession = Depends(db)) -> SetupStatus:
    return SetupStatus(needs_setup=await SetupService(session).needs_setup())


@router.post("", status_code=201, response_model=SetupResult)
async def complete(body: SetupRequest, session: AsyncSession = Depends(db)) -> SetupResult:
    admin = await SetupService(session).complete(email=body.email, password=body.password)
    return SetupResult(id=str(admin.id), email=admin.email, role=admin.role)
```

```python
# src/paw/api/routers/settings.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.db.models import User
from paw.services.settings import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings_endpoint(
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin")),
) -> dict:
    return await SettingsService(session).get()


@router.put("", dependencies=[Depends(require_csrf), Depends(require_role("admin"))])
async def put_settings_endpoint(body: dict, session: AsyncSession = Depends(db)) -> dict:
    return await SettingsService(session).update(body)
```

```python
# src/paw/api/routers/users.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.db.models import User
from paw.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "viewer"


class UserOut(BaseModel):
    id: str
    email: str
    role: str


@router.get("", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(db),
                     _: User = Depends(require_role("admin"))) -> list[UserOut]:
    return [UserOut(id=str(u.id), email=u.email, role=u.role)
            for u in await UserService(session).list()]


@router.post("", status_code=201, response_model=UserOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin"))])
async def create_user(body: UserCreate, session: AsyncSession = Depends(db)) -> UserOut:
    u = await UserService(session).create(email=body.email, password=body.password,
                                          role=body.role)
    return UserOut(id=str(u.id), email=u.email, role=u.role)
```

```python
# src/paw/main.py  (add setup/settings/users router includes)
    from paw.api.routers import setup as setup_router
    from paw.api.routers import settings as settings_router
    from paw.api.routers import users as users_router
    app.include_router(setup_router.router, prefix="/api/v1")
    app.include_router(settings_router.router, prefix="/api/v1")
    app.include_router(users_router.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_setup.py -v`
Expected: PASS (needs_setup True → complete → admin → second 409 → needs_setup False).

- [ ] **Step 5: Commit**

```bash
git add src/paw/services/setup.py src/paw/services/settings.py src/paw/services/users.py src/paw/api/routers/setup.py src/paw/api/routers/settings.py src/paw/api/routers/users.py src/paw/main.py tests/api/test_setup.py
git commit -m "feat: add first-run setup wizard, settings and users admin API"
```

---

### Task 19: Web UI base (frame C templates, vendored assets, CSP)

**Files:**
- Create: `src/paw/api/web/__init__.py`
- Create: `src/paw/api/web/templates/base.html`
- Create: `src/paw/api/web/templates/login.html`
- Create: `src/paw/api/web/static/theme.css`
- Create: `src/paw/api/web/static/htmx.min.js` (vendored, see Step 1)
- Create: `src/paw/api/web/static/app.js` (CSP-safe tab toggle + 409 conflict banner)
- Create: `src/paw/api/web/routes.py`
- Modify: `src/paw/main.py` (mount static + web router + CSP middleware)
- Test: `tests/api/test_web_shell.py`

- [ ] **Step 1: Vendor HTMX (no CDN)**

Run:
```bash
mkdir -p src/paw/api/web/static src/paw/api/web/templates
curl -fsSL https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js -o src/paw/api/web/static/htmx.min.js
test -s src/paw/api/web/static/htmx.min.js && echo "vendored"
```
Expected: prints `vendored`.

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_web_shell.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, redis_container):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_login_page_renders_frame(client):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "Personal AI Wiki" in r.text
    # CSP header present, no inline script allowed
    assert "content-security-policy" in {k.lower() for k in r.headers}
    assert "script-src 'self'" in r.headers["content-security-policy"]


async def test_static_htmx_served(client):
    r = await client.get("/static/htmx.min.js")
    assert r.status_code == 200
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web_shell.py -v`
Expected: FAIL — web routes/static not mounted.

- [ ] **Step 4: Write minimal implementation**

```html
<!-- src/paw/api/web/templates/base.html -->
<!DOCTYPE html>
<html lang="{{ ui_lang | default('en') }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Personal AI Wiki{% endblock %}</title>
  <link rel="stylesheet" href="/static/theme.css">
  <script src="/static/htmx.min.js" defer></script>
  <script src="/static/app.js" defer></script>
</head>
<body>
  <div class="app">
    <nav class="rail">
      <a href="/" title="Domains">🏠</a>
      <a href="/" title="Articles">📚</a>
      <a href="#" title="Chat (later)">💬</a>
      <a href="#" title="Graph (later)">🕸</a>
      <a href="/settings" title="Settings">⚙</a>
    </nav>
    <aside class="sidebar">{% block sidebar %}{% endblock %}</aside>
    <main class="content">{% block content %}{% endblock %}</main>
  </div>
</body>
</html>
```

```html
<!-- src/paw/api/web/templates/login.html -->
{% extends "base.html" %}
{% block title %}Sign in · Personal AI Wiki{% endblock %}
{% block content %}
<h1>Personal AI Wiki</h1>
<form method="post" action="/api/v1/auth/login" hx-post="/api/v1/auth/login" hx-ext="json-enc">
  <label>Email <input name="email" type="email" required></label>
  <label>Password <input name="password" type="password" required></label>
  <button type="submit">Sign in</button>
</form>
{% endblock %}
```

```css
/* src/paw/api/web/static/theme.css */
:root { --bg:#f6f7fb; --fg:#2b2b3a; --accent:#5c6bc0; --border:#d6d8e6; --surface:#fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#181825; --fg:#cdd6f4; --accent:#89b4fa; --border:#3a3b52; --surface:#262637; }
}
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
.app { display:grid; grid-template-columns:56px 260px 1fr; min-height:100vh; }
.rail { display:flex; flex-direction:column; gap:.4rem; padding:.6rem; background:var(--surface);
        border-right:1px solid var(--border); align-items:center; font-size:1.3rem; }
.rail a { text-decoration:none; }
.sidebar { padding:1rem; background:var(--surface); border-right:1px solid var(--border); overflow:auto; }
.content { padding:1.5rem 2rem; overflow:auto; }
.tabs { display:flex; gap:.5rem; border-bottom:1px solid var(--border); margin-bottom:1rem; }
.banner { background:#fde; border:1px solid #e88; padding:.6rem 1rem; border-radius:8px; }
button { background:var(--accent); color:#fff; border:0; padding:.4rem .8rem; border-radius:6px; cursor:pointer; }
input, textarea { width:100%; padding:.4rem; border:1px solid var(--border); border-radius:6px; background:var(--bg); color:var(--fg); }
```

```javascript
// src/paw/api/web/static/app.js
// CSP-safe: external file, no inline handlers, no eval. Tab toggle + 409 conflict banner.
document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (!tab) return;
  const root = tab.closest("[data-tabs]");
  if (!root) return;
  const name = tab.getAttribute("data-tab");
  root.querySelectorAll("[data-panel]").forEach((p) => {
    p.style.display = p.getAttribute("data-panel") === name ? "block" : "none";
  });
});

document.body.addEventListener("htmx:responseError", (e) => {
  if (e.detail.xhr.status === 409) {
    const banner = document.getElementById("conflict-banner");
    if (banner) banner.style.display = "block";
  }
});
```

```python
# src/paw/api/web/__init__.py
```

```python
# src/paw/api/web/routes.py
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["web"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")
```

```python
# src/paw/main.py  (replace create_app to add CSP middleware + static + web router)
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from paw.api.errors import install_error_handlers
from paw.api.routers import articles as articles_router
from paw.api.routers import auth as auth_router
from paw.api.routers import domains as domains_router
from paw.api.routers import settings as settings_router
from paw.api.routers import setup as setup_router
from paw.api.routers import sources as sources_router
from paw.api.routers import users as users_router
from paw.api.web import routes as web_routes

_STATIC_DIR = Path(__file__).parent / "api" / "web" / "static"

_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'"


def create_app() -> FastAPI:
    app = FastAPI(title="Personal AI Wiki", version="0.1.0")
    install_error_handlers(app)

    @app.middleware("http")
    async def csp(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = _CSP
        return resp

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (auth_router, domains_router, sources_router, articles_router,
              setup_router, settings_router, users_router):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return app


app = create_app()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_web_shell.py -v`
Expected: PASS (login page renders, CSP header present, static served).

- [ ] **Step 6: Commit**

```bash
git add src/paw/api/web tests/api/test_web_shell.py src/paw/main.py
git commit -m "feat: add web frame C base templates, vendored htmx, CSP middleware"
```

---

### Task 20: Web pages (setup wizard, dashboard, domain, article view/edit, settings)

**Files:**
- Create: `src/paw/api/web/templates/setup.html`
- Create: `src/paw/api/web/templates/dashboard.html`
- Create: `src/paw/api/web/templates/domain.html`
- Create: `src/paw/api/web/templates/article.html`
- Create: `src/paw/api/web/templates/settings.html`
- Modify: `src/paw/api/web/routes.py` (add page routes that call the JSON API server-side)
- Test: `tests/api/test_web_pages.py`

> These pages are thin server-rendered shells; interactivity (form posts) reuses the JSON API via HTMX with the CSRF cookie/header. The routes fetch data through the service layer directly (same session dependency) so SSR has content on first paint.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_web_pages.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, redis_container):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_setup_page_shown_when_no_users(client):
    r = await client.get("/")
    # first run redirects to setup
    assert r.status_code in (302, 307)
    assert "/setup" in r.headers["location"]


async def test_setup_then_dashboard(client):
    await client.post("/api/v1/setup", json={"email": "REDACTED", "password": "pw12345"})
    await client.post("/api/v1/auth/login", json={"email": "REDACTED", "password": "pw12345"})
    r = await client.get("/")
    assert r.status_code == 200
    assert "Domains" in r.text or "domains" in r.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web_pages.py -v`
Expected: FAIL — page routes missing.

- [ ] **Step 3: Write minimal implementation**

```html
<!-- src/paw/api/web/templates/setup.html -->
{% extends "base.html" %}
{% block title %}Setup · Personal AI Wiki{% endblock %}
{% block content %}
<h1>First-run setup</h1>
<p>Create the administrator account.</p>
<form hx-post="/api/v1/setup" hx-ext="json-enc">
  <label>Admin email <input name="email" type="email" required></label>
  <label>Password <input name="password" type="password" required></label>
  <button type="submit">Create admin</button>
</form>
{% endblock %}
```

```html
<!-- src/paw/api/web/templates/dashboard.html -->
{% extends "base.html" %}
{% block title %}Domains · Personal AI Wiki{% endblock %}
{% block sidebar %}<h3>Domains</h3>{% endblock %}
{% block content %}
<h1>Domains</h1>
<form hx-post="/api/v1/domains" hx-ext="json-enc" hx-headers='{"x-csrf-token": "{{ csrf }}"}'>
  <label>New domain <input name="name" required></label>
  <button type="submit">Create</button>
</form>
<ul>
  {% for d in domains %}<li><a href="/domains/{{ d.id }}">{{ d.name }}</a></li>{% endfor %}
</ul>
{% endblock %}
```

```html
<!-- src/paw/api/web/templates/domain.html -->
{% extends "base.html" %}
{% block title %}{{ domain.name }} · Personal AI Wiki{% endblock %}
{% block sidebar %}
<h3>{{ domain.name }}</h3>
<ul>{% for a in articles %}<li><a href="/articles/{{ a.id }}">{{ a.title }}</a></li>{% endfor %}</ul>
{% endblock %}
{% block content %}
<h1>{{ domain.name }}</h1>
<p>Sources and articles for this domain. (Ingest/Lint/Format land in later phases.)</p>
{% endblock %}
```

```html
<!-- src/paw/api/web/templates/article.html -->
{% extends "base.html" %}
{% block title %}{{ article.title }} · Personal AI Wiki{% endblock %}
{% block content %}
<div id="conflict-banner" class="banner" style="display:none">
  This article changed on the server (409 conflict). Reload the page before saving again.
</div>
<div data-tabs>
  <div class="tabs">
    <button type="button" data-tab="read">Read</button>
    <button type="button" data-tab="edit">Edit</button>
  </div>
  <article data-panel="read" style="display:block">{{ html | safe }}</article>
  <form data-panel="edit" style="display:none"
        hx-put="/api/v1/articles/{{ article.id }}" hx-ext="json-enc"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'>
    <input type="hidden" name="expected_rev" value="{{ article.current_rev }}">
    <label>Title <input name="title" value="{{ article.title }}"></label>
    <label>Markdown <textarea name="markdown" rows="16">{{ markdown }}</textarea></label>
    <button type="submit">Save</button>
  </form>
</div>
<section>
  <h3>Revisions</h3>
  <ul>{% for r in revisions %}<li>v{{ r.rev_no }} · {{ r.origin }}</li>{% endfor %}</ul>
</section>
{% endblock %}
```

```html
<!-- src/paw/api/web/templates/settings.html -->
{% extends "base.html" %}
{% block title %}Settings · Personal AI Wiki{% endblock %}
{% block sidebar %}
<nav><a href="#connection">Connection</a><br><a href="#users">Users</a></nav>
{% endblock %}
{% block content %}
<h1>Settings</h1>
<section id="connection"><h2>Connection</h2><p>Configured in Phase 2 (LLM provider).</p></section>
<section id="users"><h2>Users</h2></section>
{% endblock %}
```

```python
# src/paw/api/web/routes.py  (replace file)
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import CSRF_COOKIE, SESSION_COOKIE, db, get_session_store
from paw.security.sanitize import render_markdown
from paw.security.sessions import SessionStore
from paw.services.articles import ArticleService
from paw.services.domains import DomainService
from paw.services.setup import SetupService
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["web"])


async def _current_uid(request: Request, store: SessionStore) -> str | None:
    return await store.get(request.cookies.get(SESSION_COOKIE, ""))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, session: AsyncSession = Depends(db)) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(db),
                    store: SessionStore = Depends(get_session_store)):
    if await SetupService(session).needs_setup():
        return RedirectResponse("/setup", status_code=307)
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domains = await DomainService(session).list()
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request, "dashboard.html", {"domains": domains, "csrf": csrf}
    )


@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_page(domain_id: uuid.UUID, request: Request,
                      session: AsyncSession = Depends(db),
                      store: SessionStore = Depends(get_session_store)):
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    return templates.TemplateResponse(
        request, "domain.html", {"domain": domain, "articles": articles}
    )


@router.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_page(article_id: uuid.UUID, request: Request,
                       session: AsyncSession = Depends(db),
                       store: SessionStore = Depends(get_session_store)):
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    body = await ArticleService(session).get_body(article_id)
    revisions = await ArticleRepo(session).list_revisions(article_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request, "article.html",
        {"article": body.article, "html": render_markdown(body.markdown),
         "markdown": body.markdown, "revisions": revisions, "csrf": csrf},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: AsyncSession = Depends(db),
                        store: SessionStore = Depends(get_session_store)):
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    return templates.TemplateResponse(request, "settings.html")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_web_pages.py -v`
Expected: PASS (first run → /setup redirect; after setup+login → dashboard).

- [ ] **Step 5: Commit**

```bash
git add src/paw/api/web/templates src/paw/api/web/routes.py tests/api/test_web_pages.py
git commit -m "feat: add web pages (setup, dashboard, domain, article, settings)"
```

---

### Task 21: arq worker skeleton + heartbeat + Dockerfile

**Files:**
- Create: `src/paw/worker.py`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Test: `tests/integration/test_worker_heartbeat.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_worker_heartbeat.py
from paw.worker import heartbeat


async def test_heartbeat_writes_marker(redis_client):
    ctx = {"redis": redis_client}
    out = await heartbeat(ctx)
    assert out == "ok"
    assert await redis_client.get("paw:worker:heartbeat") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_worker_heartbeat.py -v`
Expected: FAIL — `paw.worker` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/paw/worker.py
from arq.connections import RedisSettings

from paw.config import get_settings


async def heartbeat(ctx: dict) -> str:
    """Liveness marker so deploys can assert the worker runs (LLD §7 seed for jobs)."""
    redis = ctx["redis"]
    await redis.set("paw:worker:heartbeat", "1", ex=120)
    return "ok"


class WorkerSettings:
    functions = [heartbeat]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        await heartbeat(ctx)
```

```dockerfile
# Dockerfile
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv pip install --system .

# api: uvicorn; worker: arq; init: alembic upgrade head  (entrypoint chosen in compose)
EXPOSE 8000
CMD ["uvicorn", "paw.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```text
# .dockerignore
.git
.venv
__pycache__
tests
docs
.superpowers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_worker_heartbeat.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paw/worker.py Dockerfile .dockerignore tests/integration/test_worker_heartbeat.py
git commit -m "feat: add arq worker skeleton with heartbeat and Dockerfile"
```

---

### Task 22: Docker Compose (traefik/api/worker/postgres/redis/init) + E2E

**Files:**
- Create: `docker-compose.yml`
- Test: `tests/e2e/test_walking_skeleton.py`

> The compose file is validated by `docker compose config`; the end-to-end behavior is asserted by an in-process E2E test that exercises the full flow against real Postgres + Redis containers (already provided by fixtures). A manual smoke-check via compose is in Step 5.

- [ ] **Step 1: Write the failing E2E test**

```python
# tests/e2e/test_walking_skeleton.py
import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, redis_container):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_full_walking_skeleton(client):
    # 1. first run -> setup
    assert (await client.get("/api/v1/setup/status")).json()["needs_setup"] is True
    # 2. create admin
    r = await client.post("/api/v1/setup",
                          json={"email": "REDACTED", "password": "pw12345"})
    assert r.status_code == 201
    # 3. login
    await client.post("/api/v1/auth/login",
                      json={"email": "REDACTED", "password": "pw12345"})
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    # 4. create domain
    dom = (await client.post("/api/v1/domains", json={"name": "networking"}, headers=h)).json()
    # 5. upload md source
    files = {"file": ("intro.md", b"# Intro\n\nQUIC over UDP.", "text/markdown")}
    s = await client.post(f"/api/v1/domains/{dom['id']}/sources", files=files, headers=h)
    assert s.status_code == 201
    # 6. manually author an article
    art = await client.post(
        f"/api/v1/domains/{dom['id']}/articles",
        json={"slug": "quic", "title": "QUIC", "markdown": "# QUIC\n\n**fast** transport"},
        headers=h,
    )
    assert art.status_code == 201
    aid = art.json()["id"]
    # 7. render sanitized
    page = await client.get(f"/api/v1/articles/{aid}")
    assert "<h1>QUIC</h1>" in page.json()["html"]
    assert "<strong>fast</strong>" in page.json()["html"]
    # 8. web article page renders with edit form + 409 conflict banner element
    web = await client.get(f"/articles/{aid}")
    assert web.status_code == 200
    assert "QUIC" in web.text
    assert 'id="conflict-banner"' in web.text
    assert 'hx-put="/api/v1/articles/' in web.text
```

- [ ] **Step 2: Run test to verify it fails (then passes once all prior tasks are in)**

Run: `uv run pytest tests/e2e/test_walking_skeleton.py -v`
Expected: PASS if Tasks 1–21 are complete (this is the integration capstone).

- [ ] **Step 3: Write the compose file**

```yaml
# docker-compose.yml
services:
  traefik:
    image: traefik:v3.2
    command:
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
      - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
      - "--certificatesresolvers.le.acme.tlschallenge=true"
      - "--certificatesresolvers.le.acme.email=${ACME_EMAIL:-REDACTED}"
      - "--certificatesresolvers.le.acme.storage=/letsencrypt/acme.json"
    ports: ["80:80", "443:443"]
    volumes:
      - "letsencrypt:/letsencrypt"
      - "/var/run/docker.sock:/var/run/docker.sock:ro"

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: paw
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-paw}
      POSTGRES_DB: paw
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U paw"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes: ["redisdata:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  init:
    build: .
    command: ["alembic", "upgrade", "head"]
    environment:
      DATABASE_URL: postgresql+asyncpg://paw:${POSTGRES_PASSWORD:-paw}@postgres:5432/paw
      REDIS_URL: redis://redis:6379/0
      SESSION_SECRET: ${SESSION_SECRET}
      FERNET_KEY: ${FERNET_KEY}
    depends_on:
      postgres: { condition: service_healthy }

  api:
    build: .
    command: ["uvicorn", "paw.main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://paw:${POSTGRES_PASSWORD:-paw}@postgres:5432/paw
      REDIS_URL: redis://redis:6379/0
      SESSION_SECRET: ${SESSION_SECRET}
      FERNET_KEY: ${FERNET_KEY}
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      init: { condition: service_completed_successfully }
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.paw.rule=Host(`${PAW_HOST:-localhost}`)"
      - "traefik.http.routers.paw.entrypoints=websecure"
      - "traefik.http.routers.paw.tls.certresolver=le"
      - "traefik.http.services.paw.loadbalancer.server.port=8000"
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)\""]
      interval: 10s
      timeout: 3s
      retries: 5

  worker:
    build: .
    command: ["arq", "paw.worker.WorkerSettings"]
    environment:
      DATABASE_URL: postgresql+asyncpg://paw:${POSTGRES_PASSWORD:-paw}@postgres:5432/paw
      REDIS_URL: redis://redis:6379/0
      SESSION_SECRET: ${SESSION_SECRET}
      FERNET_KEY: ${FERNET_KEY}
    depends_on:
      redis: { condition: service_healthy }
      init: { condition: service_completed_successfully }

volumes:
  pgdata:
  redisdata:
  letsencrypt:
```

- [ ] **Step 4: Validate the compose file**

Run:
```bash
SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))") \
FERNET_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
docker compose config >/dev/null && echo "compose valid"
```
Expected: prints `compose valid`.

- [ ] **Step 5: Manual smoke check (optional, requires Docker)**

Run:
```bash
cp .env.example .env   # then edit SESSION_SECRET + FERNET_KEY with real values
docker compose up -d --build
# wait for healthchecks, then:
curl -fsk https://localhost/health   # -> {"status":"ok"}  (or http://localhost:8000/health without traefik TLS)
docker compose down
```
Expected: `/health` returns `{"status":"ok"}`; the `init` container exits 0 after migrating.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml tests/e2e/test_walking_skeleton.py
git commit -m "feat: add docker compose stack and walking-skeleton E2E test"
```

---

## Self-Review

**1. Spec coverage** (against `…paw-phase-1-skeleton-design.md`):

| Spec requirement | Task(s) |
|---|---|
| Scaffold (uv, ruff/mypy/pytest, FastAPI factory, arq worker skeleton) | 1, 21 |
| Config env layer | 2 |
| DB core subset + alembic baseline via init container | 4, 5, 22 |
| PostgresStorage (bytea + Large Object) | 6 |
| Auth + security baseline (sessions, argon2, RBAC, CSRF, sanitize, RFC9457, cursor, upload guard) | 7, 8, 9, 10, 11, 12, 14, 16 |
| Services: domain CRUD; manual article + revisions + optimistic lock; md/txt source upload | 15, 16, 17 |
| API subset (auth, domains, sources, articles+revisions+rollback, settings, users, setup) | 14–18 |
| Web UI frame C (setup wizard, login, dashboard, domain, article view/edit tabs + 409 banner, settings) | 19, 20 |
| Deploy: compose (traefik/api/worker/postgres/redis/init), healthchecks, first-run wizard | 18, 22 |
| Acceptance criteria + tests (unit/api/e2e + CI) | every task (TDD) + 22 (E2E) |

All Phase 1 spec requirements map to at least one task. Out-of-scope items (LLM, ingest, chunking, retrieval, chat, graph viz, cache, MCP, observability, full hardening) are intentionally absent.

**2. Placeholder scan:** No `TODO`/`TBD`/`fill in`/"add error handling" left. The earlier placeholder logout in Task 14 was replaced with a single correct implementation. Vendored-asset and compose smoke steps are concrete commands, not placeholders.

**3. Type consistency:** Cross-task names checked — `ProblemError(status,title,detail)`, `render_markdown(text)`, `PostgresStorage(session).put/get/open/delete/exists`, `SessionStore(client, ttl_seconds).create/get/delete`, `issue_token/verify_token`, repo signatures (`UserRepo.create/get/get_by_email/count/list`, `ArticleRepo.create/add_revision/list_revisions/list_by_domain`), service signatures (`ArticleService.create/update/rollback/get_body`), and deps (`db`, `current_user`, `require_role`, `require_csrf`, `get_session_store`, `SESSION_COOKIE`, `CSRF_COOKIE`) are consistent across Tasks 6–22.

**Acceptance mapping (spec §"Acceptance criteria"):** AC1→Task22(compose+init), AC2→Task18(setup), AC3→Task14(login/logout), AC4→Task15(RBAC), AC5→Task15/16/17(CSRF), AC6→Task22(domain→upload→article→render), AC7→Task17(409), AC8→Task15(pagination).

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session via executing-plans, batched with checkpoints.

Which approach?

