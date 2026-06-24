---
title: "Phase 7 — Query-cache + suggestions implementation plan"
phase: 7
status: design
date: 2026-06-24
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-7-query-cache-design.md
review:
  plan_hash: 22674f1a6e57b257
  spec_hash: 517ac36128dd837d
  last_run: 2026-06-24
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: CRITICAL
      section: "Task 6: Stale-marking — repo method + article-aware seam + call sites"
      section_hash: 42ebf2515820b6c4
      text: >-
        Spec lists ingest/fix/format for eager stale-marking (In scope + acceptance
        criterion 3), but the plan wired only fix/format. Re-ingest of a cited article
        (upsert_article bumps current_rev) would not invalidate dependents. Fixed: ingest.py
        now calls mark_articles_cache_stale(session, [art.id]) after Stage C, and a re-ingest
        integration test asserts the dependent entry goes stale.
      verdict: fixed
      verdict_at: 2026-06-24
    - id: F-002
      phase: coverage
      severity: WARNING
      section: "Task 12: E2E — query → cached → edit cited article → stale → refresh → fresh"
      section_hash: 122c9e6d390fde3f
      text: >-
        E2E exercised only the Format edit path, masking the ingest gap (F-001). Fixed: Task 6
        now includes a re-ingest invalidation integration test covering criterion 3's ingest path.
      verdict: fixed
      verdict_at: 2026-06-24
    - id: F-003
      phase: consistency
      severity: WARNING
      section: "Task 11: GC — TTL cleanup of expired cache entries"
      section_hash: 0f4388bcbc77d3b8
      text: >-
        Plan's gc_housekeeping no-regression claim was not validated against the actual fixtures.
        Verified: existing tests/integration/test_gc_housekeeping.py uses wired_settings, so
        get_settings()/get_query_cache() resolve defaults and delete_expired touches only
        query_cache rows (empty in those tests) — no regression. Task 11 Step 6 re-runs that test
        as the gate.
      verdict: accepted
      verdict_at: 2026-06-24
    - id: F-004
      phase: coverage
      severity: INFO
      section: "Task 5: QueryCacheRepo (upsert + exact/ANN lookup + touch)"
      section_hash: 87ff54f813a97e23
      text: >-
        Cache lookup is not filtered by embedding_version; after a model/version bump without a
        dim change (the managed column is only truncated on a dim change), same-dim embeddings
        from the old model could still ANN-match. Spec mandates only dim-change handling, so this
        is within scope; noted as a freshness edge for a future phase.
      verdict: accepted
      verdict_at: 2026-06-24
    - id: F-005
      phase: consistency
      severity: INFO
      section: "Task 8: API — cache-aware query JSON path + ?refresh"
      section_hash: 8c16c54b625cf901
      text: >-
        Existing test_query_api.py::test_query_response_shape_valid asserts the exact response
        key set; the plan correctly updates it to include stale/cached (Task 8 Step 5). Confirmed
        accurate, no further action.
      verdict: accepted
      verdict_at: 2026-06-24
---

# Phase 7 — Query-cache + Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut LLM + retrieval load by serving repeated/semantically-near queries from a per-domain answer cache, eagerly invalidated when cited articles change, with as-you-type suggestions.

**Architecture:** A new `query_cache` table (per-domain, `UNIQUE(domain_id, query_norm)`, managed `query_embedding vector(dim)` like `chunks.embedding`) plus a `query_cache_articles` dependency table. `QueryService.answer_cached()` checks the cache before retrieval (exact-norm fast-path, then semantic ANN ≥ `sim_threshold`); a fresh hit returns `answer_md` + `refs` with **no LLM call**; a miss runs the full Phase 3 path then upserts the answer with its cited-article dependencies. Article writes (fix/format) mark dependent entries `stale=true` transactionally via the existing cache seam; stale hits are served flagged with a Refresh action. `gc_housekeeping` removes TTL-expired entries.

**Tech Stack:** Python 3.12 · `uv` · FastAPI · async SQLAlchemy 2.0 · PostgreSQL 16 + `pgvector` (HNSW + cosine) · Redis · Jinja2 + HTMX · pytest + testcontainers.

## Global Constraints

- **Branch:** all work lands on `dev/paw-phase-7` (already exists on origin); open a PR into `master`. Never commit to `master`.
- **Run everything through `uv`:** `uv run ruff check .` → `uv run mypy src` → `uv run pytest -q` must all pass (CI runs exactly this). Ruff pinned `0.15.18`; selects `E,F,I,UP,B`; line length 100. mypy is strict.
- **Atomicity:** exactly one `session.commit()` per operation, at the **service layer**. Repos and storage **never commit**.
- **Per-domain isolation:** cache is per-domain — every lookup/upsert/suggest is scoped by `domain_id`. No cross-domain leakage.
- **Chat is never cached** (only the `query` op). Do not touch the chat path.
- **Cached content is sanitized on render** like any answer (`render_markdown` in the web layer) — never store or emit raw HTML.
- **`query_embedding` is dim-locked** like `chunks.embedding` — created/rebuilt at runtime in `db/managed.py`, never in an alembic migration. A dim change clears the cache.
- **Stale-marking must be transactional** with the article upsert — reuse the seam (`services/cache_seam.py`), don't add a separate eventual path.
- **Errors:** raise `ProblemError(status, title, detail)`; `IntegrityError` auto-maps to 409.
- **Tests need Docker** for `integration`/`api`/`e2e` layers (testcontainers Postgres `pgvector/pgvector:pg16` + Redis). The alembic baseline (`head`, includes the new `0005`) is applied once per session.

---

## File Structure

**Create:**
- `alembic/versions/0005_phase7_query_cache.py` — `query_cache` + `query_cache_articles` tables (no vector column; HNSW added at runtime).
- `src/paw/db/repos/query_cache.py` — `QueryCacheRepo` (raw-SQL vector ops + JSONB (de)serialization).
- `src/paw/services/query_cache.py` — pure helpers: `normalize_query`, `is_similar_enough`, `extract_dependencies`.
- `src/paw/api/web/templates/_suggestions.html` — suggestion-list partial.
- Test files (one per task, see tasks below).

**Modify:**
- `src/paw/providers/config.py` — `QueryCacheConfig` + `QUERY_CACHE_KEY`.
- `src/paw/services/provider_settings.py` — `get_query_cache()`.
- `src/paw/db/models.py` — `QueryCache` + `QueryCacheArticle` ORM models.
- `src/paw/db/managed.py` — extend `ensure_embedding_column` / `rebuild_embedding_column` to also manage `query_cache.query_embedding`.
- `src/paw/services/cache_seam.py` — replace the domain no-op with article-aware stale-marking.
- `src/paw/harness/ops/fix.py`, `src/paw/harness/ops/format.py` — update the seam call site (pass `art.id`).
- `src/paw/services/query.py` — `_resolve()` refactor + `answer_cached()` + `CachedAnswer`.
- `src/paw/api/routers/query.py` — cache-aware JSON path, `?refresh=1`, `stale`/`cached` fields, `GET .../suggest`.
- `src/paw/api/web/routes.py` — cache-aware `web_query`, `web_suggest`.
- `src/paw/api/web/templates/query.html` — suggestions dropdown.
- `src/paw/api/web/templates/_query_result.html` — stale badge + Refresh button.
- `src/paw/jobs/tasks.py` — `gc_housekeeping` TTL cleanup of `query_cache`.
- `tests/conftest.py` — drop the managed `query_cache.query_embedding` column between tests.

---

### Task 1: QueryCacheConfig + get_query_cache

**Files:**
- Modify: `src/paw/providers/config.py`
- Modify: `src/paw/services/provider_settings.py`
- Test: `tests/unit/test_query_cache_config.py`, `tests/integration/test_query_cache_config.py`

**Interfaces:**
- Consumes: nothing (leaf config).
- Produces:
  - `QUERY_CACHE_KEY = "query_cache"` (str constant in `providers/config.py`).
  - `class QueryCacheConfig(BaseModel)` with fields `enabled: bool = True`, `sim_threshold: float = 0.85`, `ttl: int = 1209600`, `suggest_top_k: int = 5`.
  - `ProviderSettingsService.get_query_cache() -> QueryCacheConfig`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_query_cache_config.py`:

```python
from paw.providers.config import QueryCacheConfig


def test_defaults():
    c = QueryCacheConfig()
    assert c.enabled is True
    assert c.sim_threshold == 0.85
    assert c.ttl == 1209600  # 14 days in seconds
    assert c.suggest_top_k == 5


def test_override_merge_keeps_other_defaults():
    base = QueryCacheConfig()
    merged = QueryCacheConfig.model_validate({**base.model_dump(), "sim_threshold": 0.9})
    assert merged.sim_threshold == 0.9
    assert merged.enabled is True
    assert merged.ttl == 1209600
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_query_cache_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'QueryCacheConfig'`.

- [ ] **Step 3: Add the config model + key**

In `src/paw/providers/config.py`, add the key constant next to the others (after `EMBEDDING_KEY = "embedding"`):

```python
QUERY_CACHE_KEY = "query_cache"
```

Add the model after `EmbeddingConfig`:

```python
class QueryCacheConfig(BaseModel):
    enabled: bool = True
    sim_threshold: float = 0.85  # cosine sim floor for a semantic (ANN) cache hit
    ttl: int = 1_209_600  # seconds (14 days) an idle entry survives before GC removes it
    suggest_top_k: int = 5  # max as-you-type suggestions returned per query
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `uv run pytest tests/unit/test_query_cache_config.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing integration test**

Create `tests/integration/test_query_cache_config.py`:

```python
from paw.config import get_settings
from paw.db.repos.settings import SettingsRepo
from paw.providers.config import QUERY_CACHE_KEY
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def test_get_query_cache_default(db_session, wired_settings):
    svc = ProviderSettingsService(db_session, box=SecretBox(get_settings().fernet_key))
    cfg = await svc.get_query_cache()
    assert cfg.sim_threshold == 0.85
    assert cfg.enabled is True


async def test_get_query_cache_global_override(db_session, wired_settings):
    await SettingsRepo(db_session).upsert({QUERY_CACHE_KEY: {"sim_threshold": 0.7, "enabled": False}})
    svc = ProviderSettingsService(db_session, box=SecretBox(get_settings().fernet_key))
    cfg = await svc.get_query_cache()
    assert cfg.sim_threshold == 0.7
    assert cfg.enabled is False
    assert cfg.suggest_top_k == 5  # untouched default
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_config.py -q`
Expected: FAIL with `AttributeError: 'ProviderSettingsService' object has no attribute 'get_query_cache'`.

- [ ] **Step 7: Add get_query_cache**

In `src/paw/services/provider_settings.py`, extend the import block from `paw.providers.config` to include `QUERY_CACHE_KEY` and `QueryCacheConfig`, then add the method after `get_maintenance`:

```python
    async def get_query_cache(self) -> QueryCacheConfig:
        raw = (await self._all()).get(QUERY_CACHE_KEY)
        return QueryCacheConfig.model_validate(raw) if raw else QueryCacheConfig()
```

- [ ] **Step 8: Run both test files + lint/type**

Run: `uv run pytest tests/unit/test_query_cache_config.py tests/integration/test_query_cache_config.py -q`
Expected: PASS (4 passed).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/paw/providers/config.py src/paw/services/provider_settings.py tests/unit/test_query_cache_config.py tests/integration/test_query_cache_config.py
git commit -m "feat(config): QueryCacheConfig + get_query_cache (Phase 7)"
```

---

### Task 2: Migration + ORM models

**Files:**
- Create: `alembic/versions/0005_phase7_query_cache.py`
- Modify: `src/paw/db/models.py`
- Test: `tests/integration/test_query_cache_migration.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Tables `query_cache` (cols `id, domain_id, query_norm, answer_md, refs, passages, model, prompt_version, stale, hit_count, last_hit_at, created_at`; `UNIQUE(domain_id, query_norm)`; index `ix_query_cache_domain_stale` on `(domain_id, stale)`) and `query_cache_articles` (cols `cache_id, article_id, rev`; PK `(cache_id, article_id)`; index `ix_query_cache_articles_article_id`).
  - ORM models `QueryCache`, `QueryCacheArticle` in `db/models.py`. `query_embedding` is **not** mapped (managed column, like `chunks.embedding`).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_migration.py`:

```python
from sqlalchemy import text


async def test_query_cache_columns_exist(db_session):
    cols = set(
        (
            await db_session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='query_cache'")
            )
        ).scalars()
    )
    assert {
        "id", "domain_id", "query_norm", "answer_md", "refs", "passages",
        "model", "prompt_version", "stale", "hit_count", "last_hit_at", "created_at",
    } <= cols


async def test_query_cache_articles_columns_exist(db_session):
    cols = set(
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='query_cache_articles'"
                )
            )
        ).scalars()
    )
    assert {"cache_id", "article_id", "rev"} <= cols


async def test_unique_domain_query_norm(db_session):
    rows = (
        await db_session.execute(
            text(
                "SELECT constraint_type FROM information_schema.table_constraints "
                "WHERE table_name='query_cache' AND constraint_type='UNIQUE'"
            )
        )
    ).scalars().all()
    assert "UNIQUE" in rows
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_migration.py -q`
Expected: FAIL — assertions fail (table `query_cache` does not exist, so column sets are empty).

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0005_phase7_query_cache.py`:

```python
from alembic import op

revision = "0005_phase7_query_cache"
down_revision = "0004_phase5_backlink_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # query_cache: per-domain answer cache. The `query_embedding vector(dim)` column
    # + its HNSW index are NOT created here — db/managed.py adds them at runtime
    # because dim depends on the configured provider (same pattern as chunks.embedding).
    op.execute("""
    CREATE TABLE query_cache (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      query_norm text NOT NULL,
      answer_md text NOT NULL,
      refs jsonb NOT NULL DEFAULT '[]',
      passages jsonb NOT NULL DEFAULT '[]',
      model text NOT NULL DEFAULT '',
      prompt_version text NOT NULL DEFAULT '',
      stale boolean NOT NULL DEFAULT false,
      hit_count int NOT NULL DEFAULT 0,
      last_hit_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, query_norm))
    """)
    op.execute("CREATE INDEX ix_query_cache_domain_stale ON query_cache(domain_id, stale)")

    op.execute("""
    CREATE TABLE query_cache_articles (
      cache_id uuid NOT NULL REFERENCES query_cache(id) ON DELETE CASCADE,
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      rev int NOT NULL DEFAULT 0,
      PRIMARY KEY (cache_id, article_id))
    """)
    op.execute(
        "CREATE INDEX ix_query_cache_articles_article_id ON query_cache_articles(article_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS query_cache_articles CASCADE")
    op.execute("DROP TABLE IF EXISTS query_cache CASCADE")
```

- [ ] **Step 4: Add the ORM models**

In `src/paw/db/models.py`, add after the `ChatMessage` class (and before the trailing `_biginteger` / `_string` lines):

```python
class QueryCache(Base):
    __tablename__ = "query_cache"
    # NOTE: `query_embedding vector(dim)` is a managed/raw column (db/managed.py +
    # QueryCacheRepo raw SQL); intentionally NOT ORM-mapped, like chunks.embedding.
    __table_args__ = (UniqueConstraint("domain_id", "query_norm"),)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    query_norm: Mapped[str] = mapped_column(Text, nullable=False)
    answer_md: Mapped[str] = mapped_column(Text, nullable=False)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    passages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QueryCacheArticle(Base):
    __tablename__ = "query_cache_articles"
    cache_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("query_cache.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    rev: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
```

(All referenced symbols — `UniqueConstraint`, `Text`, `JSONB`, `Boolean`, `Integer`, `DateTime`, `UUID`, `ForeignKey`, `func`, `Mapped`, `mapped_column`, `Any`, `datetime`, `uuid` — are already imported at the top of `models.py`.)

- [ ] **Step 5: Run the test to verify it passes**

The baseline is applied once per session via `tests/conftest.py::_migrate` (`alembic upgrade head`), which now includes `0005`.

Run: `uv run pytest tests/integration/test_query_cache_migration.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Verify mypy sees the models + lint**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/0005_phase7_query_cache.py src/paw/db/models.py tests/integration/test_query_cache_migration.py
git commit -m "feat(db): query_cache + query_cache_articles tables + ORM models (Phase 7)"
```

---

### Task 3: Managed query_embedding column (dim-locked + dim-change clears cache)

**Files:**
- Modify: `src/paw/db/managed.py`
- Modify: `tests/conftest.py`
- Test: `tests/integration/test_query_cache_managed.py`

**Interfaces:**
- Consumes: `query_cache` table (Task 2).
- Produces (behavioural, same public signatures):
  - `ensure_embedding_column(session, dim)` now ALSO adds `query_cache.query_embedding vector(dim)` + HNSW index `ix_query_cache_embedding_hnsw` (idempotent).
  - `rebuild_embedding_column(session, dim)` now ALSO drops that index/column, `TRUNCATE query_cache CASCADE` (clears recomputable cache on a dim change), re-adds the column at the new dim, recreates the index.
  - The chunks↔query_cache embedding dim invariant always holds (they are ensured/rebuilt together in one transaction).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_managed.py`:

```python
from sqlalchemy import text

from paw.db.managed import ensure_embedding_column, rebuild_embedding_column
from paw.db.repos.domains import DomainRepo


async def _qc_typmod(session):
    return (
        await session.execute(
            text(
                "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "WHERE c.relname='query_cache' AND a.attname='query_embedding' "
                "AND NOT a.attisdropped"
            )
        )
    ).scalar_one_or_none()


async def test_ensure_creates_query_cache_embedding(db_session):
    await ensure_embedding_column(db_session, 8)
    assert await _qc_typmod(db_session) == 8


async def test_rebuild_changes_dim_and_clears_cache(db_session):
    await ensure_embedding_column(db_session, 8)
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await db_session.execute(
        text(
            "INSERT INTO query_cache (domain_id, query_norm, answer_md, query_embedding) "
            "VALUES (:d, 'q', 'a', CAST(:v AS vector))"
        ),
        {"d": str(dom.id), "v": "[" + ",".join(["0.1"] * 8) + "]"},
    )
    await db_session.flush()

    await rebuild_embedding_column(db_session, 16)

    assert await _qc_typmod(db_session) == 16
    remaining = (await db_session.execute(text("SELECT count(*) FROM query_cache"))).scalar_one()
    assert remaining == 0  # dim change clears the recomputable cache
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_managed.py -q`
Expected: FAIL — `test_ensure_creates_query_cache_embedding` asserts `None == 8` (column not created yet).

- [ ] **Step 3: Extend managed.py**

In `src/paw/db/managed.py`, add the index constant near `_HNSW_INDEX`:

```python
_QC_HNSW_INDEX = "ix_query_cache_embedding_hnsw"
```

Add two private helpers at the bottom of the module:

```python
async def _ensure_query_cache_embedding(session: AsyncSession, dim: int) -> None:
    # dim validated by the caller; safe to interpolate (DDL type modifiers cannot bind).
    await session.execute(
        text(f"ALTER TABLE query_cache ADD COLUMN IF NOT EXISTS query_embedding vector({dim})")
    )
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )


async def _rebuild_query_cache_embedding(session: AsyncSession, dim: int) -> None:
    # Cached answers are recomputable; a dim change drops the column AND clears the
    # cache (old query_embeddings are at the wrong dim). CASCADE clears query_cache_articles.
    await session.execute(text(f"DROP INDEX IF EXISTS {_QC_HNSW_INDEX}"))
    await session.execute(text("ALTER TABLE query_cache DROP COLUMN IF EXISTS query_embedding"))
    await session.execute(text("TRUNCATE query_cache CASCADE"))
    await session.execute(text(f"ALTER TABLE query_cache ADD COLUMN query_embedding vector({dim})"))
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )
```

Then call them inside the two public functions, just before their final `await session.flush()`:

In `ensure_embedding_column`, after the `CREATE INDEX ... {_HNSW_INDEX}` execute and before `await session.flush()`:

```python
    await _ensure_query_cache_embedding(session, dim)
    await session.flush()
```

In `rebuild_embedding_column`, after the chunks `CREATE INDEX ... {_HNSW_INDEX}` execute and before `await session.flush()`:

```python
    await _rebuild_query_cache_embedding(session, dim)
    await session.flush()
```

- [ ] **Step 4: Update conftest cleanup**

In `tests/conftest.py`, inside the `_clean_db` fixture, after the two existing chunks-column drop lines, add the query_cache equivalents:

```python
        await conn.execute(text("DROP INDEX IF EXISTS ix_query_cache_embedding_hnsw"))
        await conn.execute(text("ALTER TABLE query_cache DROP COLUMN IF EXISTS query_embedding"))
```

(The existing `TRUNCATE ... domains ... CASCADE` already clears `query_cache`/`query_cache_articles` rows via the `domain_id` FK, so the row list needs no change.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_managed.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the existing managed/embedding tests (no regression)**

Run: `uv run pytest tests/integration/test_managed_migration.py tests/integration/test_embedding_version.py -q`
Expected: PASS (existing chunks behaviour unchanged).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/paw/db/managed.py tests/conftest.py tests/integration/test_query_cache_managed.py
git commit -m "feat(db): manage query_cache.query_embedding alongside chunks (dim-locked, cleared on dim change)"
```

---

### Task 4: Pure cache helpers

**Files:**
- Create: `src/paw/services/query_cache.py`
- Test: `tests/unit/test_query_norm.py`

**Interfaces:**
- Consumes: `Ref` from `paw.harness.retrieve`.
- Produces:
  - `normalize_query(q: str) -> str` — lower + trim + collapse internal whitespace.
  - `is_similar_enough(sim: float, threshold: float) -> bool` — `sim >= threshold`.
  - `extract_dependencies(refs: list[Ref]) -> list[uuid.UUID]` — deduped cited `article_id`s, first-seen order.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_query_norm.py`:

```python
import uuid

from paw.harness.retrieve import Ref
from paw.services.query_cache import extract_dependencies, is_similar_enough, normalize_query


def test_normalize_lowercases_trims_and_collapses_ws():
    assert normalize_query("  Hello   World ") == "hello world"
    assert normalize_query("TCP\tReliable\n delivery") == "tcp reliable delivery"
    assert normalize_query("Same") == normalize_query("  same ")


def test_is_similar_enough_boundary():
    assert is_similar_enough(0.9, 0.85) is True
    assert is_similar_enough(0.85, 0.85) is True
    assert is_similar_enough(0.8499, 0.85) is False


def test_extract_dependencies_dedupes_preserving_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    refs = [
        Ref(article_id=a, slug="x", title="X"),
        Ref(article_id=a, slug="x", title="X"),
        Ref(article_id=b, slug="y", title="Y"),
    ]
    assert extract_dependencies(refs) == [a, b]


def test_extract_dependencies_empty():
    assert extract_dependencies([]) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_query_norm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paw.services.query_cache'`.

- [ ] **Step 3: Write the helpers**

Create `src/paw/services/query_cache.py`:

```python
from __future__ import annotations

import uuid

from paw.harness.retrieve import Ref


def normalize_query(q: str) -> str:
    """Canonical form for the exact-norm cache key: lowercase, trimmed, ws-collapsed."""
    return " ".join(q.lower().split())


def is_similar_enough(sim: float, threshold: float) -> bool:
    """A semantic (ANN) hit counts only when cosine similarity clears the threshold."""
    return sim >= threshold


def extract_dependencies(refs: list[Ref]) -> list[uuid.UUID]:
    """The cited article_ids a cached answer depends on (deduped, first-seen order)."""
    seen: dict[uuid.UUID, None] = {}
    for r in refs:
        seen.setdefault(r.article_id, None)
    return list(seen)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_query_norm.py -q`
Expected: PASS (4 passed).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/paw/services/query_cache.py tests/unit/test_query_norm.py
git commit -m "feat(cache): pure query-cache helpers (normalize, sim-threshold, deps)"
```

---

### Task 5: QueryCacheRepo (upsert + exact/ANN lookup + touch)

**Files:**
- Create: `src/paw/db/repos/query_cache.py`
- Test: `tests/integration/test_query_cache_repo.py`

**Interfaces:**
- Consumes: `query_cache` table + managed `query_embedding` column (Tasks 2–3); `Ref`, `Passage` from `paw.harness.retrieve`.
- Produces:
  - `@dataclass(frozen=True) CacheEntry(id: uuid.UUID, answer_md: str, refs: list[Ref], passages: list[Passage], stale: bool)`.
  - `@dataclass(frozen=True) AnnHit(entry: CacheEntry, sim: float)`.
  - `class QueryCacheRepo(session)` with:
    - `upsert(*, domain_id, query_norm, query_vector: list[float], answer_md, refs: list[Ref], passages: list[Passage], model: str, prompt_version: str, article_deps: list[uuid.UUID]) -> uuid.UUID`
    - `get_exact(domain_id, query_norm) -> CacheEntry | None`
    - `get_ann(domain_id, query_vector: list[float]) -> AnnHit | None`
    - `touch_hit(cache_id) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_repo.py`:

```python
import uuid

from sqlalchemy import text

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.harness.retrieve import Passage, Ref


async def _setup(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    return dom, art


async def test_upsert_then_exact_and_ann(db_session):
    dom, art = await _setup(db_session)
    repo = QueryCacheRepo(db_session)
    vec = [0.1] * 8
    refs = [Ref(article_id=art.id, slug="tcp", title="TCP")]
    passages = [
        Passage(chunk_id=uuid.uuid4(), article_id=art.id, slug="tcp", heading_path="R", text="t", score=1.0)
    ]
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="what is tcp", query_vector=vec, answer_md="A",
        refs=refs, passages=passages, model="m", prompt_version="v1", article_deps=[art.id],
    )
    await db_session.commit()

    exact = await repo.get_exact(dom.id, "what is tcp")
    assert exact is not None
    assert exact.answer_md == "A"
    assert exact.refs[0].slug == "tcp"
    assert exact.passages[0].article_id == art.id
    assert exact.stale is False

    ann = await repo.get_ann(dom.id, vec)
    assert ann is not None
    assert ann.sim > 0.99  # identical vector -> cosine sim ~1.0

    dep = (
        await db_session.execute(
            text("SELECT article_id FROM query_cache_articles WHERE cache_id=:c"), {"c": str(cid)}
        )
    ).scalar_one()
    assert uuid.UUID(str(dep)) == art.id


async def test_exact_miss_returns_none(db_session):
    dom, _ = await _setup(db_session)
    assert await QueryCacheRepo(db_session).get_exact(dom.id, "nope") is None


async def test_upsert_conflict_updates_in_place(db_session):
    dom, art = await _setup(db_session)
    repo = QueryCacheRepo(db_session)
    vec = [0.2] * 8
    c1 = await repo.upsert(
        domain_id=dom.id, query_norm="q", query_vector=vec, answer_md="first",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    c2 = await repo.upsert(
        domain_id=dom.id, query_norm="q", query_vector=vec, answer_md="second",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[art.id],
    )
    await db_session.commit()
    assert c1 == c2  # same (domain_id, query_norm) row
    exact = await repo.get_exact(dom.id, "q")
    assert exact is not None and exact.answer_md == "second"


async def test_touch_hit_increments(db_session):
    dom, _ = await _setup(db_session)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="q", query_vector=[0.3] * 8, answer_md="A",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    await repo.touch_hit(cid)
    await db_session.commit()
    hc = (
        await db_session.execute(text("SELECT hit_count FROM query_cache WHERE id=:i"), {"i": str(cid)})
    ).scalar_one()
    assert hc == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_repo.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paw.db.repos.query_cache'`.

- [ ] **Step 3: Write the repo**

Create `src/paw/db/repos/query_cache.py`:

```python
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.harness.retrieve import Passage, Ref


@dataclass(frozen=True)
class CacheEntry:
    id: uuid.UUID
    answer_md: str
    refs: list[Ref]
    passages: list[Passage]
    stale: bool


@dataclass(frozen=True)
class AnnHit:
    entry: CacheEntry
    sim: float


def _vec_literal(vec: list[float]) -> str:
    parts: list[str] = []
    for x in vec:
        f = float(x)
        if not math.isfinite(f):
            raise ValueError(f"query embedding contains non-finite value: {f!r}")
        parts.append(repr(f))
    return "[" + ",".join(parts) + "]"


def _as_list(v: Any) -> list[dict[str, Any]]:
    # asyncpg may return a JSONB column as an already-parsed list or as raw JSON text.
    if isinstance(v, list):
        return v
    return list(json.loads(v)) if v else []


def _ref_dict(r: Ref) -> dict[str, Any]:
    return {"article_id": str(r.article_id), "slug": r.slug, "title": r.title}


def _passage_dict(p: Passage) -> dict[str, Any]:
    return {
        "chunk_id": str(p.chunk_id),
        "article_id": str(p.article_id),
        "slug": p.slug,
        "heading_path": p.heading_path,
        "text": p.text,
        "score": p.score,
    }


def _ref(d: dict[str, Any]) -> Ref:
    return Ref(article_id=uuid.UUID(d["article_id"]), slug=d["slug"], title=d["title"])


def _passage(d: dict[str, Any]) -> Passage:
    return Passage(
        chunk_id=uuid.UUID(d["chunk_id"]),
        article_id=uuid.UUID(d["article_id"]),
        slug=d["slug"],
        heading_path=d["heading_path"],
        text=d["text"],
        score=d["score"],
    )


class QueryCacheRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def _entry(self, row: Any) -> CacheEntry:
        return CacheEntry(
            id=uuid.UUID(str(row.id)),
            answer_md=row.answer_md,
            refs=[_ref(d) for d in _as_list(row.refs)],
            passages=[_passage(d) for d in _as_list(row.passages)],
            stale=bool(row.stale),
        )

    async def upsert(
        self,
        *,
        domain_id: uuid.UUID,
        query_norm: str,
        query_vector: list[float],
        answer_md: str,
        refs: list[Ref],
        passages: list[Passage],
        model: str,
        prompt_version: str,
        article_deps: list[uuid.UUID],
    ) -> uuid.UUID:
        res = await self._s.execute(
            text(
                "INSERT INTO query_cache "
                "(domain_id, query_norm, query_embedding, answer_md, refs, passages, "
                " model, prompt_version, stale, hit_count) "
                "VALUES (:d, :qn, CAST(:v AS vector), :a, CAST(:r AS jsonb), CAST(:p AS jsonb), "
                " :m, :pv, false, 0) "
                "ON CONFLICT (domain_id, query_norm) DO UPDATE SET "
                " query_embedding = EXCLUDED.query_embedding, answer_md = EXCLUDED.answer_md, "
                " refs = EXCLUDED.refs, passages = EXCLUDED.passages, model = EXCLUDED.model, "
                " prompt_version = EXCLUDED.prompt_version, stale = false "
                "RETURNING id"
            ),
            {
                "d": str(domain_id),
                "qn": query_norm,
                "v": _vec_literal(query_vector),
                "a": answer_md,
                "r": json.dumps([_ref_dict(r) for r in refs]),
                "p": json.dumps([_passage_dict(p) for p in passages]),
                "m": model,
                "pv": prompt_version,
            },
        )
        cid = uuid.UUID(str(res.scalar_one()))
        # Replace dependency rows; capture each cited article's current_rev.
        await self._s.execute(
            text("DELETE FROM query_cache_articles WHERE cache_id = :c"), {"c": str(cid)}
        )
        for aid in article_deps:
            await self._s.execute(
                text(
                    "INSERT INTO query_cache_articles (cache_id, article_id, rev) "
                    "SELECT :c, a.id, a.current_rev FROM articles a WHERE a.id = :a "
                    "ON CONFLICT (cache_id, article_id) DO NOTHING"
                ),
                {"c": str(cid), "a": str(aid)},
            )
        await self._s.flush()
        return cid

    async def get_exact(self, domain_id: uuid.UUID, query_norm: str) -> CacheEntry | None:
        res = await self._s.execute(
            text(
                "SELECT id, answer_md, refs, passages, stale FROM query_cache "
                "WHERE domain_id = :d AND query_norm = :qn"
            ),
            {"d": str(domain_id), "qn": query_norm},
        )
        row = res.first()
        return self._entry(row) if row is not None else None

    async def get_ann(self, domain_id: uuid.UUID, query_vector: list[float]) -> AnnHit | None:
        lit = _vec_literal(query_vector)
        res = await self._s.execute(
            text(
                "SELECT id, answer_md, refs, passages, stale, "
                " 1 - (query_embedding <=> CAST(:v AS vector)) AS sim "
                "FROM query_cache "
                "WHERE domain_id = :d AND query_embedding IS NOT NULL "
                "ORDER BY query_embedding <=> CAST(:v AS vector) LIMIT 1"
            ),
            {"d": str(domain_id), "v": lit},
        )
        row = res.first()
        if row is None:
            return None
        return AnnHit(entry=self._entry(row), sim=float(row.sim))

    async def touch_hit(self, cache_id: uuid.UUID) -> None:
        await self._s.execute(
            text(
                "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = now() "
                "WHERE id = :i"
            ),
            {"i": str(cache_id)},
        )
        await self._s.flush()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_repo.py -q`
Expected: PASS (4 passed).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/repos/query_cache.py tests/integration/test_query_cache_repo.py
git commit -m "feat(cache): QueryCacheRepo upsert + exact/ANN lookup + touch (Phase 7)"
```

---

### Task 6: Stale-marking — repo method + article-aware seam + call sites

**Files:**
- Modify: `src/paw/db/repos/query_cache.py`
- Modify: `src/paw/services/cache_seam.py`
- Modify: `src/paw/harness/ops/fix.py`
- Modify: `src/paw/harness/ops/format.py`
- Modify: `src/paw/harness/ops/ingest.py`
- Delete: `tests/unit/test_cache_seam.py` (the Phase 6 no-op test)
- Test: `tests/integration/test_cache_seam.py`

**Interfaces:**
- Consumes: `QueryCacheRepo` (Task 5), `query_cache_articles`.
- Produces:
  - `QueryCacheRepo.mark_stale_for_articles(article_ids: list[uuid.UUID]) -> int` — sets `stale=true` on every entry depending on any given article; returns count.
  - `mark_articles_cache_stale(session, article_ids: list[uuid.UUID]) -> int` in `services/cache_seam.py` (replaces `mark_domain_cache_stale`).
  - All three write ops call `await mark_articles_cache_stale(session, [art.id])` after the article upsert: `fix.py`, `format.py`, and `ingest.py` (the spec names **ingest/fix/format**; re-ingest updates an existing cited article via `upsert_article`, bumping `current_rev`, so it must invalidate too).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_cache_seam.py`:

```python
import uuid

from sqlalchemy import text

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.harness.retrieve import Ref
from paw.services.cache_seam import mark_articles_cache_stale


async def _entry_depending_on(db_session, *, article_id, domain_id):
    repo = QueryCacheRepo(db_session)
    return await repo.upsert(
        domain_id=domain_id, query_norm=f"q-{uuid.uuid4()}", query_vector=[0.1] * 8,
        answer_md="A", refs=[Ref(article_id=article_id, slug="s", title="T")],
        passages=[], model="m", prompt_version="v1", article_deps=[article_id],
    )


async def test_mark_stale_only_dependent_entries(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    a1 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a1", title="A1", storage_ref="b:1", summary="s"
    )
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="b:2", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    c1 = await _entry_depending_on(db_session, article_id=a1.id, domain_id=dom.id)
    c2 = await _entry_depending_on(db_session, article_id=a2.id, domain_id=dom.id)
    await db_session.commit()

    n = await mark_articles_cache_stale(db_session, [a1.id])
    await db_session.commit()
    assert n == 1

    rows = dict(
        (uuid.UUID(str(i)), bool(s))
        for i, s in (
            await db_session.execute(text("SELECT id, stale FROM query_cache"))
        ).all()
    )
    assert rows[c1] is True  # depended on a1 -> stale
    assert rows[c2] is False  # untouched


async def test_mark_stale_empty_is_noop(db_session):
    assert await mark_articles_cache_stale(db_session, []) == 0


async def test_reingest_marks_cited_entry_stale(db_session):
    # Acceptance criterion 3: an INGEST write of a cited article marks dependents stale.
    from paw.harness.ops.ingest import run_ingest
    from paw.providers.config import WikiConfig
    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    dom = await DomainRepo(db_session).create(name="ni", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="quic", title="QUIC", storage_ref="b:q", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    cid = await _entry_depending_on(db_session, article_id=art.id, domain_id=dom.id)
    await db_session.commit()

    extraction = StubChatProvider.tool("emit_result", {"entities": ["QUIC"], "key_points": ["fast"]})
    draft = StubChatProvider.tool(
        "emit_result",
        {
            "slug": "quic", "title": "QUIC", "summary": "QUIC transport.",
            "markdown": "## Overview\n\nQUIC runs over UDP. It is fast.",
            "entities": ["QUIC"], "citations": [{"quote": "QUIC runs over UDP", "locator": "p1"}],
        },
    )
    await run_ingest(
        db_session, domain_id=dom.id, source_md="QUIC runs over UDP.",
        chat=StubChatProvider([extraction, draft]), embedder=StubEmbeddingProvider(dim=8),
        cfg=WikiConfig(chunk_target_size=60), dim=8,
    )
    await db_session.commit()

    stale = (
        await db_session.execute(text("SELECT stale FROM query_cache WHERE id=:i"), {"i": str(cid)})
    ).scalar_one()
    assert stale is True  # re-ingest of the cited article invalidated the cached answer
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_cache_seam.py -q`
Expected: FAIL with `ImportError: cannot import name 'mark_articles_cache_stale'` (and, once the import resolves, `test_reingest_marks_cited_entry_stale` fails because `ingest.py` does not yet call the seam).

- [ ] **Step 3: Add the repo method**

In `src/paw/db/repos/query_cache.py`, add to `QueryCacheRepo` (after `touch_hit`):

```python
    async def mark_stale_for_articles(self, article_ids: list[uuid.UUID]) -> int:
        if not article_ids:
            return 0
        res = await self._s.execute(
            text(
                "UPDATE query_cache SET stale = true WHERE id IN "
                "(SELECT cache_id FROM query_cache_articles WHERE article_id = ANY(:aids))"
            ),
            {"aids": [str(a) for a in article_ids]},
        )
        await self._s.flush()
        return res.rowcount or 0
```

- [ ] **Step 4: Rewrite the seam**

Replace the entire body of `src/paw/services/cache_seam.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def mark_articles_cache_stale(
    session: AsyncSession, article_ids: list[uuid.UUID]
) -> int:
    """Invalidate cached query answers whose cited articles just changed.

    Called inside the article-write transaction (fix/format) so invalidation is
    atomic with the write — no separate eventual path. Marks every ``query_cache``
    entry depending on any of ``article_ids`` (via ``query_cache_articles``).
    Returns the number of entries marked stale.
    """
    from paw.db.repos.query_cache import QueryCacheRepo

    return await QueryCacheRepo(session).mark_stale_for_articles(article_ids)
```

- [ ] **Step 5: Update the call sites**

In `src/paw/harness/ops/fix.py`:
- Change the import `from paw.services.cache_seam import mark_domain_cache_stale` → `from paw.services.cache_seam import mark_articles_cache_stale`.
- Change the call (currently `await mark_domain_cache_stale(session, domain_id)`, just before `return True` in `apply_fix`) to:

```python
    await mark_articles_cache_stale(session, [art.id])
```

In `src/paw/harness/ops/format.py`:
- Change the import the same way.
- Change the call (currently `await mark_domain_cache_stale(session, domain_id)` in `run_format_article`) to:

```python
    await mark_articles_cache_stale(session, [art.id])
```

In `src/paw/harness/ops/ingest.py` (no seam call exists yet — add one):
- Add the import after the existing `from paw.services.ingest_write import upsert_article` line:

```python
from paw.services.cache_seam import mark_articles_cache_stale
```

- In `run_ingest`, immediately after the Stage C citations loop (the `for c in draft.citations:` block, before the `# Stage D — links` comment), add:

```python
    # Phase 7: a re-ingested cited article invalidates cached answers depending on it.
    await mark_articles_cache_stale(session, [art.id])
```

- [ ] **Step 6: Delete the obsolete no-op unit test**

```bash
git rm tests/unit/test_cache_seam.py
```

- [ ] **Step 7: Run the new test + the fix/format/ingest op tests (no regression)**

Run: `uv run pytest tests/integration/test_cache_seam.py tests/integration/test_fix_op.py tests/integration/test_format_op.py tests/integration/test_ingest_op.py tests/integration/test_ingest_task.py -q`
Expected: PASS (the fix/format/ingest ops still succeed; the seam now performs a real, harmless UPDATE on an empty/dependent cache, and re-ingest invalidates dependents).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/paw/db/repos/query_cache.py src/paw/services/cache_seam.py src/paw/harness/ops/fix.py src/paw/harness/ops/format.py src/paw/harness/ops/ingest.py tests/integration/test_cache_seam.py tests/unit/test_cache_seam.py
git commit -m "feat(cache): article-aware stale seam marks dependent entries (ingest/fix/format)"
```

---

### Task 7: QueryService.answer_cached (lookup-before-retrieval + upsert-on-miss)

**Files:**
- Modify: `src/paw/services/query.py`
- Test: `tests/integration/test_query_cache_service.py`

**Interfaces:**
- Consumes: `QueryCacheRepo` (Tasks 5–6); `normalize_query`, `is_similar_enough`, `extract_dependencies` (Task 4); `QueryCacheConfig`, `get_query_cache` (Task 1); `embed_query_cached`; `embedding_dim`; `PROMPT_VERSION`.
- Produces:
  - `@dataclass CachedAnswer(answer_md: str, refs: list[Ref], passages: list[Passage], stale: bool, cached: bool)`.
  - `QueryService.answer_cached(*, domain_id, question, refresh: bool = False) -> CachedAnswer`.
  - Internal `_resolve(domain_id) -> _Resolved` (shared by `prepare`); `prepare`/`complete`/`answer` keep their current signatures and behaviour (SSE path unaffected).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_service.py`:

```python
import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.vector.embed import embed_and_write
from tests.stubs import StubChatProvider

_FERNET = "k" * 43 + "="


class _FixedEmbedder:
    """Deterministic embedder: maps chosen texts to chosen vectors for ANN control."""

    def __init__(self, mapping: dict[str, list[float]], default: list[float]) -> None:
        self._mapping = mapping
        self._default = default

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        return [self._mapping.get(t, self._default) for t in texts]


async def _provision(db_session, monkeypatch, *, embedder, answer="reliable means [tcp]"):
    box = SecretBox(_FERNET)
    psvc = ProviderSettingsService(db_session, box=box)
    await psvc.persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="secret"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=embedder,
    )
    await db_session.commit()
    stub = StubChatProvider(responder=lambda msgs, tools: StubChatProvider.text(answer))
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: stub)
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: embedder)
    return dom, stub


async def test_miss_then_exact_hit_skips_llm(db_session, monkeypatch):
    emb = _FixedEmbedder({}, default=[0.2] * 8)
    dom, stub = await _provision(db_session, monkeypatch, embedder=emb)
    svc = QueryService(db_session, fernet_key=_FERNET).with_redis(None)

    first = await svc.answer_cached(domain_id=dom.id, question="What is reliable?")
    assert first.cached is False
    assert first.answer_md == "reliable means [tcp]"
    calls_after_first = len(stub.calls)
    assert calls_after_first == 1

    # Same question (normalizes identically) -> exact hit, no further LLM call.
    second = await svc.answer_cached(domain_id=dom.id, question="  what is   RELIABLE? ")
    assert second.cached is True
    assert second.answer_md == "reliable means [tcp]"
    assert len(stub.calls) == calls_after_first  # LLM not called again


async def test_ann_hit_within_threshold(db_session, monkeypatch):
    # Two distinct question strings share an embedding -> cosine sim 1.0 >= 0.85.
    same = [0.5] * 8
    emb = _FixedEmbedder({"what is reliable?": same, "tell me about reliability": same}, default=[0.0] * 8)
    dom, stub = await _provision(db_session, monkeypatch, embedder=emb)
    svc = QueryService(db_session, fernet_key=_FERNET).with_redis(None)

    await svc.answer_cached(domain_id=dom.id, question="what is reliable?")
    n = len(stub.calls)
    hit = await svc.answer_cached(domain_id=dom.id, question="tell me about reliability")
    assert hit.cached is True
    assert len(stub.calls) == n  # served via ANN, no LLM


async def test_below_threshold_misses_and_recomputes(db_session, monkeypatch):
    a = [1.0] + [0.0] * 7
    b = [0.0, 1.0] + [0.0] * 6  # orthogonal -> cosine sim 0 < 0.85
    emb = _FixedEmbedder({"alpha question": a, "beta question": b}, default=[0.0] * 8)
    dom, stub = await _provision(db_session, monkeypatch, embedder=emb)
    svc = QueryService(db_session, fernet_key=_FERNET).with_redis(None)

    await svc.answer_cached(domain_id=dom.id, question="alpha question")
    n = len(stub.calls)
    miss = await svc.answer_cached(domain_id=dom.id, question="beta question")
    assert miss.cached is False
    assert len(stub.calls) == n + 1  # recomputed


async def test_disabled_bypasses_cache(db_session, monkeypatch):
    from paw.db.repos.settings import SettingsRepo
    from paw.providers.config import QUERY_CACHE_KEY

    emb = _FixedEmbedder({}, default=[0.2] * 8)
    dom, stub = await _provision(db_session, monkeypatch, embedder=emb)
    await SettingsRepo(db_session).upsert({QUERY_CACHE_KEY: {"enabled": False}})
    await db_session.commit()
    svc = QueryService(db_session, fernet_key=_FERNET).with_redis(None)

    await svc.answer_cached(domain_id=dom.id, question="q")
    await svc.answer_cached(domain_id=dom.id, question="q")
    assert len(stub.calls) == 2  # no caching -> LLM called each time
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_service.py -q`
Expected: FAIL with `AttributeError: 'QueryService' object has no attribute 'answer_cached'`.

- [ ] **Step 3: Refactor QueryService + add answer_cached**

Replace the contents of `src/paw/services/query.py` with:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.managed import embedding_dim
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.harness.ops.query import DONT_KNOW, QueryAnswer, build_messages, dont_know, to_answer
from paw.harness.prompts import PROMPT_VERSION
from paw.harness.retrieve import Passage, Ref, RetrievedContext, retrieve
from paw.providers.base import ChatProvider, EmbeddingProvider, Message
from paw.providers.config import ProviderConfig, QueryCacheConfig, RetrievalConfig, WikiConfig
from paw.providers.factory import build_chat_provider, build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query_cache import extract_dependencies, is_similar_enough, normalize_query
from paw.vector.embed_cache import embed_query_cached


@dataclass
class Prepared:
    chat: ChatProvider
    messages: list[Message] | None  # None -> empty context (don't-know)
    ctx: RetrievedContext


@dataclass
class _Resolved:
    pc: ProviderConfig
    chat: ChatProvider
    embedder: EmbeddingProvider
    wiki: WikiConfig
    retr: RetrievalConfig
    qc: QueryCacheConfig
    embedding_version: int


@dataclass
class CachedAnswer:
    answer_md: str
    refs: list[Ref]
    passages: list[Passage]
    stale: bool
    cached: bool


class QueryService:
    def __init__(self, session: AsyncSession, *, fernet_key: str | None = None) -> None:
        self._s = session
        self._box = SecretBox(fernet_key or get_settings().fernet_key)
        self._redis: object | None = None

    def with_redis(self, redis: object | None) -> QueryService:
        self._redis = redis
        return self

    async def _resolve(self, domain_id: uuid.UUID) -> _Resolved:
        psvc = ProviderSettingsService(self._s, box=self._box)
        pc = await psvc.get_provider()
        if pc is None:
            raise ProblemError(
                status=422,
                title="Provider not configured",
                detail="Configure an LLM provider before querying.",
            )
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")

        wiki = await psvc.get_wiki()
        global_retr = await psvc.get_retrieval()
        global_qc = await psvc.get_query_cache()
        dom_cfg = dom.config if isinstance(dom.config, dict) else {}
        retr_ov = dom_cfg.get("retrieval")
        retr = (
            RetrievalConfig.model_validate({**global_retr.model_dump(), **retr_ov})
            if isinstance(retr_ov, dict)
            else global_retr
        )
        qc_ov = dom_cfg.get("query_cache")
        qc = (
            QueryCacheConfig.model_validate({**global_qc.model_dump(), **qc_ov})
            if isinstance(qc_ov, dict)
            else global_qc
        )
        return _Resolved(
            pc=pc,
            chat=build_chat_provider(pc, self._box),
            embedder=build_embedding_provider(pc, self._box),
            wiki=wiki,
            retr=retr,
            qc=qc,
            embedding_version=await psvc.get_embedding_version(),
        )

    async def _retrieve_ctx(self, res: _Resolved, *, domain_id: uuid.UUID, question: str) -> RetrievedContext:
        return await retrieve(
            self._s,
            domain_id=domain_id,
            query=question,
            embedder=res.embedder,
            cfg=res.retr,
            embedding_version=res.embedding_version,
            redis=self._redis,
            embed_model=res.pc.embedding_model,
        )

    async def prepare(self, *, domain_id: uuid.UUID, question: str) -> Prepared:
        res = await self._resolve(domain_id)
        ctx = await self._retrieve_ctx(res, domain_id=domain_id, question=question)
        messages = build_messages(question, ctx, res.wiki) if ctx.passages else None
        return Prepared(chat=res.chat, messages=messages, ctx=ctx)

    async def complete(self, prepared: Prepared) -> QueryAnswer:
        if prepared.messages is None:
            return dont_know()
        result = await prepared.chat.chat(prepared.messages)
        return to_answer(result.content or DONT_KNOW, prepared.ctx)

    async def answer(self, *, domain_id: uuid.UUID, question: str) -> QueryAnswer:
        prepared = await self.prepare(domain_id=domain_id, question=question)
        return await self.complete(prepared)

    async def _embed(self, res: _Resolved, question: str) -> list[float]:
        return await embed_query_cached(
            self._redis,
            res.embedder,
            query=question,
            model=res.pc.embedding_model,
            embedding_version=res.embedding_version,
        )

    async def _recompute(self, res: _Resolved, *, domain_id: uuid.UUID, question: str) -> QueryAnswer:
        ctx = await self._retrieve_ctx(res, domain_id=domain_id, question=question)
        if not ctx.passages:
            return dont_know()
        result = await res.chat.chat(build_messages(question, ctx, res.wiki))
        return to_answer(result.content or DONT_KNOW, ctx)

    async def answer_cached(
        self, *, domain_id: uuid.UUID, question: str, refresh: bool = False
    ) -> CachedAnswer:
        res = await self._resolve(domain_id)  # raises 404/422
        if not res.qc.enabled:
            ans = await self._recompute(res, domain_id=domain_id, question=question)
            return CachedAnswer(ans.answer_md, ans.refs, ans.passages, stale=False, cached=False)

        repo = QueryCacheRepo(self._s)
        norm = normalize_query(question)
        has_embedding = await embedding_dim(self._s) is not None
        qvec: list[float] | None = None

        if not refresh:
            hit = await repo.get_exact(domain_id, norm)
            if hit is None and has_embedding:
                qvec = await self._embed(res, question)
                ann = await repo.get_ann(domain_id, qvec)
                if ann is not None and is_similar_enough(ann.sim, res.qc.sim_threshold):
                    hit = ann.entry
            if hit is not None:
                await repo.touch_hit(hit.id)
                await self._s.commit()
                return CachedAnswer(
                    hit.answer_md, hit.refs, hit.passages, stale=hit.stale, cached=True
                )

        # Miss or forced refresh: full Phase 3 path, then upsert (clears stale).
        ans = await self._recompute(res, domain_id=domain_id, question=question)
        if has_embedding:
            if qvec is None:
                qvec = await self._embed(res, question)
            await repo.upsert(
                domain_id=domain_id,
                query_norm=norm,
                query_vector=qvec,
                answer_md=ans.answer_md,
                refs=ans.refs,
                passages=ans.passages,
                model=res.pc.chat_model,
                prompt_version=PROMPT_VERSION,
                article_deps=extract_dependencies(ans.refs),
            )
        await self._s.commit()
        return CachedAnswer(ans.answer_md, ans.refs, ans.passages, stale=False, cached=False)
```

(Note: `retrieve` and the retrieve dataclasses are now imported from `paw.harness.retrieve`; the previous `from paw.harness.retrieve import RetrievedContext, retrieve` is folded into the single import line above.)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_service.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the existing query-service tests (no regression)**

Run: `uv run pytest tests/integration/test_query_service.py -q`
Expected: PASS (3 passed — `prepare`/`complete`/`answer` behaviour unchanged).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/paw/services/query.py tests/integration/test_query_cache_service.py
git commit -m "feat(cache): QueryService.answer_cached — cache lookup before retrieval + upsert on miss"
```

---

### Task 8: API — cache-aware query JSON path + ?refresh

**Files:**
- Modify: `src/paw/api/routers/query.py`
- Test: `tests/api/test_query_cache_api.py`

**Interfaces:**
- Consumes: `QueryService.answer_cached`, `CachedAnswer` (Task 7).
- Produces:
  - `POST /api/v1/domains/{id}/query` (JSON, non-SSE) now serves from cache; response gains `stale: bool` and `cached: bool`.
  - `?refresh=1` query param bypasses the cache, recomputes, and clears `stale`.
  - SSE path (when `accept: text/event-stream`) unchanged (no caching).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_query_cache_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write
from tests.stubs import StubChatProvider, StubEmbeddingProvider

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()
    calls = {"n": 0}

    def _responder(msgs, tools):
        calls["n"] += 1
        return StubChatProvider.text("reliable means [tcp]")

    monkeypatch.setattr(
        query_mod, "build_chat_provider", lambda pc, b: StubChatProvider(responder=_responder)
    )
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        c._calls = calls  # type: ignore[attr-defined]
        yield c


async def test_repeat_query_served_from_cache_without_llm(client):
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}
    first = await client.post(url, json={"q": "what is reliable?"}, headers=h)
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert client._calls["n"] == 1

    second = await client.post(url, json={"q": "what is reliable?"}, headers=h)
    assert second.json()["cached"] is True
    assert second.json()["answer_md"] == "reliable means [tcp]"
    assert client._calls["n"] == 1  # no second LLM call


async def test_refresh_recomputes_and_clears_stale(client, db_session):
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}
    await client.post(url, json={"q": "what is reliable?"}, headers=h)
    # Force the entry stale directly.
    await db_session.execute(text("UPDATE query_cache SET stale = true"))
    await db_session.commit()

    stale_hit = await client.post(url, json={"q": "what is reliable?"}, headers=h)
    assert stale_hit.json()["cached"] is True
    assert stale_hit.json()["stale"] is True
    calls_before_refresh = client._calls["n"]

    refreshed = await client.post(f"{url}?refresh=1", json={"q": "what is reliable?"}, headers=h)
    assert refreshed.json()["cached"] is False
    assert refreshed.json()["stale"] is False
    assert client._calls["n"] == calls_before_refresh + 1
    remaining_stale = (
        await db_session.execute(text("SELECT bool_or(stale) FROM query_cache"))
    ).scalar_one()
    assert remaining_stale is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_query_cache_api.py -q`
Expected: FAIL — response JSON has no `cached` key (`KeyError`).

- [ ] **Step 3: Make the JSON path cache-aware**

In `src/paw/api/routers/query.py`:

Add `stale`/`cached` to the response model:

```python
class QueryResult(BaseModel):
    answer_md: str
    refs: list[RefOut]
    passages: list[PassageOut]
    stale: bool = False
    cached: bool = False
```

Add a builder next to `_to_result` (import `CachedAnswer` from `paw.services.query`):

```python
def _cached_to_result(res: CachedAnswer) -> QueryResult:
    return QueryResult(
        answer_md=res.answer_md,
        refs=[RefOut(**r) for r in _refs_json(res.refs)],
        passages=[PassageOut(**p) for p in _passages_json(res.passages)],  # type: ignore[arg-type]
        stale=res.stale,
        cached=res.cached,
    )
```

Update the import line `from paw.services.query import Prepared, QueryService` → `from paw.services.query import CachedAnswer, Prepared, QueryService`.

Replace `query_domain` so the non-SSE branch uses the cache and accepts `refresh`:

```python
@router.post(
    "/domains/{domain_id}/query",
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor", "viewer"))],
)
async def query_domain(
    domain_id: uuid.UUID,
    body: QueryRequest,
    request: Request,
    refresh: int = 0,
    session: AsyncSession = Depends(db),
) -> object:
    svc = QueryService(session).with_redis(get_redis())
    if "text/event-stream" in request.headers.get("accept", ""):
        prepared = await svc.prepare(domain_id=domain_id, question=body.q)  # raises 404/422
        return StreamingResponse(_sse(prepared), media_type="text/event-stream")
    res = await svc.answer_cached(
        domain_id=domain_id, question=body.q, refresh=bool(refresh)
    )  # raises 404/422
    return _cached_to_result(res)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/api/test_query_cache_api.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing query API tests (no regression)**

Run: `uv run pytest tests/api/test_query_api.py -q`
Expected: PASS — but note `test_query_response_shape_valid` asserts `set(body) == {"answer_md", "refs", "passages"}`. The response now also has `stale`/`cached`, so this assertion must be updated. In `tests/api/test_query_api.py`, change that assertion to:

```python
    assert set(body) == {"answer_md", "refs", "passages", "stale", "cached"}
```

Re-run: `uv run pytest tests/api/test_query_api.py tests/api/test_query_cache_api.py -q`
Expected: PASS.
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/paw/api/routers/query.py tests/api/test_query_cache_api.py tests/api/test_query_api.py
git commit -m "feat(api): cache-aware query JSON path + ?refresh; stale/cached fields"
```

---

### Task 9: Suggestions — repo.suggest + API + web endpoint + partial

**Files:**
- Modify: `src/paw/db/repos/query_cache.py`
- Modify: `src/paw/api/routers/query.py`
- Modify: `src/paw/api/web/routes.py`
- Create: `src/paw/api/web/templates/_suggestions.html`
- Test: `tests/api/test_suggest_api.py`, `tests/api/test_suggest_web.py`

**Interfaces:**
- Consumes: `query_cache` rows + `get_query_cache().suggest_top_k`.
- Produces:
  - `QueryCacheRepo.suggest(domain_id, q: str, limit: int) -> list[tuple[str, int]]` — `query_norm` ILIKE-contains `q`, ranked by `hit_count` desc then `query_norm`.
  - `GET /api/v1/domains/{id}/suggest?q=` → `list[{query: str, hit_count: int}]`.
  - `GET /domains/{id}/suggest?q=` (web) → `_suggestions.html` `<li>` list.

- [ ] **Step 1: Write the failing API test**

Create `tests/api/test_suggest_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.managed import ensure_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password

_FERNET = "k" * 43 + "="


async def _seed(db_session, dom_id, norm, hits):
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom_id, query_norm=norm, query_vector=[0.1] * 8, answer_md="A",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    for _ in range(hits):
        await repo.touch_hit(cid)


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    await _seed(db_session, dom.id, "popular tcp question", hits=5)
    await _seed(db_session, dom.id, "rare tcp question", hits=1)
    await _seed(db_session, dom.id, "unrelated udp question", hits=9)
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        yield c


async def test_suggest_ranks_matching_by_hit_count(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest?q=tcp")
    assert r.status_code == 200
    queries = [i["query"] for i in r.json()]
    assert queries == ["popular tcp question", "rare tcp question"]  # 'udp' excluded by match


async def test_suggest_empty_q_returns_empty(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest?q=")
    assert r.json() == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_suggest_api.py -q`
Expected: FAIL with 404 (route not defined) → `KeyError`/assertion.

- [ ] **Step 3: Add the repo.suggest method**

In `src/paw/db/repos/query_cache.py`, add to `QueryCacheRepo`:

```python
    async def suggest(self, domain_id: uuid.UUID, q: str, limit: int) -> list[tuple[str, int]]:
        needle = q.lower().strip()
        if not needle:
            return []
        res = await self._s.execute(
            text(
                "SELECT query_norm, hit_count FROM query_cache "
                "WHERE domain_id = :d AND query_norm ILIKE :pat "
                "ORDER BY hit_count DESC, query_norm ASC LIMIT :k"
            ),
            {"d": str(domain_id), "pat": f"%{needle}%", "k": limit},
        )
        return [(r[0], int(r[1])) for r in res.all()]
```

- [ ] **Step 4: Add the API suggest endpoint**

In `src/paw/api/routers/query.py`, add imports near the top:

```python
from paw.api.deps import db, get_redis, require_csrf, require_role
from paw.db.repos.query_cache import QueryCacheRepo
from paw.services.provider_settings import ProviderSettingsService
```

(`db`, `get_redis`, `require_csrf`, `require_role` are already imported together — extend that line rather than duplicating.)

Add the model + endpoint at the end of the module:

```python
class SuggestItem(BaseModel):
    query: str
    hit_count: int


@router.get(
    "/domains/{domain_id}/suggest",
    dependencies=[Depends(require_role("admin", "editor", "viewer"))],
)
async def suggest_domain(
    domain_id: uuid.UUID,
    q: str = "",
    session: AsyncSession = Depends(db),
) -> list[SuggestItem]:
    if not q.strip():
        return []
    cfg = await ProviderSettingsService(session).get_query_cache()
    rows = await QueryCacheRepo(session).suggest(domain_id, q, cfg.suggest_top_k)
    return [SuggestItem(query=norm, hit_count=hc) for norm, hc in rows]
```

- [ ] **Step 5: Run the API test to verify it passes**

Run: `uv run pytest tests/api/test_suggest_api.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Write the failing web test**

Create `tests/api/test_suggest_web.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.managed import ensure_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    await QueryCacheRepo(db_session).upsert(
        domain_id=dom.id, query_norm="what is tcp", query_vector=[0.1] * 8, answer_md="A",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        yield c


async def test_web_suggest_renders_list(client):
    r = await client.get(f"/domains/{client._dom.id}/suggest?q=tcp")
    assert r.status_code == 200
    assert "what is tcp" in r.text


async def test_web_suggest_empty_when_no_match(client):
    r = await client.get(f"/domains/{client._dom.id}/suggest?q=zzz")
    assert "what is tcp" not in r.text
```

- [ ] **Step 7: Run it to verify it fails**

Run: `uv run pytest tests/api/test_suggest_web.py -q`
Expected: FAIL with 404 (web route not defined).

- [ ] **Step 8: Add the web suggest route + partial**

Create `src/paw/api/web/templates/_suggestions.html`:

```html
{% if suggestions %}
<ul class="suggest" role="listbox">
  {% for s in suggestions %}
  <li><button type="button" class="suggest-item"
      onclick="document.getElementById('query-input').value = this.textContent.trim()">{{ s }}</button></li>
  {% endfor %}
</ul>
{% endif %}
```

In `src/paw/api/web/routes.py`, extend the `paw.api.deps` import to include `get_redis`, and add the imports:

```python
from paw.db.repos.query_cache import QueryCacheRepo
from paw.services.provider_settings import ProviderSettingsService
```

Add the route near `query_page`:

```python
@router.get("/domains/{domain_id}/suggest", response_class=HTMLResponse)
async def web_suggest(
    domain_id: uuid.UUID,
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return HTMLResponse("")
    suggestions: list[str] = []
    if q.strip():
        cfg = await ProviderSettingsService(session).get_query_cache()
        rows = await QueryCacheRepo(session).suggest(domain_id, q, cfg.suggest_top_k)
        suggestions = [norm for norm, _ in rows]
    return templates.TemplateResponse(
        request, "_suggestions.html", {"suggestions": suggestions}
    )
```

- [ ] **Step 9: Run the web test to verify it passes**

Run: `uv run pytest tests/api/test_suggest_web.py -q`
Expected: PASS (2 passed).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/paw/db/repos/query_cache.py src/paw/api/routers/query.py src/paw/api/web/routes.py src/paw/api/web/templates/_suggestions.html tests/api/test_suggest_api.py tests/api/test_suggest_web.py
git commit -m "feat(cache): suggestions — repo.suggest + /suggest API + web dropdown endpoint"
```

---

### Task 10: Web query — cache-aware result with stale badge, Refresh, suggestions dropdown

**Files:**
- Modify: `src/paw/api/web/routes.py`
- Modify: `src/paw/api/web/templates/query.html`
- Modify: `src/paw/api/web/templates/_query_result.html`
- Test: `tests/api/test_query_cache_web.py`

**Interfaces:**
- Consumes: `QueryService.answer_cached`; `get_redis`; `_suggestions.html` (Task 9).
- Produces:
  - `web_query` serves from cache and passes `stale`, `q`, `domain_id`, `csrf` to the result partial; accepts a `refresh` form field.
  - `_query_result.html` renders a "may be outdated" badge + Refresh form when `stale`.
  - `query.html` wires the as-you-type suggestions dropdown (`hx-get .../suggest`, 300ms delay).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_query_cache_web.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write
from tests.stubs import StubChatProvider, StubEmbeddingProvider

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(responder=lambda m, t: StubChatProvider.text("reliable [tcp]")),
    )
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_web_query_stale_badge_and_refresh(client, db_session):
    url = f"/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}
    fresh = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "may be outdated" not in fresh.text

    await db_session.execute(text("UPDATE query_cache SET stale = true"))
    await db_session.commit()

    stale = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "may be outdated" in stale.text
    assert "Refresh" in stale.text
    assert 'name="refresh"' in stale.text


async def test_query_page_has_suggest_wiring(client):
    r = await client.get(f"/domains/{client._dom.id}/query")
    assert "/suggest" in r.text
    assert "query-input" in r.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_query_cache_web.py -q`
Expected: FAIL — `"may be outdated"` absent (result partial has no badge), and `/suggest` absent from the query page.

- [ ] **Step 3: Make web_query cache-aware**

In `src/paw/api/web/routes.py`, replace the `web_query` handler with:

```python
@router.post("/domains/{domain_id}/query", response_class=HTMLResponse)
async def web_query(
    domain_id: uuid.UUID,
    request: Request,
    q: str = Form(...),
    refresh: str = Form(""),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor", "viewer")),
) -> Response:
    res = await QueryService(session).with_redis(get_redis()).answer_cached(
        domain_id=domain_id, question=q, refresh=bool(refresh)
    )
    return templates.TemplateResponse(
        request,
        "_query_result.html",
        {
            "answer_html": render_markdown(res.answer_md),
            "refs": res.refs,
            "passages": res.passages,
            "stale": res.stale,
            "q": q,
            "domain_id": domain_id,
            "csrf": request.cookies.get(CSRF_COOKIE, ""),
        },
    )
```

(`get_redis` is now imported in this module from Task 9. `CSRF_COOKIE` and `render_markdown` are already imported.)

- [ ] **Step 4: Add the stale badge + Refresh to the result partial**

Replace `src/paw/api/web/templates/_query_result.html` with:

```html
<article class="answer">{{ answer_html | safe }}</article>
{% if stale %}
<div class="stale-badge" role="status">
  ⚠ This answer may be outdated.
  <form hx-post="/domains/{{ domain_id }}/query"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'
        hx-target="#query-result" hx-swap="innerHTML" style="display:inline">
    <input type="hidden" name="q" value="{{ q }}">
    <input type="hidden" name="refresh" value="1">
    <button type="submit">Refresh</button>
  </form>
</div>
{% endif %}
{% if refs %}
<div class="chips">
  {% for r in refs %}<a class="chip" href="/articles/{{ r.article_id }}">{{ r.slug }}</a>{% endfor %}
</div>
{% endif %}
{% if passages %}
<details class="passages"><summary>{{ passages | length }} passages</summary>
  <ul>{% for p in passages %}<li>{{ p.slug }}{% if p.heading_path %} › {{ p.heading_path }}{% endif %}</li>{% endfor %}</ul>
</details>
{% endif %}
```

- [ ] **Step 5: Wire the suggestions dropdown on the query page**

Replace `src/paw/api/web/templates/query.html` with:

```html
{% extends "base.html" %}
{% block title %}Query · {{ domain.name }}{% endblock %}
{% block sidebar %}<h3>{{ domain.name }}</h3>{% endblock %}
{% block content %}
<h1>🔍 Query · {{ domain.name }}</h1>
<form hx-post="/domains/{{ domain.id }}/query"
      hx-headers='{"x-csrf-token": "{{ csrf }}"}'
      hx-target="#query-result" hx-swap="innerHTML">
  <input id="query-input" type="text" name="q" placeholder="Ask a question…" autocomplete="off" required
         hx-get="/domains/{{ domain.id }}/suggest"
         hx-trigger="keyup changed delay:300ms"
         hx-target="#suggest-box" hx-swap="innerHTML">
  <div id="suggest-box"></div>
  <button type="submit">Ask</button>
</form>
<section id="query-result" class="query-result"></section>
{% endblock %}
```

- [ ] **Step 6: Run the web test to verify it passes**

Run: `uv run pytest tests/api/test_query_cache_web.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Run the existing query web tests (no regression)**

Run: `uv run pytest tests/api/test_query_web.py -q`
Expected: PASS (the result partial still renders answer/refs/passages; the new `{% if stale %}` block is skipped when `stale` is falsy/absent).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/query.html src/paw/api/web/templates/_query_result.html tests/api/test_query_cache_web.py
git commit -m "feat(web): cache-aware query — stale badge + Refresh + as-you-type suggestions"
```

---

### Task 11: GC — TTL cleanup of expired cache entries

**Files:**
- Modify: `src/paw/db/repos/query_cache.py`
- Modify: `src/paw/jobs/tasks.py`
- Test: `tests/integration/test_query_cache_gc.py`

**Interfaces:**
- Consumes: `QueryCacheRepo`; `get_query_cache().ttl`; existing `gc_housekeeping`.
- Produces:
  - `QueryCacheRepo.delete_expired(*, ttl_seconds: int) -> int` — deletes entries whose `COALESCE(last_hit_at, created_at)` is older than `ttl_seconds`.
  - `gc_housekeeping` also runs cache TTL cleanup (return string `"gc:{pruned}"` is unchanged).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_gc.py`:

```python
from sqlalchemy import text

from paw.db.managed import ensure_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.jobs.tasks import gc_housekeeping


async def test_gc_deletes_expired_keeps_fresh(db_session, wired_settings):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    repo = QueryCacheRepo(db_session)
    old = await repo.upsert(
        domain_id=dom.id, query_norm="old", query_vector=[0.1] * 8, answer_md="A",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    fresh = await repo.upsert(
        domain_id=dom.id, query_norm="fresh", query_vector=[0.2] * 8, answer_md="B",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    # Age the 'old' entry well past the 14-day default TTL.
    await db_session.execute(
        text("UPDATE query_cache SET last_hit_at = now() - interval '30 days', "
             "created_at = now() - interval '30 days' WHERE id = :i"),
        {"i": str(old)},
    )
    await db_session.commit()

    await gc_housekeeping({})

    remaining = set(
        str(i) for i in (await db_session.execute(text("SELECT id FROM query_cache"))).scalars()
    )
    assert str(old) not in remaining
    assert str(fresh) in remaining


async def test_delete_expired_returns_count(db_session, wired_settings):
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="q", query_vector=[0.3] * 8, answer_md="A",
        refs=[], passages=[], model="m", prompt_version="v1", article_deps=[],
    )
    await db_session.execute(
        text("UPDATE query_cache SET created_at = now() - interval '40 days', last_hit_at = NULL "
             "WHERE id = :i"),
        {"i": str(cid)},
    )
    await db_session.commit()
    n = await repo.delete_expired(ttl_seconds=1_209_600)
    assert n == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_gc.py -q`
Expected: FAIL — `gc_housekeeping` does not delete cache rows (the `old` entry remains) and `delete_expired` does not exist.

- [ ] **Step 3: Add delete_expired to the repo**

In `src/paw/db/repos/query_cache.py`, add to `QueryCacheRepo`:

```python
    async def delete_expired(self, *, ttl_seconds: int) -> int:
        res = await self._s.execute(
            text(
                "DELETE FROM query_cache "
                "WHERE COALESCE(last_hit_at, created_at) < now() - make_interval(secs => :ttl)"
            ),
            {"ttl": ttl_seconds},
        )
        await self._s.flush()
        return res.rowcount or 0
```

- [ ] **Step 4: Extend gc_housekeeping**

In `src/paw/jobs/tasks.py`, inside `gc_housekeeping`, add the cache cleanup just before `await session.commit()` (after the user-pruning loop). Add `QueryCacheRepo` to the local imports at the top of the function:

```python
    from paw.db.repos.query_cache import QueryCacheRepo
```

Then, before `await session.commit()`:

```python
        qc_cfg = await ProviderSettingsService(session, box=box).get_query_cache()
        await QueryCacheRepo(session).delete_expired(ttl_seconds=qc_cfg.ttl)
```

(`ProviderSettingsService` and `box` are already in scope in `gc_housekeeping`.)

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_gc.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the existing GC tests (no regression)**

Run: `uv run pytest tests/integration/test_gc_housekeeping.py -q`
Expected: PASS (return value `"gc:{pruned}"` unchanged; cache cleanup is additive and those tests create no cache rows).
Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/paw/db/repos/query_cache.py src/paw/jobs/tasks.py tests/integration/test_query_cache_gc.py
git commit -m "feat(cache): gc_housekeeping prunes TTL-expired query_cache entries"
```

---

### Task 12: E2E — query → cached → edit cited article → stale → refresh → fresh

**Files:**
- Test: `tests/e2e/test_query_cache_e2e.py`

**Interfaces:**
- Consumes: `QueryService.answer_cached`; `run_format_article` (calls the seam); `QueryCacheRepo`; stubs.
- Produces: end-to-end coverage of acceptance criteria 1, 3, 4 (cache hit without LLM; edit marks stale; refresh recomputes + clears stale).

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_query_cache_e2e.py`:

```python
from sqlalchemy import text

import paw.services.query as query_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.format import run_format_article
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.storage.postgres import PostgresStorage
from paw.vector.embed import embed_and_write
from tests.stubs import StubChatProvider, StubEmbeddingProvider

_FERNET = "k" * 43 + "="


async def test_query_cache_full_round_trip(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="secret"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    storage = PostgresStorage(db_session)
    ref = await storage.put(b"# TCP\n\nTCP provides reliable delivery.", content_type="text/markdown")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref=ref, summary="TCP summary"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()

    chat = StubChatProvider(responder=lambda m, t: StubChatProvider.text("reliable means [tcp]"))
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: chat)
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    svc = QueryService(db_session, fernet_key=_FERNET).with_redis(None)

    # 1) miss -> caches
    first = await svc.answer_cached(domain_id=dom.id, question="what is reliable?")
    assert first.cached is False
    n_after_first = len(chat.calls)

    # 2) repeat -> served from cache, no LLM (acceptance 1)
    second = await svc.answer_cached(domain_id=dom.id, question="what is reliable?")
    assert second.cached is True and second.stale is False
    assert len(chat.calls) == n_after_first

    # 3) edit the cited article via Format -> seam marks the entry stale (acceptance 3)
    fmt_chat = StubChatProvider(
        responder=lambda m, t: StubChatProvider.text("# TCP\n\nTCP provides reliable delivery overall.")
    )
    ok = await run_format_article(
        db_session, domain_id=dom.id, article=art,
        entity_names=[], citation_quotes=[], chat=fmt_chat, cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is True
    stale_flag = (await db_session.execute(text("SELECT bool_or(stale) FROM query_cache"))).scalar_one()
    assert stale_flag is True

    # 4) next read returns the cached answer flagged stale
    third = await svc.answer_cached(domain_id=dom.id, question="what is reliable?")
    assert third.cached is True and third.stale is True

    # 5) Refresh recomputes + clears stale (acceptance 4)
    refreshed = await svc.answer_cached(domain_id=dom.id, question="what is reliable?", refresh=True)
    assert refreshed.cached is False and refreshed.stale is False
    after_clear = (await db_session.execute(text("SELECT bool_or(stale) FROM query_cache"))).scalar_one()
    assert after_clear is False
```

- [ ] **Step 2: Run it to verify it fails (then passes)**

Run: `uv run pytest tests/e2e/test_query_cache_e2e.py -q`
Expected: With Tasks 1–11 implemented, this should PASS directly. If it FAILS, the failure pinpoints an integration gap (e.g. the seam not firing on Format, or refresh not clearing stale) — fix the responsible task's code, not the test.

If `PostgresStorage.put` has a different signature in this codebase, adjust the two storage lines to match `tests/e2e/test_maintenance_e2e.py`'s article-creation pattern (it plants an article + storage_ref the same way); keep the rest of the test identical.

- [ ] **Step 3: Run the full suite + lint + types**

Run: `uv run pytest -q`
Expected: PASS (entire suite green, including the pre-existing layers).
Run: `uv run ruff check . && uv run mypy src`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_query_cache_e2e.py
git commit -m "test(e2e): query -> cached -> edit cited article -> stale -> refresh -> fresh"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):

- DB `query_cache` + `query_cache_articles` (cols, unique, indexes, managed `vector(dim)`) → Tasks 2, 3.
- Lookup: exact-norm fast-path, then semantic ANN ≥ `sim_threshold`; fresh hit returns `answer_md`+`refs` without LLM; miss → full path → upsert with deps → Tasks 5, 7.
- Eager stale-marking via the Phase 2/6 seam, transactional, per `article_id`, on **all three** write ops (ingest/fix/format) → Task 6.
- Stale handling: flag + Refresh (`?refresh=1` bypasses, recomputes, clears stale) → Tasks 7, 8, 10.
- Suggestions `GET /suggest?q=` ranked by `hit_count` → Task 9 (API) + Task 10 (web dropdown).
- GC TTL cleanup in `gc_housekeeping` → Task 11.
- Web UI: suggestions dropdown (300ms) + stale badge + Refresh → Task 10.
- Config `query_cache` block (`enabled`, `sim_threshold`, `ttl`, `suggest_top_k`), global + per-domain → Task 1 (+ per-domain override resolved in Task 7 `_resolve`).
- Security: per-domain scoping on every query → Tasks 5, 7, 9; sanitized render → Task 10 (`render_markdown`).
- Risk: dim change reindexes/clears cache → Task 3 (`rebuild` truncates query_cache). Stale-marking transactional → Task 6 (in the writers' commit boundary).
- Acceptance criteria 1–6 → Tasks 7/8 (1, 2), 6 (3 — ingest/fix/format, with a re-ingest test) + 12 (3 — format leg), 8/12 (4), 9 (5), 11 (6).
- Out-of-scope respected: chat never cached (untouched); scheduled GC cron not added (manual `gc_housekeeping` only); reranking not added.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every code/test step shows complete content. The only conditional instruction is Task 12 Step 2's fallback for `PostgresStorage.put`, which names the exact reference file to mirror.

**3. Type consistency:** `QueryCacheRepo` methods (`upsert`, `get_exact`, `get_ann`, `touch_hit`, `mark_stale_for_articles`, `suggest`, `delete_expired`) are defined in Tasks 5/6/9/11 and consumed with matching signatures in Tasks 6/7/8/9/11. `CachedAnswer(answer_md, refs, passages, stale, cached)` defined in Task 7 and consumed identically in Tasks 8/10/12. `mark_articles_cache_stale(session, article_ids)` defined in Task 6 and called with `[art.id]` in fix/format. `QueryCacheConfig` fields (`enabled`, `sim_threshold`, `ttl`, `suggest_top_k`) consistent across Tasks 1/7/9/11. `Ref`/`Passage` (de)serialization round-trips through the repo's JSONB helpers.

**Note for the implementer:** Tasks are ordered by dependency — implement in sequence. Each task ends green (its own tests + ruff + mypy) before the next begins.
