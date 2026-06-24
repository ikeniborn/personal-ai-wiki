---
title: "Phase 8 — MCP Server Implementation Plan"
phase: 8
state: reviewed
review:
  plan_hash: 2b6aad5fd77280f7
  spec_hash: 33b04c7f8f3a3db9
  last_run: 2026-06-24
  phases:
    structure: { status: passed }
    coverage: { status: passed }
    dependencies: { status: passed }
    verifiability: { status: passed }
    consistency: { status: passed }
  findings:
    - { id: F-001, phase: structure, severity: INFO, section: "Task 4 Step 5", section_hash: 246faaef5133459a, text: "Expected count PASS (5 passed) matches 5 test functions — consistent, no action.", verdict: accepted, verdict_at: 2026-06-24 }
    - { id: F-002, phase: coverage, severity: INFO, section: "Task 7", section_hash: e6c0700488ac4237, text: "Spec 'Traefik must not buffer Streamable HTTP' is deploy/infra, noted in Risks, not turned into a plan step — acceptable.", verdict: accepted, verdict_at: 2026-06-24 }
    - { id: F-003, phase: dependencies, severity: WARNING, section: "Task 8", section_hash: d5e4de51a0b71346, text: "Test db_session uses its own engine while middleware/tools read via process-global get_sessionmaker(); same physical DB and seeds commit before app reads, so committed rows are visible. Keep 'await db_session.commit()' before any app/MCP read.", verdict: accepted, verdict_at: 2026-06-24 }
    - { id: F-004, phase: verifiability, severity: INFO, section: "Task 9 Step 3", section_hash: 16bd1ce4350e322f, text: "iwiki regenerate step is least crisply measurable but has a check (/iwiki-lint) and expected outcome — clears the bar.", verdict: accepted, verdict_at: 2026-06-24 }
    - { id: F-005, phase: consistency, severity: WARNING, section: "Task 6 Step 3", section_hash: 33854eb11e4769a7, text: "FastMCP API surface (FastMCP(...), settings.streamable_http_path, session_manager.run(), streamable_http_app(), @mcp.tool, list_tools()) not empirically validated — mcp not installed in review env. External SDK mcp>=1.28; confirm against installed SDK at Task 1/6.", verdict: accepted, verdict_at: 2026-06-24 }
    - { id: F-006, phase: consistency, severity: INFO, section: "Task 7 Step 6", section_hash: e6c0700488ac4237, text: "Regression claim holds: tests use ASGITransport without a lifespan manager, so the FastAPI lifespan never triggers; MCPAuthMiddleware passes non-/mcp paths through. E2E runs uvicorn with lifespan='on'.", verdict: accepted, verdict_at: 2026-06-24 }
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-8-mcp-design.md
---

# Phase 8 — MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the wiki to external MCP clients (IDEs/agents) over a read-only, API-key-authenticated MCP server mounted at `/mcp`, reusing the Phase 3 retrieval path and Phase 5 link reads with no new retrieval logic.

**Architecture:** A new `paw.mcp` package holds (a) pure, session-taking tool functions (`mcp/tools.py`) that wrap the existing `retrieve()` and `LinkRepo`/`ArticleRepo` reads and return Pydantic models, and (b) a FastMCP server (`mcp/server.py`) that registers three read-only tools and is mounted into the FastAPI app via `app.mount("/mcp", …)` with its session-manager lifespan wired in. A pure-ASGI middleware (`mcp/auth.py`) gates every `/mcp` request by `Bearer paw_<prefix>.<secret>` API key (lookup by prefix → verify sha256 → require the `read` scope → reject revoked). A minimal `/api-keys` REST surface (session-auth + CSRF) lets a user mint/list/revoke their own keys. `api_keys` already exists in the schema (Phase 1) — no migration.

**Tech Stack:** Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL 16 + `pgvector` · MCP Python SDK `mcp>=1.28` (FastMCP, Streamable HTTP) · `pytest` + `testcontainers`.

## Global Constraints

- **Dependency management is `uv`** — never call `pip`/`pytest` directly; go through `uv run`. Add deps with `uv add`.
- **CI gate (all three must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- **Service is the single commit boundary.** Repos and storage must never `commit()`; a service batches writes and commits once.
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457 `application/problem+json`). `IntegrityError` auto-maps to 409.
- **Async everywhere** (`asyncpg`, `redis.asyncio`); `pytest` runs `asyncio_mode = auto` so tests are plain `async def`.
- **Layering (no cycles):** `api`/`mcp` → `services` → `db.repos`, `storage` → `db`, `config`. `services` may import `paw.api.errors.ProblemError` (existing precedent in `services/query.py`). **`paw.mcp.tools` must NOT import from `paw.api`** — raise `ValueError` for not-found/unknown-domain there; the server wrapper turns exceptions into MCP tool errors.
- **MCP is read-only:** register only the three read tools; no write tool is exposed.
- **Tests need Docker** for `integration`/`api`/`e2e` layers (real Postgres + Redis via testcontainers). Only `unit` runs without Docker.
- **Branch workflow:** all work on a `dev/*` branch off up-to-date `master`; merge via PR. Never commit to `master`. Per-task commits as specified.
- **Docs are English; conversation is Russian.** After functional changes, update `docs/wiki/` via iwiki (final task).

## Reused building blocks (already in the codebase — do not reimplement)

- `paw.harness.retrieve.retrieve(session, *, domain_id, query, embedder, cfg, embedding_version, redis, embed_model) -> RetrievedContext` with `RetrievedContext.passages: list[Passage]` and `.refs: list[Ref]`. `Passage(chunk_id, article_id, slug, heading_path, text, score)`, `Ref(article_id, slug, title)`. **This is the single Phase 3 retrieval implementation — `search_wiki` calls it.**
- `paw.db.repos.links.LinkRepo(session)`: `.outgoing(article_id)` / `.backlinks(article_id)` → `list[LinkedArticle(link_type, article_id, slug, title)]`.
- `paw.db.repos.articles.ArticleRepo(session)`: `.get(article_id)`, `.list_by_domain`, `.slug_id_map`. (We add `.get_by_slug`.)
- `paw.db.repos.domains.DomainRepo(session)`: `.get(domain_id)`. (We add `.get_by_name`.)
- `paw.storage.postgres.PostgresStorage(session).get(storage_ref) -> bytes`.
- `paw.services.provider_settings.ProviderSettingsService(session)`: `.get_provider()`, `.get_retrieval()`, `.get_embedding_version()`.
- `paw.providers.factory.build_embedding_provider(pc, box)`, `paw.security.secrets.SecretBox(fernet_key)`, `paw.config.get_settings()`.
- `paw.providers.config.RetrievalConfig` (fields incl. `top_n`).
- `paw.db.session.get_sessionmaker()` → process-global `async_sessionmaker` (reset by the `wired_settings` test fixture).
- `paw.db.repos.users.UserRepo`, `paw.security.passwords.hash_password` (test seeding).
- Test stubs: `tests.stubs.StubEmbeddingProvider(dim)` (deterministic vectors by text hash), `paw.vector.embed.embed_and_write`, `paw.db.managed.ensure_embedding_column`.
- CSRF test helper pattern (see `tests/api/test_domains.py`): login → read `paw_csrf` cookie → send it as `x-csrf-token` header on writes.

## File Structure

**Create:**
- `src/paw/security/api_keys.py` — pure key crypto: generate / hash / verify / parse-bearer; scope constants.
- `src/paw/db/repos/api_keys.py` — `ApiKeyRepo` (create / by_prefix / list_for_user / revoke / touch_last_used). No commits.
- `src/paw/services/api_keys.py` — `ApiKeyService` (issue / list / revoke / authenticate) + `AuthedKey`, `IssuedKey`. Commit boundary.
- `src/paw/api/routers/api_keys.py` — `/api-keys` issue/list/revoke (session-auth + CSRF, self-scoped).
- `src/paw/mcp/__init__.py` — package marker.
- `src/paw/mcp/tools.py` — pure tool functions + output Pydantic models. No `paw.api` imports.
- `src/paw/mcp/server.py` — `build_mcp() -> FastMCP`; registers the three read tools; resolves session/domain/embedder.
- `src/paw/mcp/auth.py` — `MCPAuthMiddleware` (pure ASGI; streaming-safe) gating `/mcp`.
- `tests/unit/test_api_keys_crypto.py`, `tests/unit/test_mcp_server.py`.
- `tests/integration/test_api_keys_repo.py`, `tests/integration/test_api_keys_service.py`, `tests/integration/test_mcp_tools.py`.
- `tests/api/test_api_keys.py`, `tests/api/test_mcp_auth.py`.
- `tests/e2e/test_mcp_e2e.py`.

**Modify:**
- `pyproject.toml` — add `mcp>=1.28` dependency; add mypy override for `mcp.*`.
- `src/paw/db/repos/domains.py` — add `get_by_name`.
- `src/paw/db/repos/articles.py` — add `get_by_slug`.
- `src/paw/main.py` — include `api_keys` router; build + mount MCP; wire lifespan; add auth middleware.
- `docs/wiki/*` — refreshed via iwiki (final task).

---

### Task 1: API-key crypto helpers + MCP dependency

**Files:**
- Modify: `pyproject.toml`
- Create: `src/paw/security/api_keys.py`
- Test: `tests/unit/test_api_keys_crypto.py`

**Interfaces:**
- Produces:
  - `KEY_PREFIX: str = "paw_"`, `API_KEY_SCOPES: tuple[str, ...] = ("read",)`, `MCP_REQUIRED_SCOPE: str = "read"`
  - `generate_key() -> tuple[str, str, str]` → `(prefix, secret, token)` where `token == f"paw_{prefix}.{secret}"`
  - `hash_secret(secret: str) -> str` (sha256 hex)
  - `verify_secret(secret: str, stored_hash: str) -> bool` (constant-time)
  - `parse_bearer(authorization: str | None) -> tuple[str, str] | None` → `(prefix, secret)` or `None`

- [ ] **Step 1: Add the MCP SDK dependency**

Run:
```bash
uv add "mcp>=1.28"
```
Expected: `pyproject.toml` `[project].dependencies` gains `mcp>=1.28`; `uv.lock` updates; `.venv` resolves (mcp 1.28.x).

- [ ] **Step 2: Silence mypy on the untyped MCP import**

Append to `pyproject.toml` (after the existing `[tool.mypy]` block):
```toml
[[tool.mypy.overrides]]
module = ["mcp.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Write the failing unit test**

Create `tests/unit/test_api_keys_crypto.py`:
```python
from paw.security.api_keys import (
    KEY_PREFIX,
    generate_key,
    hash_secret,
    parse_bearer,
    verify_secret,
)


def test_generate_key_shape():
    prefix, secret, token = generate_key()
    assert token == f"{KEY_PREFIX}{prefix}.{secret}"
    assert "." not in prefix and "." not in secret


def test_parse_bearer_roundtrip():
    prefix, secret, token = generate_key()
    assert parse_bearer(f"Bearer {token}") == (prefix, secret)
    assert parse_bearer(f"bearer {token}") == (prefix, secret)  # scheme is case-insensitive


def test_parse_bearer_rejects_bad_inputs():
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer nope") is None              # missing paw_ prefix
    assert parse_bearer("Bearer paw_onlyprefix") is None    # missing '.secret'
    assert parse_bearer("Bearer paw_.secret") is None       # empty prefix


def test_verify_secret():
    _, secret, _ = generate_key()
    h = hash_secret(secret)
    assert verify_secret(secret, h) is True
    assert verify_secret("wrong-secret", h) is False
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_api_keys_crypto.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.api_keys'`.

- [ ] **Step 5: Implement the helpers**

Create `src/paw/security/api_keys.py`:
```python
from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "paw_"
# Allowlisted scopes a key may carry (Phase 8 is read-only).
API_KEY_SCOPES: tuple[str, ...] = ("read",)
# Scope every MCP request must present.
MCP_REQUIRED_SCOPE = "read"


def generate_key() -> tuple[str, str, str]:
    """Return (prefix, secret, full_token). Token is shown to the user once."""
    prefix = secrets.token_hex(4)  # 8 hex chars, no '.'
    secret = secrets.token_urlsafe(32)  # urlsafe base64, no '.'
    return prefix, secret, f"{KEY_PREFIX}{prefix}.{secret}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def parse_bearer(authorization: str | None) -> tuple[str, str] | None:
    """Parse 'Bearer paw_<prefix>.<secret>' -> (prefix, secret), else None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.startswith(KEY_PREFIX):
        return None
    body = value[len(KEY_PREFIX):]
    prefix, sep, secret = body.partition(".")
    if not sep or not prefix or not secret:
        return None
    return prefix, secret
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_api_keys_crypto.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/paw/security/api_keys.py tests/unit/test_api_keys_crypto.py
git commit -m "feat(mcp): add api-key crypto helpers and MCP SDK dependency"
```

---

### Task 2: ApiKeyRepo

**Files:**
- Create: `src/paw/db/repos/api_keys.py`
- Test: `tests/integration/test_api_keys_repo.py`

**Interfaces:**
- Consumes: `paw.db.models.ApiKey` (columns: `id, user_id, prefix, hash, scopes(JSONB list), created_at, last_used, revoked_at`).
- Produces `ApiKeyRepo(session)`:
  - `create(*, user_id: uuid.UUID, prefix: str, hash: str, scopes: list[str]) -> ApiKey` (flush, no commit)
  - `by_prefix(prefix: str) -> list[ApiKey]`
  - `list_for_user(user_id: uuid.UUID) -> list[ApiKey]` (newest first)
  - `revoke(key_id: uuid.UUID, user_id: uuid.UUID) -> bool` (sets `revoked_at=now()` only if owned and not already revoked; returns whether a row changed)
  - `touch_last_used(key_id: uuid.UUID) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_api_keys_repo.py`:
```python
from paw.db.repos.api_keys import ApiKeyRepo
from paw.db.repos.users import UserRepo


async def _user(db_session):
    return await UserRepo(db_session).create(email="a@b.c", pw_hash="x", role="admin")


async def test_create_and_by_prefix(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="abc12345", hash="h", scopes=["read"])
    await db_session.commit()
    rows = await repo.by_prefix("abc12345")
    assert [r.id for r in rows] == [k.id]
    assert rows[0].scopes == ["read"]
    assert await repo.by_prefix("missing") == []


async def test_list_for_user(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    await repo.create(user_id=u.id, prefix="p1", hash="h", scopes=[])
    await repo.create(user_id=u.id, prefix="p2", hash="h", scopes=["read"])
    await db_session.commit()
    rows = await repo.list_for_user(u.id)
    assert {r.prefix for r in rows} == {"p1", "p2"}


async def test_revoke_is_idempotent_and_owner_scoped(db_session):
    u = await _user(db_session)
    other = await UserRepo(db_session).create(email="o@b.c", pw_hash="x", role="viewer")
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="p3", hash="h", scopes=["read"])
    await db_session.commit()

    assert await repo.revoke(k.id, other.id) is False  # not owner -> no change
    assert await repo.revoke(k.id, u.id) is True
    await db_session.commit()
    assert (await repo.by_prefix("p3"))[0].revoked_at is not None
    assert await repo.revoke(k.id, u.id) is False  # already revoked


async def test_touch_last_used(db_session):
    u = await _user(db_session)
    repo = ApiKeyRepo(db_session)
    k = await repo.create(user_id=u.id, prefix="p4", hash="h", scopes=["read"])
    await db_session.commit()
    await repo.touch_last_used(k.id)
    await db_session.commit()
    assert (await repo.by_prefix("p4"))[0].last_used is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_api_keys_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.repos.api_keys'`.

- [ ] **Step 3: Implement the repo**

Create `src/paw/db/repos/api_keys.py`:
```python
from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import ApiKey


class ApiKeyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self, *, user_id: uuid.UUID, prefix: str, hash: str, scopes: list[str]
    ) -> ApiKey:
        k = ApiKey(user_id=user_id, prefix=prefix, hash=hash, scopes=scopes)
        self._s.add(k)
        await self._s.flush()
        return k

    async def by_prefix(self, prefix: str) -> list[ApiKey]:
        res = await self._s.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        return list(res.scalars().all())

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiKey]:
        res = await self._s.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list(res.scalars().all())

    async def revoke(self, key_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        res = await self._s.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key_id,
                ApiKey.user_id == user_id,
                ApiKey.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )
        return bool(res.rowcount)

    async def touch_last_used(self, key_id: uuid.UUID) -> None:
        await self._s.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used=func.now())
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_api_keys_repo.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/db/repos/api_keys.py tests/integration/test_api_keys_repo.py
git commit -m "feat(mcp): add ApiKeyRepo"
```

---

### Task 3: ApiKeyService (issue / list / revoke / authenticate)

**Files:**
- Create: `src/paw/services/api_keys.py`
- Test: `tests/integration/test_api_keys_service.py`

**Interfaces:**
- Consumes: `ApiKeyRepo` (Task 2); `paw.security.api_keys` (Task 1); `paw.api.errors.ProblemError`.
- Produces:
  - `@dataclass(frozen=True) AuthedKey(id: uuid.UUID, user_id: uuid.UUID, scopes: list[str])`
  - `@dataclass(frozen=True) IssuedKey(id: uuid.UUID, prefix: str, token: str, scopes: list[str])`
  - `ApiKeyService(session)`:
    - `issue(*, user_id, scopes: list[str]) -> IssuedKey` (422 on scope outside `API_KEY_SCOPES`; commits)
    - `list(user_id) -> list[ApiKey]`
    - `revoke(*, user_id, key_id) -> None` (404 if not owned/absent; commits)
    - `authenticate(authorization: str | None) -> AuthedKey | None` (parse → by_prefix → verify sha256 → reject revoked → touch last_used + commit on success)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_api_keys_service.py`:
```python
import pytest

from paw.api.errors import ProblemError
from paw.db.repos.users import UserRepo
from paw.services.api_keys import ApiKeyService


async def _user(db_session, email="a@b.c"):
    return await UserRepo(db_session).create(email=email, pw_hash="x", role="admin")


async def test_issue_then_authenticate(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    issued = await svc.issue(user_id=u.id, scopes=["read"])
    assert issued.token.startswith("paw_")

    authed = await svc.authenticate(f"Bearer {issued.token}")
    assert authed is not None
    assert authed.user_id == u.id
    assert authed.scopes == ["read"]


async def test_issue_rejects_unknown_scope(db_session):
    u = await _user(db_session)
    await db_session.commit()
    with pytest.raises(ProblemError) as ei:
        await ApiKeyService(db_session).issue(user_id=u.id, scopes=["write"])
    assert ei.value.status == 422


async def test_authenticate_rejects_unknown_and_revoked(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    assert await svc.authenticate("Bearer paw_deadbeef.nope") is None
    assert await svc.authenticate(None) is None

    issued = await svc.issue(user_id=u.id, scopes=["read"])
    await svc.revoke(user_id=u.id, key_id=issued.id)
    assert await svc.authenticate(f"Bearer {issued.token}") is None


async def test_authenticate_rejects_wrong_secret(db_session):
    u = await _user(db_session)
    await db_session.commit()
    svc = ApiKeyService(db_session)
    issued = await svc.issue(user_id=u.id, scopes=["read"])
    prefix = issued.token.removeprefix("paw_").split(".")[0]
    assert await svc.authenticate(f"Bearer paw_{prefix}.tampered") is None


async def test_revoke_missing_raises_404(db_session):
    import uuid

    u = await _user(db_session)
    await db_session.commit()
    with pytest.raises(ProblemError) as ei:
        await ApiKeyService(db_session).revoke(user_id=u.id, key_id=uuid.uuid4())
    assert ei.value.status == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_api_keys_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.services.api_keys'`.

- [ ] **Step 3: Implement the service**

Create `src/paw/services/api_keys.py`:
```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import ApiKey
from paw.db.repos.api_keys import ApiKeyRepo
from paw.security.api_keys import (
    API_KEY_SCOPES,
    generate_key,
    hash_secret,
    parse_bearer,
    verify_secret,
)


@dataclass(frozen=True)
class AuthedKey:
    id: uuid.UUID
    user_id: uuid.UUID
    scopes: list[str]


@dataclass(frozen=True)
class IssuedKey:
    id: uuid.UUID
    prefix: str
    token: str
    scopes: list[str]


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = ApiKeyRepo(session)

    async def issue(self, *, user_id: uuid.UUID, scopes: list[str]) -> IssuedKey:
        invalid = sorted(set(scopes) - set(API_KEY_SCOPES))
        if invalid:
            raise ProblemError(
                status=422, title="Invalid scopes", detail=f"unknown scopes: {invalid}"
            )
        prefix, secret, token = generate_key()
        row = await self._repo.create(
            user_id=user_id, prefix=prefix, hash=hash_secret(secret), scopes=list(scopes)
        )
        await self._s.commit()
        return IssuedKey(id=row.id, prefix=prefix, token=token, scopes=list(scopes))

    async def list(self, user_id: uuid.UUID) -> list[ApiKey]:
        return await self._repo.list_for_user(user_id)

    async def revoke(self, *, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
        if not await self._repo.revoke(key_id, user_id):
            raise ProblemError(status=404, title="API key not found")
        await self._s.commit()

    async def authenticate(self, authorization: str | None) -> AuthedKey | None:
        parsed = parse_bearer(authorization)
        if parsed is None:
            return None
        prefix, secret = parsed
        for row in await self._repo.by_prefix(prefix):
            if row.revoked_at is None and verify_secret(secret, row.hash):
                await self._repo.touch_last_used(row.id)
                await self._s.commit()
                return AuthedKey(id=row.id, user_id=row.user_id, scopes=list(row.scopes))
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_api_keys_service.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/services/api_keys.py tests/integration/test_api_keys_service.py
git commit -m "feat(mcp): add ApiKeyService with issue/list/revoke/authenticate"
```

---

### Task 4: `/api-keys` REST surface (issue / list / revoke)

**Files:**
- Create: `src/paw/api/routers/api_keys.py`
- Modify: `src/paw/main.py` (include the router only)
- Test: `tests/api/test_api_keys.py`

**Interfaces:**
- Consumes: `ApiKeyService` (Task 3); `paw.api.deps.{current_user, db, require_csrf}`.
- Produces routes under `/api/v1/api-keys`:
  - `POST /api-keys` body `{scopes: list[str] = ["read"]}` → `201 {id, prefix, key, scopes}` (full `key` shown once). Session-auth + CSRF; scoped to `current_user`.
  - `GET /api-keys` → `200 [{id, prefix, scopes, created_at, last_used, revoked_at}]` (never the secret).
  - `DELETE /api-keys/{key_id}` → `204` (revoke own key; 404 otherwise). Session-auth + CSRF.

**Design note:** keys are per-user (FK `user_id`); the spec says "so a user can mint a key for MCP" → **self-service for any authenticated role**, scoped to the caller's own keys (not admin-only).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_api_keys.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _login(client: AsyncClient, email: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    csrf = client.cookies.get("paw_csrf")
    assert csrf is not None
    return csrf


@pytest.fixture
async def seeded(db_session):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()


@pytest.fixture
async def client(seeded, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_issue_list_revoke_roundtrip(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.post(
        "/api/v1/api-keys", json={"scopes": ["read"]}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("paw_")
    key_id = body["id"]

    r = await client.get("/api/v1/api-keys")
    assert r.status_code == 200
    listed = r.json()
    assert any(k["id"] == key_id for k in listed)
    assert all("key" not in k and "hash" not in k for k in listed)  # secret never returned

    r = await client.request(
        "DELETE", f"/api/v1/api-keys/{key_id}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 204


async def test_issue_requires_csrf(client):
    await _login(client, "admin@example.com", "pw12345")
    r = await client.post("/api/v1/api-keys", json={"scopes": ["read"]})
    assert r.status_code == 403


async def test_issue_rejects_unknown_scope(client):
    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.post(
        "/api/v1/api-keys", json={"scopes": ["write"]}, headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 422


async def test_revoke_unknown_returns_404(client):
    import uuid

    csrf = await _login(client, "admin@example.com", "pw12345")
    r = await client.request(
        "DELETE", f"/api/v1/api-keys/{uuid.uuid4()}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 404


async def test_anonymous_cannot_list(client):
    r = await client.get("/api/v1/api-keys")
    assert r.status_code == 401
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_api_keys.py -q`
Expected: FAIL — 404 on `/api/v1/api-keys` (router not mounted).

- [ ] **Step 3: Implement the router**

Create `src/paw/api/routers/api_keys.py`:
```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf
from paw.db.models import User
from paw.services.api_keys import ApiKeyService

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    scopes: list[str] = ["read"]


class ApiKeyIssued(BaseModel):
    id: str
    prefix: str
    key: str  # full secret token — shown once
    scopes: list[str]


class ApiKeyOut(BaseModel):
    id: str
    prefix: str
    scopes: list[str]
    created_at: str
    last_used: str | None
    revoked_at: str | None


@router.post("", status_code=201, response_model=ApiKeyIssued, dependencies=[Depends(require_csrf)])
async def create_api_key(
    body: ApiKeyCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> ApiKeyIssued:
    issued = await ApiKeyService(session).issue(user_id=user.id, scopes=body.scopes)
    return ApiKeyIssued(
        id=str(issued.id), prefix=issued.prefix, key=issued.token, scopes=issued.scopes
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> list[ApiKeyOut]:
    rows = await ApiKeyService(session).list(user.id)
    return [
        ApiKeyOut(
            id=str(r.id),
            prefix=r.prefix,
            scopes=list(r.scopes),
            created_at=r.created_at.isoformat(),
            last_used=r.last_used.isoformat() if r.last_used else None,
            revoked_at=r.revoked_at.isoformat() if r.revoked_at else None,
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=204, dependencies=[Depends(require_csrf)])
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(db),
) -> None:
    await ApiKeyService(session).revoke(user_id=user.id, key_id=key_id)
```

- [ ] **Step 4: Wire the router into the app**

In `src/paw/main.py`, add the import next to the other router imports:
```python
from paw.api.routers import api_keys as api_keys_router
```
and add `api_keys_router` to the `for r in (...)` include tuple (e.g. right after `users_router`):
```python
        users_router,
        api_keys_router,
        jobs_router,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_api_keys.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/paw/api/routers/api_keys.py src/paw/main.py tests/api/test_api_keys.py
git commit -m "feat(mcp): add /api-keys issue/list/revoke surface"
```

---

### Task 5: MCP tool logic + repo helpers

**Files:**
- Create: `src/paw/mcp/__init__.py`, `src/paw/mcp/tools.py`
- Modify: `src/paw/db/repos/domains.py` (add `get_by_name`), `src/paw/db/repos/articles.py` (add `get_by_slug`)
- Test: `tests/integration/test_mcp_tools.py`

**Interfaces:**
- Consumes: `retrieve()`; `LinkRepo`; `ArticleRepo`; `PostgresStorage`; `RetrievalConfig`; `EmbeddingProvider`; `CURRENT_EMBEDDING_VERSION` from `paw.vector.search`.
- Produces in `paw.mcp.tools`:
  - Pydantic models `PassageOut`, `RefOut`, `SearchResult`, `ArticleResult`, `LinkOut`, `LinksResult`.
  - `async search_wiki(session, *, domain_id, query, embedder, cfg: RetrievalConfig, embedding_version=CURRENT_EMBEDDING_VERSION, top_k: int | None = None) -> SearchResult`
  - `async get_article(session, *, domain_id, ref: str) -> ArticleResult` (raises `ValueError` if not found in domain)
  - `async list_links(session, *, domain_id, ref: str) -> LinksResult`
- Produces repo helpers:
  - `DomainRepo.get_by_name(name: str) -> Domain | None`
  - `ArticleRepo.get_by_slug(domain_id, slug) -> Article | None`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_mcp_tools.py`:
```python
import pytest

from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo
from paw.ingest.chunking import ChunkSpec
from paw.mcp import tools as mcp_tools
from paw.providers.config import RetrievalConfig
from paw.vector.embed import embed_and_write


async def _seed(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    tcp = await repo.create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:tcp", summary="reliable"
    )
    udp = await repo.create(
        domain_id=dom.id, slug="udp", title="UDP", storage_ref="b:udp", summary="datagram"
    )
    await ensure_embedding_column(db_session, dim)
    emb = StubEmbeddingProvider(dim=dim)
    await embed_and_write(
        db_session, article_id=tcp.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable ordered")],
        embedder=emb,
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=tcp.id, dst_article_id=udp.id, type="related"
    )
    await db_session.commit()
    return dom, tcp, udp, emb


async def test_search_wiki_returns_passages_and_refs(db_session):
    dom, tcp, _udp, emb = await _seed(db_session)
    out = await mcp_tools.search_wiki(
        db_session, domain_id=dom.id, query="reliable",
        embedder=emb, cfg=RetrievalConfig(k1=10, k2=10, top_n=5), embedding_version=1,
    )
    assert out.passages and any(p.slug == "tcp" for p in out.passages)
    assert any(r.slug == "tcp" for r in out.refs)


async def test_get_article_by_slug_and_id(db_session):
    dom, tcp, _udp, _emb = await _seed(db_session)
    by_slug = await mcp_tools.get_article(db_session, domain_id=dom.id, ref="tcp")
    assert by_slug.slug == "tcp" and by_slug.title == "TCP"
    by_id = await mcp_tools.get_article(db_session, domain_id=dom.id, ref=str(tcp.id))
    assert by_id.id == str(tcp.id)


async def test_get_article_cross_domain_rejected(db_session):
    dom, tcp, _udp, _emb = await _seed(db_session)
    other = await DomainRepo(db_session).create(name="other", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    with pytest.raises(ValueError):
        await mcp_tools.get_article(db_session, domain_id=other.id, ref="tcp")
    with pytest.raises(ValueError):
        await mcp_tools.get_article(db_session, domain_id=other.id, ref=str(tcp.id))


async def test_list_links_returns_typed_edges(db_session):
    dom, tcp, udp, _emb = await _seed(db_session)
    out = await mcp_tools.list_links(db_session, domain_id=dom.id, ref="tcp")
    assert out.article_id == str(tcp.id)
    assert any(e.type == "related" and e.slug == "udp" for e in out.outgoing)
    back = await mcp_tools.list_links(db_session, domain_id=dom.id, ref="udp")
    assert any(e.type == "related" and e.slug == "tcp" for e in back.backlinks)
```

Note: `get_article` reads the article body from storage via `storage_ref`. The seed uses ref `"b:tcp"` which is not a real blob — so `get_article` body read would fail. **Fix the seed to store real bodies:** replace the two `repo.create(...)` calls with bodies via `PostgresStorage`:
```python
    from paw.storage.postgres import PostgresStorage
    store = PostgresStorage(db_session)
    tcp_ref = await store.put(b"# TCP\nreliable ordered delivery", content_type="text/markdown")
    udp_ref = await store.put(b"# UDP\ndatagram", content_type="text/markdown")
    tcp = await repo.create(domain_id=dom.id, slug="tcp", title="TCP", storage_ref=tcp_ref, summary="reliable")
    udp = await repo.create(domain_id=dom.id, slug="udp", title="UDP", storage_ref=udp_ref, summary="datagram")
```
(Use this storage-backed `_seed`; keep the rest of `_seed` as written.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_mcp_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.mcp'`.

- [ ] **Step 3: Add repo helpers**

In `src/paw/db/repos/domains.py`, add to `DomainRepo`:
```python
    async def get_by_name(self, name: str) -> Domain | None:
        res = await self._s.execute(select(Domain).where(Domain.name == name))
        return res.scalar_one_or_none()
```

In `src/paw/db/repos/articles.py`, add to `ArticleRepo`:
```python
    async def get_by_slug(self, domain_id: uuid.UUID, slug: str) -> Article | None:
        res = await self._s.execute(
            select(Article).where(Article.domain_id == domain_id, Article.slug == slug)
        )
        return res.scalar_one_or_none()
```

- [ ] **Step 4: Implement the package marker and tools**

Create `src/paw/mcp/__init__.py`:
```python
```
(empty file)

Create `src/paw/mcp/tools.py`:
```python
from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.links import LinkRepo
from paw.harness.retrieve import retrieve
from paw.providers.base import EmbeddingProvider
from paw.providers.config import RetrievalConfig
from paw.storage.postgres import PostgresStorage
from paw.vector.search import CURRENT_EMBEDDING_VERSION


class PassageOut(BaseModel):
    chunk_id: str
    slug: str
    heading_path: str | None
    text: str
    score: float


class RefOut(BaseModel):
    article_id: str
    slug: str
    title: str


class SearchResult(BaseModel):
    passages: list[PassageOut]
    refs: list[RefOut]


class ArticleResult(BaseModel):
    id: str
    slug: str
    title: str
    summary: str | None
    current_rev: int
    updated_at: str
    markdown: str


class LinkOut(BaseModel):
    type: str
    article_id: str
    slug: str
    title: str


class LinksResult(BaseModel):
    article_id: str
    outgoing: list[LinkOut]
    backlinks: list[LinkOut]


async def _resolve_article(session: AsyncSession, domain_id: uuid.UUID, ref: str) -> Article:
    repo = ArticleRepo(session)
    art: Article | None
    try:
        art = await repo.get(uuid.UUID(ref))
    except ValueError:
        art = await repo.get_by_slug(domain_id, ref)
    if art is None or art.domain_id != domain_id:
        raise ValueError("article not found in domain")
    return art


async def search_wiki(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    query: str,
    embedder: EmbeddingProvider,
    cfg: RetrievalConfig,
    embedding_version: int = CURRENT_EMBEDDING_VERSION,
    top_k: int | None = None,
) -> SearchResult:
    if top_k is not None:
        cfg = cfg.model_copy(update={"top_n": int(top_k)})
    ctx = await retrieve(
        session,
        domain_id=domain_id,
        query=query,
        embedder=embedder,
        cfg=cfg,
        embedding_version=embedding_version,
        embed_model=getattr(embedder, "embedding_model", ""),
    )
    return SearchResult(
        passages=[
            PassageOut(
                chunk_id=str(p.chunk_id),
                slug=p.slug,
                heading_path=p.heading_path,
                text=p.text,
                score=p.score,
            )
            for p in ctx.passages
        ],
        refs=[RefOut(article_id=str(r.article_id), slug=r.slug, title=r.title) for r in ctx.refs],
    )


async def get_article(session: AsyncSession, *, domain_id: uuid.UUID, ref: str) -> ArticleResult:
    art = await _resolve_article(session, domain_id, ref)
    markdown = (await PostgresStorage(session).get(art.storage_ref)).decode()
    return ArticleResult(
        id=str(art.id),
        slug=art.slug,
        title=art.title,
        summary=art.summary,
        current_rev=art.current_rev,
        updated_at=art.updated_at.isoformat(),
        markdown=markdown,
    )


async def list_links(session: AsyncSession, *, domain_id: uuid.UUID, ref: str) -> LinksResult:
    art = await _resolve_article(session, domain_id, ref)
    repo = LinkRepo(session)

    def _conv(items: list) -> list[LinkOut]:  # type: ignore[type-arg]
        return [
            LinkOut(type=la.link_type, article_id=str(la.article_id), slug=la.slug, title=la.title)
            for la in items
        ]

    return LinksResult(
        article_id=str(art.id),
        outgoing=_conv(await repo.outgoing(art.id)),
        backlinks=_conv(await repo.backlinks(art.id)),
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_mcp_tools.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors. (If mypy flags the `_conv` `list` param, annotate as `list[object]` is wrong for attribute access — instead import `LinkedArticle` from `paw.db.repos.links` and type it `list[LinkedArticle]`, dropping the `# type: ignore`.)

- [ ] **Step 7: Commit**

```bash
git add src/paw/mcp/__init__.py src/paw/mcp/tools.py src/paw/db/repos/domains.py \
        src/paw/db/repos/articles.py tests/integration/test_mcp_tools.py
git commit -m "feat(mcp): add read-only MCP tool logic (search/get_article/list_links)"
```

---

### Task 6: MCP server assembly (`build_mcp`)

**Files:**
- Create: `src/paw/mcp/server.py`
- Test: `tests/unit/test_mcp_server.py`

**Interfaces:**
- Consumes: `mcp.server.fastmcp.FastMCP`; `paw.mcp.tools`; `ProviderSettingsService`; `DomainRepo.get_by_name`; `build_embedding_provider`; `SecretBox`; `get_settings`; `get_sessionmaker`; `RetrievalConfig`.
- Produces: `build_mcp() -> FastMCP` registering exactly three read tools — `search_wiki(query, domain, top_k?)`, `get_article(ref, domain)`, `list_links(article, domain)` — each opening its own session, resolving the domain by name, and returning a `dict`. Endpoint path configured to `/` so the mount at `/mcp` resolves cleanly.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_server.py`:
```python
from paw.mcp.server import build_mcp


async def test_registers_three_read_tools():
    mcp = build_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_wiki", "get_article", "list_links"}


def test_streamable_path_is_root():
    mcp = build_mcp()
    assert mcp.settings.streamable_http_path == "/"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_mcp_server.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.mcp.server'`.

- [ ] **Step 3: Implement the server**

Create `src/paw/mcp/server.py`:
```python
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from paw.config import get_settings
from paw.db.models import Domain
from paw.db.repos.domains import DomainRepo
from paw.db.session import get_sessionmaker
from paw.mcp import tools as mcp_tools
from paw.providers.config import RetrievalConfig
from paw.providers.factory import build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def _resolve_domain(session: Any, name: str) -> Domain:
    dom = await DomainRepo(session).get_by_name(name)
    if dom is None:
        raise ValueError(f"unknown domain: {name}")
    return dom


def _retrieval_for(global_retr: RetrievalConfig, dom: Domain) -> RetrievalConfig:
    overrides = dom.config.get("retrieval") if isinstance(dom.config, dict) else None
    if isinstance(overrides, dict):
        return RetrievalConfig.model_validate({**global_retr.model_dump(), **overrides})
    return global_retr


def build_mcp() -> FastMCP:
    mcp = FastMCP("paw", stateless_http=True, json_response=True)
    # Mounted at /mcp; serve the endpoint at the mount root so the URL is exactly /mcp.
    mcp.settings.streamable_http_path = "/"

    @mcp.tool(description="Hybrid search (vector + FTS + graph) over a domain's wiki. Read-only.")
    async def search_wiki(query: str, domain: str, top_k: int | None = None) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            psvc = ProviderSettingsService(session)
            pc = await psvc.get_provider()
            if pc is None:
                raise ValueError("provider not configured")
            dom = await _resolve_domain(session, domain)
            cfg = _retrieval_for(await psvc.get_retrieval(), dom)
            embedder = build_embedding_provider(pc, SecretBox(get_settings().fernet_key))
            result = await mcp_tools.search_wiki(
                session,
                domain_id=dom.id,
                query=query,
                embedder=embedder,
                cfg=cfg,
                embedding_version=await psvc.get_embedding_version(),
                top_k=top_k,
            )
            return result.model_dump()

    @mcp.tool(description="Get a domain article by id or slug. Read-only.")
    async def get_article(ref: str, domain: str) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            dom = await _resolve_domain(session, domain)
            result = await mcp_tools.get_article(session, domain_id=dom.id, ref=ref)
            return result.model_dump()

    @mcp.tool(description="List typed links (outgoing + backlinks) for an article. Read-only.")
    async def list_links(article: str, domain: str) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            dom = await _resolve_domain(session, domain)
            result = await mcp_tools.list_links(session, domain_id=dom.id, ref=article)
            return result.model_dump()

    return mcp
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_mcp_server.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/mcp/server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): assemble FastMCP server with three read-only tools"
```

---

### Task 7: MCP auth middleware + mount + lifespan

**Files:**
- Create: `src/paw/mcp/auth.py`
- Modify: `src/paw/main.py` (build + mount MCP, wire lifespan, add middleware)
- Test: `tests/api/test_mcp_auth.py`

**Interfaces:**
- Consumes: `ApiKeyService.authenticate`; `MCP_REQUIRED_SCOPE`; `get_sessionmaker`; `build_mcp`.
- Produces:
  - `MCPAuthMiddleware(app)` — pure-ASGI middleware. For `http` requests whose path starts with `/mcp`: authenticate the `Authorization` header; `None` → `401`, missing `read` scope → `403` (both `application/problem+json`); otherwise pass through untouched (streaming-safe — does **not** wrap the response body). All non-`/mcp` requests pass straight through.
  - `create_app()` mounts the MCP Streamable HTTP app at `/mcp`, runs its session-manager via the FastAPI lifespan, and installs `MCPAuthMiddleware`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_mcp_auth.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.services.api_keys import ApiKeyService


@pytest.fixture
async def issued(db_session):
    """Return (read_token, noscope_token, revoked_token) for a seeded user."""
    user = await UserRepo(db_session).create(email="k@b.c", pw_hash="x", role="admin")
    await db_session.commit()
    svc = ApiKeyService(db_session)
    read = await svc.issue(user_id=user.id, scopes=["read"])
    noscope = await svc.issue(user_id=user.id, scopes=[])
    revoked = await svc.issue(user_id=user.id, scopes=["read"])
    await svc.revoke(user_id=user.id, key_id=revoked.id)
    return read.token, noscope.token, revoked.token


@pytest.fixture
async def client(wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_missing_or_bad_key_401(client, issued):
    r = await client.post("/mcp", json={})
    assert r.status_code == 401
    r = await client.post("/mcp", json={}, headers={"Authorization": "Bearer paw_dead.beef"})
    assert r.status_code == 401


async def test_revoked_key_401(client, issued):
    _read, _noscope, revoked = issued
    r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {revoked}"})
    assert r.status_code == 401


async def test_valid_key_without_scope_403(client, issued):
    _read, noscope, _revoked = issued
    r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {noscope}"})
    assert r.status_code == 403
```

Note: these deny cases short-circuit inside the middleware **before** the MCP app runs, so they need neither the MCP lifespan nor a valid MCP handshake — plain `httpx` ASGI transport is enough. The accept path is covered by the E2E (Task 8).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_mcp_auth.py -q`
Expected: FAIL — `/mcp` returns 404 (not mounted / no middleware) instead of 401/403.

- [ ] **Step 3: Implement the middleware**

Create `src/paw/mcp/auth.py`:
```python
from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from paw.db.session import get_sessionmaker
from paw.security.api_keys import MCP_REQUIRED_SCOPE
from paw.services.api_keys import ApiKeyService

_GUARD_PREFIX = "/mcp"


class MCPAuthMiddleware:
    """Pure-ASGI guard for /mcp: Bearer api-key auth + required-scope check.

    Streaming-safe: it never wraps the downstream response (unlike
    BaseHTTPMiddleware), so Streamable-HTTP/SSE bodies stream untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(_GUARD_PREFIX):
            await self.app(scope, receive, send)
            return

        authorization: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                authorization = value.decode("latin-1")
                break

        async with get_sessionmaker()() as session:
            authed = await ApiKeyService(session).authenticate(authorization)

        if authed is None:
            await _problem(send, 401, "Unauthorized")
            return
        if MCP_REQUIRED_SCOPE not in authed.scopes:
            await _problem(send, 403, "Forbidden")
            return
        await self.app(scope, receive, send)


async def _problem(send: Send, status: int, title: str) -> None:
    body = json.dumps({"type": "about:blank", "title": title, "status": status}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 4: Mount MCP + wire lifespan + middleware in `main.py`**

In `src/paw/main.py`:

Add imports near the top:
```python
import contextlib
from collections.abc import AsyncIterator

from paw.mcp.auth import MCPAuthMiddleware
from paw.mcp.server import build_mcp
```

Rewrite `create_app()` so it builds one MCP per app, attaches the lifespan, mounts the MCP app, and installs the guard middleware. The body changes are:
```python
def create_app() -> FastAPI:
    mcp = build_mcp()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Personal AI Wiki", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def csp(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = _CSP
        return resp

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (
        auth_router,
        domains_router,
        sources_router,
        articles_router,
        setup_router,
        settings_router,
        users_router,
        api_keys_router,
        jobs_router,
        query_router,
        chat_router,
        graph_router,
        maintenance_router,
    ):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.mount("/mcp", mcp.streamable_http_app())
    app.add_middleware(MCPAuthMiddleware)
    return app
```
(Keep the existing module-level `app = create_app()` at the bottom.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_mcp_auth.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Guard against regressions in the rest of the API/web suite**

Run: `uv run pytest tests/api -q`
Expected: PASS — adding the lifespan + middleware must not break existing routes (non-`/mcp` requests pass straight through; ASGI transport does not trigger the lifespan).

- [ ] **Step 7: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/paw/mcp/auth.py src/paw/main.py tests/api/test_mcp_auth.py
git commit -m "feat(mcp): mount /mcp with api-key auth middleware and lifespan"
```

---

### Task 8: End-to-end MCP round-trip

**Files:**
- Test: `tests/e2e/test_mcp_e2e.py`

**Interfaces:**
- Consumes: `mcp.client.streamable_http.streamablehttp_client`, `mcp.ClientSession`; `uvicorn`; the seeded helpers from Task 5; `ApiKeyService`; `ProviderSettingsService`.

**Approach:** Run the real app under `uvicorn` on an ephemeral port (same event loop, as a background task), then drive it with a real MCP client over Streamable HTTP. The embedder is stubbed by monkeypatching `paw.mcp.server.build_embedding_provider` (in-process), and a provider row + a seeded, embedded corpus are committed to the test DB. This exercises auth + transport + the three tools end-to-end (acceptance criteria 1, 2, 4). Criterion 3 (401/403) is covered in Task 7.

- [ ] **Step 1: Write the E2E test**

Create `tests/e2e/test_mcp_e2e.py`:
```python
import asyncio

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.stubs import StubEmbeddingProvider

import paw.mcp.server as mcp_server
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.secrets import SecretBox
from paw.services.api_keys import ApiKeyService
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


async def _seed(db_session) -> str:
    """Seed provider + corpus + an api key; return the full bearer token."""
    await ProviderSettingsService(db_session, box=SecretBox(_FERNET)).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    store = PostgresStorage(db_session)
    repo = ArticleRepo(db_session)
    tcp_ref = await store.put(b"# TCP\nreliable ordered delivery", content_type="text/markdown")
    udp_ref = await store.put(b"# UDP\ndatagram", content_type="text/markdown")
    tcp = await repo.create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref=tcp_ref, summary="reliable"
    )
    udp = await repo.create(
        domain_id=dom.id, slug="udp", title="UDP", storage_ref=udp_ref, summary="datagram"
    )
    await ensure_embedding_column(db_session, 8)
    await embed_and_write(
        db_session, article_id=tcp.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable ordered")],
        embedder=StubEmbeddingProvider(dim=8),
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=tcp.id, dst_article_id=udp.id, type="related"
    )
    user = await UserRepo(db_session).create(email="mcp@b.c", pw_hash="x", role="admin")
    await db_session.commit()
    issued = await ApiKeyService(db_session).issue(user_id=user.id, scopes=["read"])
    return issued.token


async def test_mcp_round_trip(db_session, wired_settings, monkeypatch):
    monkeypatch.setattr(
        mcp_server, "build_embedding_provider", lambda pc, box: StubEmbeddingProvider(dim=8)
    )
    token = await _seed(db_session)

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        while not server.started:  # wait for the socket to bind
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/mcp"
        headers = {"Authorization": f"Bearer {token}"}

        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "search_wiki",
                    "get_article",
                    "list_links",
                }

                search = await session.call_tool(
                    "search_wiki", {"query": "reliable", "domain": "net"}
                )
                assert search.structuredContent is not None
                assert any(p["slug"] == "tcp" for p in search.structuredContent["passages"])

                article = await session.call_tool(
                    "get_article", {"ref": "tcp", "domain": "net"}
                )
                assert article.structuredContent is not None
                assert article.structuredContent["slug"] == "tcp"
                assert "reliable" in article.structuredContent["markdown"]

                links = await session.call_tool(
                    "list_links", {"article": "tcp", "domain": "net"}
                )
                assert links.structuredContent is not None
                assert any(
                    e["type"] == "related" and e["slug"] == "udp"
                    for e in links.structuredContent["outgoing"]
                )
    finally:
        server.should_exit = True
        await serve_task
```

- [ ] **Step 2: Run the E2E test**

Run: `uv run pytest tests/e2e/test_mcp_e2e.py -q`
Expected: PASS (1 passed). If the client errors with a 307 redirect to `/mcp/`, change `url` to end with a trailing slash (`…/mcp/`); the auth middleware guards both forms.

- [ ] **Step 3: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors (the test file lives under `tests/`, which mypy's `src`-scoped run does not type-check, but ruff does lint it).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_mcp_e2e.py
git commit -m "test(mcp): e2e search_wiki -> get_article -> list_links round-trip"
```

---

### Task 9: Docs refresh + full CI

**Files:**
- Modify: `docs/wiki/*` (regenerated via iwiki)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — entire suite green (new MCP + api-key tests included).

- [ ] **Step 2: Run the complete CI gate**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: all three pass (mirrors `.github/workflows/ci.yml`).

- [ ] **Step 3: Regenerate the wiki pages for the changed sources**

Invoke the iwiki ingest skill for each new/changed source area, then lint:
```
iwiki:iwiki-ingest src/paw/mcp
iwiki:iwiki-ingest src/paw/services/api_keys.py
iwiki:iwiki-ingest src/paw/security/api_keys.py
iwiki:iwiki-ingest src/paw/api/routers/api_keys.py
```
Then run `/iwiki-lint`.
Expected: a new/updated MCP wiki page plus refreshed `security.md` / `api.md`; no broken `[[refs]]`, no orphans/stale pages.

- [ ] **Step 4: Commit docs**

```bash
git add docs/wiki
git commit -m "docs(wiki): document Phase 8 MCP server and api-keys"
```

- [ ] **Step 5: Open the PR**

Use **@skill:git-workflow** to push the `dev/*` branch and open a PR into `master` summarizing: read-only MCP server at `/mcp` (Streamable HTTP, api-key auth), three tools (`search_wiki`/`get_article`/`list_links`), and the `/api-keys` issue/list/revoke surface. After the PR is created, remove the branch's worktree if one was used.

---

## Acceptance Criteria → Coverage Map

1. **Client authenticates with a valid key and lists exactly the three read-only tools; no write tool.** → Task 6 (`list_tools` == 3), Task 8 (E2E `list_tools` over the wire). `build_mcp` registers only read tools.
2. **`search_wiki` returns ranked passages with refs; `get_article` returns the article; `list_links` returns typed edges.** → Task 5 (integration on each), Task 8 (E2E round-trip).
3. **Revoked/unknown key → 401; key lacking required scope → 403.** → Task 7 (`tests/api/test_mcp_auth.py`).
4. **Requests scoped to the specified domain; another domain's content is not returned.** → Task 5 (`test_get_article_cross_domain_rejected`; tools take `domain_id` and `retrieve`/`LinkRepo` filter by it), Task 8 (E2E uses `domain="net"`).

## Tests → Spec Map

- **Unit:** `test_api_keys_crypto.py` (parse/verify/generate), `test_mcp_server.py` (tool registration/schema). ✔ "tool input/output schemas + serialization; api-key parse/verify".
- **Integration (testcontainers):** `test_api_keys_repo.py`, `test_api_keys_service.py`, `test_mcp_tools.py` (tools vs seeded corpus; domain enforcement). ✔
- **API (httpx):** `test_api_keys.py` (issue/list/revoke), `test_mcp_auth.py` (auth accept/reject 401/403). ✔
- **E2E:** `test_mcp_e2e.py` (`search_wiki` → `get_article` → `list_links`). ✔

## Risks / Notes

- **One retrieval implementation:** `search_wiki` calls `paw.harness.retrieve.retrieve` — the same function the REST `/query` path uses pre-LLM — so there is no second retrieval code path to drift.
- **Streaming-safe middleware:** `MCPAuthMiddleware` is pure-ASGI and never buffers the downstream response, so Streamable-HTTP responses stream through (Traefik must likewise not buffer — same requirement as SSE).
- **No migration:** `api_keys` exists from Phase 1 (present in the alembic baseline and the conftest `TRUNCATE` list). The model's `scopes` is `JSONB` (list), not the `text[]` shown in an older LLD snippet — the model is authoritative.
- **Mount path / trailing slash:** the MCP endpoint is served at the mount root (`streamable_http_path="/"`), exposing `/mcp`. If a client hits a `307 → /mcp/`, use the trailing-slash URL; the guard matches both.
- **Per-call session:** each tool and each middleware invocation opens its own `AsyncSession` from the process-global sessionmaker (reset in tests by `wired_settings`); `authenticate` commits only the `last_used` touch.
