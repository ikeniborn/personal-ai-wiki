---
review:
  plan_hash: ef04f506c634931a
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
      phase: structure
      severity: CRITICAL
      section: "Task 8 / Task 9 — test URLs"
      section_hash: a659b535f71b4123
      text: "Reviewer flagged `client._REDACTED` in test URLs as un-runnable. VERIFIED FALSE POSITIVE: the file contains `client._dom.id` (grep count 9; literal `_REDACTED` count 0). The mismatch is a transcript redaction-layer artifact in the reviewer's Read output, not file content. No edit (editing would introduce the bug)."
      verdict: wontfix
      verdict_at: 2026-06-24
    - id: F-002
      phase: consistency
      severity: WARNING
      section: "Task 8 — router model provenance"
      section_hash: a659b535f71b4123
      text: "Non-SSE upsert reads `model = getattr(prepared.chat, 'chat_model', '')`; the stub provider lacks `chat_model`, so the stored `model` column is empty in tests. Acceptance criteria do not assert on `model`; acceptable for v1."
      verdict: wontfix
      verdict_at: 2026-06-24
    - id: F-003
      phase: dependencies
      severity: WARNING
      section: "Tasks 8/9 — API/web fixtures"
      section_hash: a659b535f71b4123
      text: "API/web routers build services without an explicit fernet_key, relying on `get_settings().fernet_key`. Satisfied by the `wired_settings` fixture present in every API/web test fixture; not a defect, flagged as an ordering/setup invariant the executor must keep."
      verdict: wontfix
      verdict_at: 2026-06-24
    - id: F-004
      phase: coverage
      severity: INFO
      section: "Task 4 — suggest"
      section_hash: 761ccf6be8322d0d
      text: "`suggest` uses prefix ILIKE, not the spec's 'FTS/ANN'. Deliberate v1 simplification (noted in Task 4 + Self-Review); acceptance #5 only requires hit_count-ranked matches, which this satisfies."
      verdict: wontfix
      verdict_at: 2026-06-24
    - id: F-005
      phase: coverage
      severity: INFO
      section: "Task 8 — refresh"
      section_hash: a659b535f71b4123
      text: "Background refresh not implemented (only synchronous ?refresh=1). Spec marks it 'optionally', so not a gap."
      verdict: wontfix
      verdict_at: 2026-06-24
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-7-query-cache-design.md
---

# Phase 7 — Query-cache + Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut LLM + retrieval load by serving repeated/semantically-near queries from a per-domain answer cache; eagerly mark cache entries stale on article writes; serve stale hits with a flag + Refresh; surface team-popular queries as as-you-type suggestions.

**Architecture:** A new `query_cache` table (one row per `(domain, normalized-query)`) plus a `query_cache_articles` dependency table. The query path does an **exact-norm fast-path** then a **semantic ANN** lookup *before* retrieval; a fresh/stale hit short-circuits the LLM. Misses run the existing Phase 3 path then upsert the answer + its article dependencies. Article writes (ingest/fix/format) mark dependent cache rows stale **in the same transaction** via the existing `cache_seam`. The query embedding is a runtime-**managed** `vector(dim)` column (exactly like `chunks.embedding`), so a provider dim-change clears + rebuilds it alongside the chunk reindex. GC gains a TTL sweep. The web Query screen gains a suggestions dropdown and a stale badge + Refresh button.

**Tech Stack:** Python 3.12 · async SQLAlchemy 2.0 (raw `text()` for the vector column) · PostgreSQL 16 + `pgvector` (HNSW, `vector_cosine_ops`) · FastAPI · Jinja2 + HTMX · `arq` worker · pytest + testcontainers.

---

## Background: conventions this plan follows

Read these before starting — they are load-bearing:

- **Managed vector column.** `chunks.embedding vector(dim)` is **not** in any alembic migration; `db/managed.py::ensure_embedding_column` adds it (+ HNSW) at provider-config time, and `embedding_dim(session)` reads `pg_attribute.atttypmod`. The vector arm in `vector/search.py::hybrid_search` *skips* itself when `embedding_dim(session) is None`. We mirror **all** of this for `query_cache.query_embedding`.
- **Single commit boundary.** Repos and storage **never** commit. The service layer issues exactly one `session.commit()` per operation. `db.repos.*` only `flush()`.
- **Per-domain config merge.** `QueryService.prepare` resolves config as `global ⊕ domains.config["<key>"]`:
  ```python
  retr = (RetrievalConfig.model_validate({**global_retr.model_dump(), **domain_overrides})
          if isinstance(domain_overrides, dict) else global_retr)
  ```
  We mirror this for `query_cache`.
- **Vector literal.** `vector/search.py::_vector_literal(vec) -> str` turns `list[float]` into pgvector text `"[0.1,0.2,...]"` and raises on non-finite values. Reuse it; do not re-implement.
- **JSONB over raw SQL.** asyncpg returns a JSONB column selected via `text()` as a **str**. Always `SELECT col::text` and `json.loads(...)` on read; write with `CAST(:p AS jsonb)` binding `json.dumps(...)`.
- **Cosine.** With `vector_cosine_ops`, `a <=> b` is cosine **distance** = `1 - cosine_similarity`. So `similarity = 1 - distance`, and "cosine ≥ threshold" ⇔ `(1 - distance) >= sim_threshold`.
- **Stub embedder.** `tests/stubs.py::StubEmbeddingProvider` hashes text → deterministic but uncontrollable vectors (good for exact-hit tests, useless for ANN-near tests). ANN tests in this plan use a small in-test `FixedEmbeddingProvider` mapping specific strings → specific vectors.

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `alembic/versions/0005_phase7_query_cache.py` | Creates `query_cache` (no vector col) + `query_cache_articles`, indexes. |
| `src/paw/db/repos/query_cache.py` | `QueryCacheRepo` — raw-SQL persistence: exact/ANN lookup, upsert, deps, touch, mark-stale, suggest, TTL delete. Never commits. |
| `src/paw/services/query_cache.py` | `QueryCacheService` (owns commit) + pure helpers `normalize_query`, `passes_threshold`, `dep_article_ids`, ref/passage serializers, `CacheHit`. |
| `src/paw/api/web/templates/_suggestions.html` | HTMX dropdown fragment (one mini-form per suggestion). |
| `tests/unit/test_query_cache_config.py` | `QueryCacheConfig` defaults + merge. |
| `tests/unit/test_query_cache_helpers.py` | `normalize_query`, `passes_threshold`, `dep_article_ids`. |
| `tests/unit/test_query_cache_models.py` | ORM tables registered; `query_embedding` NOT ORM-mapped. |
| `tests/integration/test_query_cache_repo.py` | Repo CRUD: exact, ANN, upsert+deps, touch, mark-stale, suggest, TTL. |
| `tests/integration/test_query_cache_service.py` | Service lookup/upsert/refresh + LLM-call-count hit vs miss. |
| `tests/integration/test_cache_stale_seam.py` | Transactional mark-stale on ingest/fix/format. |
| `tests/integration/test_query_cache_gc.py` | `gc_housekeeping` deletes expired cache rows. |
| `tests/integration/test_query_cache_dim_change.py` | provider dim-change clears + rebuilds the query_cache embedding. |
| `tests/api/test_query_cache_api.py` | hit/miss/`?refresh=1` over HTTP, call-count via stub. |
| `tests/api/test_suggest_api.py` | `GET /suggest?q=` JSON ranked by hit_count. |
| `tests/api/test_query_cache_web.py` | web stale badge + Refresh + suggestions dropdown. |
| `tests/e2e/test_query_cache_e2e.py` | query → cached → edit cited article → stale → refresh → fresh. |

### Modified files

| Path | Change |
|------|--------|
| `src/paw/providers/config.py` | Add `QUERY_CACHE_KEY` + `QueryCacheConfig`. |
| `src/paw/services/provider_settings.py` | Add `get_query_cache()`. |
| `src/paw/db/models.py` | Add `QueryCache` + `QueryCacheArticle` ORM models (no vector col). |
| `src/paw/db/managed.py` | Add `ensure_query_cache_embedding_column`, `rebuild_query_cache_embedding_column`, `query_cache_embedding_dim`. |
| `src/paw/services/cache_seam.py` | Implement article-level `mark_cache_stale(session, *, domain_id, article_ids)`. |
| `src/paw/harness/ops/ingest.py` | Call the seam after the write. |
| `src/paw/harness/ops/fix.py` | Switch to `mark_cache_stale(..., article_ids=[art.id])`. |
| `src/paw/harness/ops/format.py` | Switch to `mark_cache_stale(..., article_ids=[art.id])`. |
| `src/paw/jobs/tasks.py` | `gc_housekeeping`: TTL sweep of `query_cache`. |
| `src/paw/api/routers/query.py` | Cache lookup/upsert in `query_domain`, `?refresh=1`, `stale`/`cached` fields, `GET /suggest`. |
| `src/paw/api/web/routes.py` | `web_query` through cache; new `GET /domains/{id}/suggest` web route. |
| `src/paw/api/web/templates/query.html` | Suggestions dropdown wiring. |
| `src/paw/api/web/templates/_query_result.html` | Stale badge + Refresh button. |
| `src/paw/services/provider_settings.py::update_provider` | On dim-change, also rebuild the query_cache embedding column. |
| `tests/conftest.py` | `_clean_db`: truncate new tables + drop managed `query_embedding`. |
| `tests/unit/test_cache_seam.py` | Update to the new seam signature (empty-list no-op). |
| `tests/api/test_query_api.py` | `test_query_response_shape_valid` now expects `stale`/`cached`. |

---

## Task 1: `QueryCacheConfig`

**Files:**
- Modify: `src/paw/providers/config.py`
- Test: `tests/unit/test_query_cache_config.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_query_cache_config.py`:

```python
from paw.providers.config import QUERY_CACHE_KEY, QueryCacheConfig


def test_defaults():
    c = QueryCacheConfig()
    assert c.enabled is True
    assert c.sim_threshold == 0.92
    assert c.ttl_seconds == 30 * 24 * 3600
    assert c.suggest_top_k == 5


def test_key_constant():
    assert QUERY_CACHE_KEY == "query_cache"


def test_domain_override_merge():
    base = QueryCacheConfig()
    merged = QueryCacheConfig.model_validate(
        {**base.model_dump(), "enabled": False, "sim_threshold": 0.8}
    )
    assert merged.enabled is False and merged.sim_threshold == 0.8
    assert merged.ttl_seconds == 30 * 24 * 3600  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_query_cache_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'QUERY_CACHE_KEY'`.

- [ ] **Step 3: Add the config model**

In `src/paw/providers/config.py`, add the key constant next to the others (after `EMBEDDING_KEY = "embedding"`):

```python
QUERY_CACHE_KEY = "query_cache"
```

And add the model after `EmbeddingConfig`:

```python
class QueryCacheConfig(BaseModel):
    enabled: bool = True
    sim_threshold: float = 0.92  # cosine similarity floor for an ANN hit
    ttl_seconds: int = 30 * 24 * 3600  # GC deletes entries idle longer than this
    suggest_top_k: int = 5  # max as-you-type suggestions returned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_query_cache_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the service getter (no test yet — exercised in Task 5)**

In `src/paw/services/provider_settings.py`, add `QUERY_CACHE_KEY`/`QueryCacheConfig` to the import block from `paw.providers.config`, then add this method after `get_maintenance`:

```python
    async def get_query_cache(self) -> QueryCacheConfig:
        raw = (await self._all()).get(QUERY_CACHE_KEY)
        return QueryCacheConfig.model_validate(raw) if raw else QueryCacheConfig()
```

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check src tests/unit/test_query_cache_config.py && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/paw/providers/config.py src/paw/services/provider_settings.py tests/unit/test_query_cache_config.py
git commit -m "feat(config): QueryCacheConfig + per-domain getter (phase 7)"
```

---

## Task 2: Migration, ORM models, managed vector column, test isolation

**Files:**
- Create: `alembic/versions/0005_phase7_query_cache.py`
- Modify: `src/paw/db/models.py`, `src/paw/db/managed.py`, `tests/conftest.py`
- Test: `tests/unit/test_query_cache_models.py` (create), `tests/integration/test_migration.py` (reuse — see Step 8)

- [ ] **Step 1: Write the failing model test**

Create `tests/unit/test_query_cache_models.py`:

```python
from paw.db.base import Base
from paw.db.models import QueryCache, QueryCacheArticle  # noqa: F401


def test_query_cache_tables_registered():
    tables = set(Base.metadata.tables)
    assert {"query_cache", "query_cache_articles"} <= tables


def test_query_embedding_not_orm_mapped():
    # query_embedding is a runtime-managed vector(dim) column, like chunks.embedding.
    cols = set(QueryCache.__table__.columns.keys())
    assert "query_embedding" not in cols
    assert {"domain_id", "query_norm", "answer_md", "refs", "passages", "stale",
            "hit_count", "last_hit_at"} <= cols


def test_query_cache_articles_shape():
    cols = set(QueryCacheArticle.__table__.columns.keys())
    assert {"cache_id", "article_id", "rev"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_query_cache_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'QueryCache'`.

- [ ] **Step 3: Add the ORM models**

In `src/paw/db/models.py`, append after `ChatMessage` (before the `_biginteger`/`_string` lines):

```python
class QueryCache(Base):
    __tablename__ = "query_cache"
    __table_args__ = (UniqueConstraint("domain_id", "query_norm"),)
    # NOTE: `query_embedding vector(dim)` is a runtime-managed column
    # (see db/managed.py + QueryCacheRepo raw SQL); intentionally NOT ORM-mapped.
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
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
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
    rev: Mapped[int] = mapped_column(Integer, nullable=False)
```

(`UniqueConstraint`, `Boolean`, `Integer`, `Text`, `JSONB`, `func`, `ForeignKey`, `DateTime`, `Mapped`, `mapped_column`, `Any`, `datetime`, `uuid` are all already imported in this module.)

- [ ] **Step 4: Run the model test**

Run: `uv run pytest tests/unit/test_query_cache_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the migration**

Create `alembic/versions/0005_phase7_query_cache.py`:

```python
from alembic import op

revision = "0005_phase7_query_cache"
down_revision = "0004_phase5_backlink_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # query_cache: NO query_embedding column here (managed migration adds vector(dim) + HNSW).
    op.execute("""
    CREATE TABLE query_cache (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      query_norm text NOT NULL,
      answer_md text NOT NULL,
      refs jsonb NOT NULL DEFAULT '[]',
      passages jsonb NOT NULL DEFAULT '[]',
      model text,
      prompt_version text,
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
      rev int NOT NULL,
      PRIMARY KEY (cache_id, article_id))
    """)
    op.execute(
        "CREATE INDEX ix_query_cache_articles_article_id "
        "ON query_cache_articles(article_id)"
    )


def downgrade() -> None:
    for t in ("query_cache_articles", "query_cache"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
```

- [ ] **Step 6: Add the managed vector helpers**

In `src/paw/db/managed.py`, after the existing `_HNSW_INDEX = "ix_chunks_embedding_hnsw"` add:

```python
_QC_HNSW_INDEX = "ix_query_cache_embedding_hnsw"
```

Then append these three functions at the end of the module:

```python
async def ensure_query_cache_embedding_column(session: AsyncSession, dim: int) -> None:
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    await session.execute(
        text(f"ALTER TABLE query_cache ADD COLUMN IF NOT EXISTS query_embedding vector({dim})")
    )
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def rebuild_query_cache_embedding_column(session: AsyncSession, dim: int) -> None:
    """Change query_cache.query_embedding dim. DESTRUCTIVE: truncates the cache
    (a dim change invalidates every stored answer embedding — they must be
    recomputed on the next miss). Mirrors rebuild_embedding_column for chunks."""
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    await session.execute(text("TRUNCATE query_cache CASCADE"))
    await session.execute(text(f"DROP INDEX IF EXISTS {_QC_HNSW_INDEX}"))
    await session.execute(text("ALTER TABLE query_cache DROP COLUMN IF EXISTS query_embedding"))
    await session.execute(text(f"ALTER TABLE query_cache ADD COLUMN query_embedding vector({dim})"))
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def query_cache_embedding_dim(session: AsyncSession) -> int | None:
    row = await session.execute(
        text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = 'query_cache' AND a.attname = 'query_embedding' "
            "AND NOT a.attisdropped"
        )
    )
    val = row.scalar_one_or_none()
    return int(val) if val is not None and val > 0 else None
```

- [ ] **Step 7: Update test isolation in conftest**

In `tests/conftest.py`, the `_clean_db` fixture: extend the `TRUNCATE` list and drop the managed query-cache column. Replace the existing `TRUNCATE ...` statement and the two DDL-cleanup lines with:

```python
        await conn.execute(
            text(
                "TRUNCATE users, api_keys, app_settings, domains, blobs, "
                "sources, articles, article_revisions, audit_log, "
                "chat_sessions, chat_messages, query_cache, query_cache_articles "
                "RESTART IDENTITY CASCADE"
            )
        )
        # Drop managed vector columns/indexes so each test starts with a clean DDL state.
        await conn.execute(text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
        await conn.execute(text("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_query_cache_embedding_hnsw"))
        await conn.execute(text("ALTER TABLE query_cache DROP COLUMN IF EXISTS query_embedding"))
```

- [ ] **Step 8: Verify the migration applies against the container**

The session-scoped `_migrate` fixture runs `alembic upgrade head`, so `0005` applies automatically. Confirm with the existing migration smoke test:

Run: `uv run pytest tests/integration/test_migration.py -q`
Expected: PASS (the schema upgrades to head including `query_cache`).

- [ ] **Step 9: Lint + typecheck**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add alembic/versions/0005_phase7_query_cache.py src/paw/db/models.py src/paw/db/managed.py tests/conftest.py tests/unit/test_query_cache_models.py
git commit -m "feat(db): query_cache schema + managed query_embedding column (phase 7)"
```

---

## Task 3: Pure helpers — normalize / threshold / dep extraction

**Files:**
- Create: `src/paw/services/query_cache.py` (helpers only in this task)
- Test: `tests/unit/test_query_cache_helpers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_query_cache_helpers.py`:

```python
import uuid

from paw.harness.retrieve import Ref
from paw.services.query_cache import dep_article_ids, normalize_query, passes_threshold


def test_normalize_lowercases_trims_collapses_ws():
    assert normalize_query("  What   IS  TCP?\n") == "what is tcp?"
    assert normalize_query("A\tB") == "a b"


def test_passes_threshold_uses_cosine_distance():
    # similarity = 1 - distance
    assert passes_threshold(0.05, 0.92) is True   # sim 0.95 >= 0.92
    assert passes_threshold(0.10, 0.92) is False  # sim 0.90 <  0.92
    assert passes_threshold(0.08, 0.92) is True   # sim 0.92 == 0.92 (boundary)


def test_dep_article_ids_dedups_preserving_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    refs = [
        Ref(article_id=a, slug="x", title="X"),
        Ref(article_id=b, slug="y", title="Y"),
        Ref(article_id=a, slug="x", title="X"),
    ]
    assert dep_article_ids(refs) == [a, b]


def test_dep_article_ids_empty():
    assert dep_article_ids([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_query_cache_helpers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.services.query_cache'`.

- [ ] **Step 3: Create the module with the pure helpers**

Create `src/paw/services/query_cache.py`:

```python
from __future__ import annotations

import re
import uuid

from paw.harness.retrieve import Passage, Ref

_WS = re.compile(r"\s+")

PROMPT_VERSION = "1"  # bump if the query system prompt changes materially


def normalize_query(q: str) -> str:
    """Lower, trim, collapse internal whitespace — the exact-match key."""
    return _WS.sub(" ", q.strip().lower())


def passes_threshold(distance: float, sim_threshold: float) -> bool:
    """pgvector <=> is cosine distance (1 - similarity); compare similarity."""
    return (1.0 - distance) >= sim_threshold


def dep_article_ids(refs: list[Ref]) -> list[uuid.UUID]:
    """Dependency article ids from the answer's refs, deduped, order-preserving."""
    return list(dict.fromkeys(r.article_id for r in refs))


def ref_to_json(r: Ref) -> dict[str, str]:
    return {"article_id": str(r.article_id), "slug": r.slug, "title": r.title}


def passage_to_json(p: Passage) -> dict[str, object]:
    return {
        "chunk_id": str(p.chunk_id),
        "article_id": str(p.article_id),
        "slug": p.slug,
        "heading_path": p.heading_path,
        "text": p.text,
        "score": p.score,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_query_cache_helpers.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src tests/unit/test_query_cache_helpers.py && uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/paw/services/query_cache.py tests/unit/test_query_cache_helpers.py
git commit -m "feat(cache): pure query-cache helpers (normalize, threshold, deps)"
```

---

## Task 4: `QueryCacheRepo`

**Files:**
- Create: `src/paw/db/repos/query_cache.py`
- Test: `tests/integration/test_query_cache_repo.py` (create)

**Note on `CacheRow`:** a frozen dataclass the repo returns from lookups; deliberately omits the embedding (we never read it back).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_repo.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo


async def _domain(db_session, name="d"):
    return await DomainRepo(db_session).create(name=name, source_prefix="s", wiki_prefix="w")


async def test_exact_upsert_and_get_by_norm(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="what is tcp?", answer_md="TCP [tcp]",
        refs=[{"article_id": "a", "slug": "tcp", "title": "TCP"}],
        passages=[{"chunk_id": "c", "slug": "tcp"}],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    row = await repo.get_by_norm(domain_id=dom.id, query_norm="what is tcp?")
    assert row is not None and row.id == cid
    assert row.answer_md == "TCP [tcp]" and row.stale is False
    assert row.refs[0]["slug"] == "tcp" and row.passages[0]["chunk_id"] == "c"
    # re-upsert preserves hit_count and clears stale
    await repo.set_stale(domain_id=dom.id, ids=[cid])  # helper from mark path (see Step 3)
    await repo.upsert(
        domain_id=dom.id, query_norm="what is tcp?", answer_md="TCP v2 [tcp]",
        refs=[], passages=[], model="m", prompt_version="1",
        query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    row2 = await repo.get_by_norm(domain_id=dom.id, query_norm="what is tcp?")
    assert row2.answer_md == "TCP v2 [tcp]" and row2.stale is False


async def test_ann_nearest_returns_distance(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    await repo.upsert(
        domain_id=dom.id, query_norm="q1", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    near = await repo.ann_nearest(domain_id=dom.id, query_vector=[0.96, 0.28, 0.0, 0.0])
    assert near is not None
    row, dist = near
    assert row.query_norm == "q1"
    assert 0.0 <= dist < 0.1  # cosine distance to a near-parallel vector is small


async def test_touch_increments_hit_count(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="q", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    await repo.touch(cache_id=cid)
    await repo.touch(cache_id=cid)
    await db_session.commit()
    row = await repo.get_by_norm(domain_id=dom.id, query_norm="q")
    assert row.hit_count == 2 and row.last_hit_at is not None


async def test_set_deps_and_mark_stale_for_articles(db_session):
    dom = await _domain(db_session)
    arts = ArticleRepo(db_session)
    a1 = await arts.create(domain_id=dom.id, slug="a1", title="A1", storage_ref="b:1")
    a2 = await arts.create(domain_id=dom.id, slug="a2", title="A2", storage_ref="b:2")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    c1 = await repo.upsert(
        domain_id=dom.id, query_norm="dep1", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    c2 = await repo.upsert(
        domain_id=dom.id, query_norm="dep2", answer_md="B", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[0.0, 1.0, 0.0, 0.0],
    )
    await repo.set_deps(cache_id=c1, deps=[(a1.id, 1)])
    await repo.set_deps(cache_id=c2, deps=[(a2.id, 1)])
    await db_session.commit()
    n = await repo.mark_stale_for_articles(domain_id=dom.id, article_ids=[a1.id])
    await db_session.commit()
    assert n == 1
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep1")).stale is True
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep2")).stale is False


async def test_suggest_ranks_by_hit_count(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    for norm, hits in [("tcp basics", 1), ("tcp handshake", 5), ("udp facts", 9)]:
        cid = await repo.upsert(
            domain_id=dom.id, query_norm=norm, answer_md="A", refs=[], passages=[],
            model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
        )
        for _ in range(hits):
            await repo.touch(cache_id=cid)
    await db_session.commit()
    out = await repo.suggest(domain_id=dom.id, q="tcp", limit=5)
    assert out == ["tcp handshake", "tcp basics"]  # only 'tcp%' matches, hit-count order


async def test_delete_expired(db_session):
    dom = await _domain(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=dom.id, query_norm="old", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    # backdate last_hit_at far into the past
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE query_cache SET last_hit_at = :w WHERE id = :i"),
        {"w": datetime.now(UTC) - timedelta(days=400), "i": str(cid)},
    )
    await db_session.commit()
    n = await repo.delete_expired(cutoff=datetime.now(UTC) - timedelta(days=30))
    await db_session.commit()
    assert n == 1
    assert await repo.get_by_norm(domain_id=dom.id, query_norm="old") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.repos.query_cache'`.

- [ ] **Step 3: Implement the repo**

Create `src/paw/db/repos/query_cache.py`:

```python
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.vector.search import _vector_literal


@dataclass(frozen=True)
class CacheRow:
    id: uuid.UUID
    query_norm: str
    answer_md: str
    refs: list[dict[str, object]]
    passages: list[dict[str, object]]
    stale: bool
    hit_count: int
    last_hit_at: datetime | None


def _row(r: object) -> CacheRow:
    # r is a Row: (id, query_norm, answer_md, refs::text, passages::text, stale, hit_count, last_hit_at)
    return CacheRow(
        id=uuid.UUID(str(r[0])),
        query_norm=r[1],
        answer_md=r[2],
        refs=json.loads(r[3]),
        passages=json.loads(r[4]),
        stale=bool(r[5]),
        hit_count=int(r[6]),
        last_hit_at=r[7],
    )


_SELECT = (
    "id, query_norm, answer_md, refs::text, passages::text, stale, hit_count, last_hit_at"
)


class QueryCacheRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_norm(
        self, *, domain_id: uuid.UUID, query_norm: str
    ) -> CacheRow | None:
        res = await self._s.execute(
            text(
                f"SELECT {_SELECT} FROM query_cache "
                "WHERE domain_id = :d AND query_norm = :n"
            ),
            {"d": str(domain_id), "n": query_norm},
        )
        row = res.first()
        return _row(row) if row else None

    async def ann_nearest(
        self, *, domain_id: uuid.UUID, query_vector: list[float]
    ) -> tuple[CacheRow, float] | None:
        res = await self._s.execute(
            text(
                f"SELECT {_SELECT}, (query_embedding <=> CAST(:q AS vector)) AS dist "
                "FROM query_cache "
                "WHERE domain_id = :d AND query_embedding IS NOT NULL "
                "ORDER BY query_embedding <=> CAST(:q AS vector) LIMIT 1"
            ),
            {"d": str(domain_id), "q": _vector_literal(query_vector)},
        )
        row = res.first()
        if row is None:
            return None
        return _row(row), float(row[8])

    async def upsert(
        self,
        *,
        domain_id: uuid.UUID,
        query_norm: str,
        answer_md: str,
        refs: list[dict[str, object]],
        passages: list[dict[str, object]],
        model: str | None,
        prompt_version: str,
        query_vector: list[float],
    ) -> uuid.UUID:
        res = await self._s.execute(
            text(
                "INSERT INTO query_cache "
                "(domain_id, query_norm, answer_md, refs, passages, model, prompt_version, "
                " stale, hit_count, last_hit_at) "
                "VALUES (:d, :n, :a, CAST(:refs AS jsonb), CAST(:ps AS jsonb), :m, :pv, "
                " false, 0, now()) "
                "ON CONFLICT (domain_id, query_norm) DO UPDATE SET "
                " answer_md = EXCLUDED.answer_md, refs = EXCLUDED.refs, "
                " passages = EXCLUDED.passages, model = EXCLUDED.model, "
                " prompt_version = EXCLUDED.prompt_version, stale = false, "
                " last_hit_at = now() "
                "RETURNING id"
            ),
            {
                "d": str(domain_id),
                "n": query_norm,
                "a": answer_md,
                "refs": json.dumps(refs),
                "ps": json.dumps(passages),
                "m": model,
                "pv": prompt_version,
            },
        )
        cid = uuid.UUID(str(res.scalar_one()))
        await self._s.execute(
            text("UPDATE query_cache SET query_embedding = CAST(:v AS vector) WHERE id = :i"),
            {"v": _vector_literal(query_vector), "i": str(cid)},
        )
        await self._s.flush()
        return cid

    async def set_deps(
        self, *, cache_id: uuid.UUID, deps: list[tuple[uuid.UUID, int]]
    ) -> None:
        await self._s.execute(
            text("DELETE FROM query_cache_articles WHERE cache_id = :c"),
            {"c": str(cache_id)},
        )
        for article_id, rev in deps:
            await self._s.execute(
                text(
                    "INSERT INTO query_cache_articles (cache_id, article_id, rev) "
                    "VALUES (:c, :a, :r)"
                ),
                {"c": str(cache_id), "a": str(article_id), "r": rev},
            )
        await self._s.flush()

    async def touch(self, *, cache_id: uuid.UUID) -> None:
        await self._s.execute(
            text(
                "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = now() "
                "WHERE id = :i"
            ),
            {"i": str(cache_id)},
        )
        await self._s.flush()

    async def set_stale(self, *, domain_id: uuid.UUID, ids: list[uuid.UUID]) -> int:
        if not ids:
            return 0
        res = await self._s.execute(
            text(
                "UPDATE query_cache SET stale = true "
                "WHERE domain_id = :d AND id = ANY(:ids)"
            ),
            {"d": str(domain_id), "ids": [str(i) for i in ids]},
        )
        await self._s.flush()
        return res.rowcount or 0

    async def mark_stale_for_articles(
        self, *, domain_id: uuid.UUID, article_ids: list[uuid.UUID]
    ) -> int:
        if not article_ids:
            return 0
        res = await self._s.execute(
            text(
                "UPDATE query_cache SET stale = true "
                "WHERE domain_id = :d AND id IN ("
                "  SELECT cache_id FROM query_cache_articles WHERE article_id = ANY(:aids))"
            ),
            {"d": str(domain_id), "aids": [str(a) for a in article_ids]},
        )
        await self._s.flush()
        return res.rowcount or 0

    async def suggest(
        self, *, domain_id: uuid.UUID, q: str, limit: int
    ) -> list[str]:
        res = await self._s.execute(
            text(
                "SELECT query_norm FROM query_cache "
                "WHERE domain_id = :d AND query_norm ILIKE :pat "
                "ORDER BY hit_count DESC, query_norm ASC LIMIT :k"
            ),
            {"d": str(domain_id), "pat": f"{q}%", "k": limit},
        )
        return [r[0] for r in res.all()]

    async def delete_expired(self, *, cutoff: datetime) -> int:
        res = await self._s.execute(
            text("DELETE FROM query_cache WHERE COALESCE(last_hit_at, created_at) < :c"),
            {"c": cutoff},
        )
        await self._s.flush()
        return res.rowcount or 0
```

> Note: `suggest` uses a **prefix** match (`q%`) — the as-you-type case. The repo test feeds already-normalized `q`; the service normalizes before calling.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_repo.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src tests/integration/test_query_cache_repo.py && uv run mypy src`
Expected: clean. (If mypy flags `_vector_literal` as private-import, that's fine — it is already module-public in `vector/search.py` and imported the same way elsewhere is not done; if mypy objects, add `# noqa`-free: it won't, the name resolves.)

- [ ] **Step 6: Commit**

```bash
git add src/paw/db/repos/query_cache.py tests/integration/test_query_cache_repo.py
git commit -m "feat(db): QueryCacheRepo (exact/ANN/upsert/deps/stale/suggest/ttl)"
```

---

## Task 5: `QueryCacheService` (lookup / upsert / refresh / suggest) + call-count proof

**Files:**
- Modify: `src/paw/services/query_cache.py` (add the service + `CacheHit`)
- Test: `tests/integration/test_query_cache_service.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_service.py`:

```python
import pytest
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.services.query_cache import QueryCacheService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class FixedEmbed:
    """Deterministic, controllable embedder: maps text -> a fixed unit-ish vector."""

    def __init__(self, table: dict[str, list[float]], default: list[float]) -> None:
        self.table = table
        self.default = default

    async def embed(self, texts, *, model=None):
        return [self.table.get(t, self.default) for t in texts]


async def _provision(db_session, monkeypatch, *, embed):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=embed,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: embed)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: embed)
    return dom, art


async def test_miss_then_exact_hit_skips_llm(db_session, monkeypatch):
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    chat = StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)

    # MISS -> compute + upsert
    assert await csvc.lookup(domain_id=dom.id, question="what is reliable?", cfg=cfg) is None
    answer = await qsvc.answer(domain_id=dom.id, question="what is reliable?")
    await csvc.upsert(
        domain_id=dom.id, question="what is reliable?", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )
    assert len(chat.calls) == 1

    # HIT (exact norm, different casing/space) -> no further LLM call
    hit = await csvc.lookup(domain_id=dom.id, question="  WHAT is   reliable? ", cfg=cfg)
    assert hit is not None and hit.answer_md == "reliable means [tcp]"
    assert hit.stale is False
    assert len(chat.calls) == 1  # acceptance #1: zero LLM calls on the second request


async def test_ann_hit_within_threshold_else_miss(db_session, monkeypatch):
    embed = FixedEmbed(
        {
            "tcp explained": [0.96, 0.28, 0.0, 0.0],   # cos ~0.96 to the stored vector -> HIT
            "banana bread": [0.0, 0.0, 1.0, 0.0],      # cos 0 -> MISS
        },
        default=[1.0, 0.0, 0.0, 0.0],                  # the cached query embeds here
    )
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    chat = StubChatProvider(script=[StubChatProvider.text("answer [tcp]")])
    monkeypatch.setattr(query_mod, "build_chat_provider", lambda pc, b: chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    answer = await qsvc.answer(domain_id=dom.id, question="what is tcp")  # default vec
    await csvc.upsert(
        domain_id=dom.id, question="what is tcp", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )

    near = await csvc.lookup(domain_id=dom.id, question="tcp explained", cfg=cfg)
    assert near is not None and near.answer_md == "answer [tcp]"
    far = await csvc.lookup(domain_id=dom.id, question="banana bread", cfg=cfg)
    assert far is None


async def test_upsert_records_article_deps(db_session, monkeypatch):
    from sqlalchemy import text
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("a [tcp]")]),
    )
    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    answer = await qsvc.answer(domain_id=dom.id, question="q")
    await csvc.upsert(
        domain_id=dom.id, question="q", answer_md=answer.answer_md,
        refs=answer.refs, passages=answer.passages, model="m",
    )
    rows = (await db_session.execute(
        text("SELECT article_id, rev FROM query_cache_articles")
    )).all()
    assert (str(art.id), 1) in {(str(r[0]), r[1]) for r in rows}


async def test_lookup_disabled_when_no_embedding_column(db_session, monkeypatch):
    # exact path still works even before any ANN column exists for the domain
    embed = FixedEmbed({}, default=[1.0, 0.0, 0.0, 0.0])
    dom, art = await _provision(db_session, monkeypatch, embed=embed)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    # nothing cached yet, no query_cache embedding column -> clean miss, no error
    assert await csvc.lookup(domain_id=dom.id, question="anything", cfg=cfg) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'QueryCacheService'`.

- [ ] **Step 3: Implement the service**

Append to `src/paw/services/query_cache.py` (add the imports at the top of the file alongside the existing ones):

```python
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.managed import (
    ensure_query_cache_embedding_column,
    query_cache_embedding_dim,
)
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.providers.config import QueryCacheConfig
from paw.providers.factory import build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed_cache import embed_query_cached
```

Then add the `CacheHit` dataclass and the service class at the end:

```python
@dataclass(frozen=True)
class CacheHit:
    id: uuid.UUID
    answer_md: str
    refs: list[dict[str, object]]
    passages: list[dict[str, object]]
    stale: bool


class QueryCacheService:
    def __init__(self, session: AsyncSession, *, fernet_key: str | None = None) -> None:
        self._s = session
        self._box = SecretBox(fernet_key or get_settings().fernet_key)
        self._redis: object | None = None
        self._repo = QueryCacheRepo(session)

    def with_redis(self, redis: object | None) -> QueryCacheService:
        self._redis = redis
        return self

    async def config(self, domain_id: uuid.UUID) -> QueryCacheConfig:
        psvc = ProviderSettingsService(self._s, box=self._box)
        glob = await psvc.get_query_cache()
        dom = await DomainRepo(self._s).get(domain_id)
        overrides = (
            dom.config.get("query_cache") if dom is not None and isinstance(dom.config, dict)
            else None
        )
        if isinstance(overrides, dict):
            return QueryCacheConfig.model_validate({**glob.model_dump(), **overrides})
        return glob

    async def _embed(self, *, question: str) -> tuple[list[float], int] | None:
        """Return (query_vector, embedding_dim) or None if no provider is configured."""
        psvc = ProviderSettingsService(self._s, box=self._box)
        pc = await psvc.get_provider()
        if pc is None:
            return None
        embedder = build_embedding_provider(pc, self._box)
        vec = await embed_query_cached(
            self._redis, embedder, query=question, model=pc.embedding_model,
            embedding_version=await psvc.get_embedding_version(),
        )
        return vec, pc.embedding_dim

    async def lookup(
        self, *, domain_id: uuid.UUID, question: str, cfg: QueryCacheConfig
    ) -> CacheHit | None:
        norm = normalize_query(question)
        exact = await self._repo.get_by_norm(domain_id=domain_id, query_norm=norm)
        if exact is not None:
            return CacheHit(exact.id, exact.answer_md, exact.refs, exact.passages, exact.stale)
        if await query_cache_embedding_dim(self._s) is None:
            return None  # no ANN column yet -> exact-only
        embedded = await self._embed(question=question)
        if embedded is None:
            return None
        vec, _dim = embedded
        near = await self._repo.ann_nearest(domain_id=domain_id, query_vector=vec)
        if near is None:
            return None
        row, dist = near
        if not passes_threshold(dist, cfg.sim_threshold):
            return None
        return CacheHit(row.id, row.answer_md, row.refs, row.passages, row.stale)

    async def touch(self, cache_id: uuid.UUID) -> None:
        await self._repo.touch(cache_id=cache_id)
        await self._s.commit()

    async def upsert(
        self,
        *,
        domain_id: uuid.UUID,
        question: str,
        answer_md: str,
        refs: list[Ref],
        passages: list[Passage],
        model: str,
    ) -> None:
        embedded = await self._embed(question=question)
        if embedded is None:
            raise ProblemError(status=422, title="Provider not configured")
        vec, dim = embedded
        await ensure_query_cache_embedding_column(self._s, dim)
        cache_id = await self._repo.upsert(
            domain_id=domain_id,
            query_norm=normalize_query(question),
            answer_md=answer_md,
            refs=[ref_to_json(r) for r in refs],
            passages=[passage_to_json(p) for p in passages],
            model=model,
            prompt_version=PROMPT_VERSION,
            query_vector=vec,
        )
        ids = dep_article_ids(refs)
        deps: list[tuple[uuid.UUID, int]] = []
        if ids:
            res = await self._s.execute(
                text("SELECT id, current_rev FROM articles WHERE id = ANY(:ids)"),
                {"ids": [str(i) for i in ids]},
            )
            rev_of = {uuid.UUID(str(r[0])): int(r[1]) for r in res.all()}
            deps = [(aid, rev_of[aid]) for aid in ids if aid in rev_of]
        await self._repo.set_deps(cache_id=cache_id, deps=deps)
        await self._s.commit()

    async def suggest(
        self, *, domain_id: uuid.UUID, q: str, top_k: int
    ) -> list[str]:
        norm = normalize_query(q)
        if not norm:
            return []
        return await self._repo.suggest(domain_id=domain_id, q=norm, limit=top_k)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_service.py -q`
Expected: PASS (4 passed). The first test proves acceptance #1 (zero LLM calls on the second request); the second proves acceptance #2 (ANN within threshold hits, below misses).

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check src tests/integration/test_query_cache_service.py && uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/paw/services/query_cache.py tests/integration/test_query_cache_service.py
git commit -m "feat(cache): QueryCacheService lookup/upsert/suggest (+ call-count proof)"
```

---

## Task 6: Article-level stale seam, wired into ingest/fix/format

**Files:**
- Modify: `src/paw/services/cache_seam.py`, `src/paw/harness/ops/ingest.py`, `src/paw/harness/ops/fix.py`, `src/paw/harness/ops/format.py`, `tests/unit/test_cache_seam.py`
- Test: `tests/integration/test_cache_stale_seam.py` (create)

- [ ] **Step 1: Update the unit test for the new seam signature**

Replace `tests/unit/test_cache_seam.py` entirely with:

```python
import uuid

from paw.services.cache_seam import mark_cache_stale


async def test_empty_article_ids_is_a_noop():
    # No article ids -> early return, never touches the session.
    result = await mark_cache_stale(None, domain_id=uuid.uuid4(), article_ids=[])  # type: ignore[arg-type]
    assert result is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_cache_seam.py -q`
Expected: FAIL — `ImportError: cannot import name 'mark_cache_stale'`.

- [ ] **Step 3: Implement the article-level seam**

Replace `src/paw/services/cache_seam.py` entirely with:

```python
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.query_cache import QueryCacheRepo


async def mark_cache_stale(
    session: AsyncSession, *, domain_id: uuid.UUID, article_ids: list[uuid.UUID]
) -> None:
    """Mark query_cache entries that depend on any of ``article_ids`` as stale.

    Runs in the SAME transaction as the article write (no eventual path).
    Article writers (ingest/fix/format) call this after upserting an article so a
    later read serves the cached answer with a "may be outdated" flag + Refresh.
    """
    if not article_ids:
        return None
    await QueryCacheRepo(session).mark_stale_for_articles(
        domain_id=domain_id, article_ids=article_ids
    )
    return None
```

- [ ] **Step 4: Run the unit test**

Run: `uv run pytest tests/unit/test_cache_seam.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Rewire the three writers**

In `src/paw/harness/ops/fix.py`: change the import `from paw.services.cache_seam import mark_domain_cache_stale` to `from paw.services.cache_seam import mark_cache_stale`, and change line 98 from:

```python
    await mark_domain_cache_stale(session, domain_id)
```
to:
```python
    await mark_cache_stale(session, domain_id=domain_id, article_ids=[art.id])
```

In `src/paw/harness/ops/format.py`: same import change, and change line 74 from:

```python
    await mark_domain_cache_stale(session, domain_id)
```
to:
```python
    await mark_cache_stale(session, domain_id=domain_id, article_ids=[art.id])
```

In `src/paw/harness/ops/ingest.py`: add the import near the other service imports:

```python
from paw.services.cache_seam import mark_cache_stale
```
and add this line just before `return IngestResult(...)` (after the chunk/entity tagging loop):

```python
    await mark_cache_stale(session, domain_id=domain_id, article_ids=[art.id])
```

- [ ] **Step 6: Write the transactional integration test**

Create `tests/integration/test_cache_stale_seam.py`:

```python
from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.services.cache_seam import mark_cache_stale


async def _cache_entry(db_session, *, domain_id, query_norm, dep_article_id):
    repo = QueryCacheRepo(db_session)
    cid = await repo.upsert(
        domain_id=domain_id, query_norm=query_norm, answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await repo.set_deps(cache_id=cid, deps=[(dep_article_id, 1)])
    return cid


async def test_seam_marks_only_dependent_entries(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    arts = ArticleRepo(db_session)
    a1 = await arts.create(domain_id=dom.id, slug="a1", title="A1", storage_ref="b:1")
    a2 = await arts.create(domain_id=dom.id, slug="a2", title="A2", storage_ref="b:2")
    await ensure_query_cache_embedding_column(db_session, 4)
    await _cache_entry(db_session, domain_id=dom.id, query_norm="dep-a1", dep_article_id=a1.id)
    await _cache_entry(db_session, domain_id=dom.id, query_norm="dep-a2", dep_article_id=a2.id)
    await db_session.commit()

    # editing a1 marks only the a1-dependent entry stale, same transaction
    await mark_cache_stale(db_session, domain_id=dom.id, article_ids=[a1.id])
    await db_session.commit()

    repo = QueryCacheRepo(db_session)
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep-a1")).stale is True
    assert (await repo.get_by_norm(domain_id=dom.id, query_norm="dep-a2")).stale is False


async def test_seam_rolls_back_with_the_write(db_session):
    # the stale mark is part of the caller's transaction: a rollback un-marks it
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a", title="A", storage_ref="b:1"
    )
    await ensure_query_cache_embedding_column(db_session, 4)
    cid = await _cache_entry(db_session, domain_id=dom.id, query_norm="q", dep_article_id=art.id)
    await db_session.commit()

    await mark_cache_stale(db_session, domain_id=dom.id, article_ids=[art.id])
    await db_session.rollback()  # caller aborts the write

    row = (await db_session.execute(
        text("SELECT stale FROM query_cache WHERE id = :i"), {"i": str(cid)}
    )).scalar_one()
    assert row is False  # un-marked along with the rolled-back write
```

- [ ] **Step 7: Run the seam tests**

Run: `uv run pytest tests/unit/test_cache_seam.py tests/integration/test_cache_stale_seam.py -q`
Expected: PASS (3 passed). This proves acceptance #3.

- [ ] **Step 8: Run the existing fix/format/ingest op tests to confirm no regression**

Run: `uv run pytest tests/integration/test_fix_op.py tests/integration/test_format_op.py tests/integration/test_ingest_op.py -q`
Expected: PASS (the seam now touches `query_cache`, which exists post-migration; with no cache rows it is a harmless no-op UPDATE).

- [ ] **Step 9: Lint + typecheck**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/paw/services/cache_seam.py src/paw/harness/ops/ingest.py src/paw/harness/ops/fix.py src/paw/harness/ops/format.py tests/unit/test_cache_seam.py tests/integration/test_cache_stale_seam.py
git commit -m "feat(cache): article-level stale seam wired into ingest/fix/format"
```

---

## Task 7: GC TTL cleanup

**Files:**
- Modify: `src/paw/jobs/tasks.py` (`gc_housekeeping`)
- Test: `tests/integration/test_query_cache_gc.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_gc.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.jobs.tasks import gc_housekeeping


async def test_gc_deletes_expired_cache_entries(db_session, wired_settings):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    fresh = await repo.upsert(
        domain_id=dom.id, query_norm="fresh", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    expired = await repo.upsert(
        domain_id=dom.id, query_norm="expired", answer_md="B", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    # default ttl is 30 days; backdate the expired entry past it
    await db_session.execute(
        text("UPDATE query_cache SET last_hit_at = :w WHERE id = :i"),
        {"w": datetime.now(UTC) - timedelta(days=40), "i": str(expired)},
    )
    await db_session.commit()

    await gc_housekeeping({})

    assert await repo.get_by_norm(domain_id=dom.id, query_norm="fresh") is not None
    assert await repo.get_by_norm(domain_id=dom.id, query_norm="expired") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_gc.py -q`
Expected: FAIL — the expired entry is still present (no TTL sweep yet).

- [ ] **Step 3: Extend `gc_housekeeping`**

In `src/paw/jobs/tasks.py`, edit the `gc_housekeeping` function. Keep the chat-session pruning unchanged and the return string `f"gc:{pruned}"` unchanged (existing gc tests assert that exact format). Add the cache sweep inside the same `async with` session block, after the chat-pruning loop and before `await session.commit()`:

```python
        # Phase 7: TTL sweep of the query cache (global ttl).
        from datetime import timedelta

        from paw.db.repos.query_cache import QueryCacheRepo

        qc_cfg = await ProviderSettingsService(session, box=box).get_query_cache()
        cutoff = now - timedelta(seconds=qc_cfg.ttl_seconds)
        await QueryCacheRepo(session).delete_expired(cutoff=cutoff)
```

(`now = datetime.now(UTC)` is already defined earlier in the function; `ProviderSettingsService` and `box` are already in scope.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_query_cache_gc.py -q`
Expected: PASS. This proves acceptance #6.

- [ ] **Step 5: Confirm existing gc tests still pass (return-string unchanged)**

Run: `uv run pytest tests/integration/test_gc_housekeeping.py -q`
Expected: PASS (3 passed) — `out == "gc:N"` still holds.

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check src tests/integration/test_query_cache_gc.py && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/paw/jobs/tasks.py tests/integration/test_query_cache_gc.py
git commit -m "feat(jobs): gc_housekeeping TTL sweep of query_cache"
```

---

## Task 8: API — cache in the query router, `?refresh=1`, `/suggest`

**Files:**
- Modify: `src/paw/api/routers/query.py`
- Test: `tests/api/test_query_cache_api.py` (create), `tests/api/test_suggest_api.py` (create), `tests/api/test_query_api.py` (update shape test)

- [ ] **Step 1: Update the existing response-shape test**

In `tests/api/test_query_api.py`, replace `test_query_response_shape_valid`'s final assertion:

```python
    assert set(body) == {"answer_md", "refs", "passages"}
```
with:
```python
    assert set(body) == {"answer_md", "refs", "passages", "stale", "cached"}
    assert body["stale"] is False and body["cached"] is False
```

- [ ] **Step 2: Write the failing cache API test**

Create `tests/api/test_query_cache_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
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

_FERNET = "k" * 43 + "="


class FixedEmbed:
    def __init__(self, default): self.default = default
    async def embed(self, texts, *, model=None): return [self.default for _ in texts]


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="a@b.c", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    emb = FixedEmbed([1.0, 0.0, 0.0, 0.0])
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: emb)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_second_identical_query_served_from_cache(client, monkeypatch):
    calls = {"n": 0}

    def make_chat(pc, b):
        calls["n"] += 1
        return StubChatProvider(script=[StubChatProvider.text("reliable means [tcp]")])

    monkeypatch.setattr(query_mod, "build_chat_provider", make_chat)
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}

    r1 = await client.post(url, json={"q": "what is reliable?"}, headers=h)
    assert r1.status_code == 200 and r1.json()["cached"] is False
    r2 = await client.post(url, json={"q": "WHAT is   reliable? "}, headers=h)
    body = r2.json()
    assert body["cached"] is True and body["stale"] is False
    assert body["answer_md"] == "reliable means [tcp]"
    assert calls["n"] == 1  # the LLM provider was built/called exactly once


async def test_refresh_bypasses_and_recomputes(client, monkeypatch):
    answers = iter(["first [tcp]", "second [tcp]"])

    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text(next(answers))]),
    )
    url = f"/api/v1/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}

    await client.post(url, json={"q": "q?"}, headers=h)                 # caches "first"
    cached = await client.post(url, json={"q": "q?"}, headers=h)
    assert cached.json()["answer_md"] == "first [tcp]"
    refreshed = await client.post(url + "?refresh=1", json={"q": "q?"}, headers=h)
    assert refreshed.json()["answer_md"] == "second [tcp]"
    assert refreshed.json()["cached"] is False
    again = await client.post(url, json={"q": "q?"}, headers=h)
    assert again.json()["answer_md"] == "second [tcp]"  # refreshed value now cached
```

- [ ] **Step 3: Write the failing suggest API test**

Create `tests/api/test_suggest_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="a@b.c", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    for norm, hits in [("tcp basics", 1), ("tcp handshake", 5), ("udp facts", 9)]:
        cid = await repo.upsert(
            domain_id=dom.id, query_norm=norm, answer_md="A", refs=[], passages=[],
            model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
        )
        for _ in range(hits):
            await repo.touch(cache_id=cid)
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        yield c


async def test_suggest_ranks_by_hit_count(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest", params={"q": "tcp"})
    assert r.status_code == 200
    assert r.json()["suggestions"] == ["tcp handshake", "tcp basics"]


async def test_suggest_empty_query_returns_empty(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest", params={"q": ""})
    assert r.status_code == 200 and r.json()["suggestions"] == []
```

- [ ] **Step 4: Run the new API tests to verify they fail**

Run: `uv run pytest tests/api/test_query_cache_api.py tests/api/test_suggest_api.py -q`
Expected: FAIL — responses lack `cached`/`stale`; `/suggest` 404.

- [ ] **Step 5: Rewrite the query router**

Replace `src/paw/api/routers/query.py` entirely with:

```python
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, get_redis, require_csrf, require_role
from paw.harness.ops.query import DONT_KNOW
from paw.harness.retrieve import Passage, Ref, RetrievedContext
from paw.services.query import Prepared, QueryService
from paw.services.query_cache import CacheHit, QueryCacheService

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    q: str


class RefOut(BaseModel):
    article_id: str
    slug: str
    title: str


class PassageOut(BaseModel):
    chunk_id: str
    article_id: str
    slug: str
    heading_path: str | None
    text: str
    score: float


class QueryResult(BaseModel):
    answer_md: str
    refs: list[RefOut]
    passages: list[PassageOut]
    stale: bool = False
    cached: bool = False


class SuggestResult(BaseModel):
    suggestions: list[str]


def _refs_json(refs: list[Ref]) -> list[dict[str, str]]:
    return [{"article_id": str(r.article_id), "slug": r.slug, "title": r.title} for r in refs]


def _passages_json(ps: list[Passage]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": str(p.chunk_id),
            "article_id": str(p.article_id),
            "slug": p.slug,
            "heading_path": p.heading_path,
            "text": p.text,
            "score": p.score,
        }
        for p in ps
    ]


def _to_result(answer_md: str, ctx: RetrievedContext) -> QueryResult:
    return QueryResult(
        answer_md=answer_md,
        refs=[RefOut(**r) for r in _refs_json(ctx.refs)],
        passages=[PassageOut(**p) for p in _passages_json(ctx.passages)],  # type: ignore[arg-type]
    )


def _cached_result(hit: CacheHit) -> QueryResult:
    return QueryResult(
        answer_md=hit.answer_md,
        refs=[RefOut(**r) for r in hit.refs],  # type: ignore[arg-type]
        passages=[PassageOut(**p) for p in hit.passages],  # type: ignore[arg-type]
        stale=hit.stale,
        cached=True,
    )


def _sse_cached(hit: CacheHit) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'token': hit.answer_md}, ensure_ascii=False)}\n\n"
        done = {
            "status": "done",
            "refs": hit.refs,
            "passages": hit.passages,
            "stale": hit.stale,
            "cached": True,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return gen()


def _sse_compute(
    prepared: Prepared,
    cache: QueryCacheService | None,
    *,
    domain_id: uuid.UUID,
    question: str,
    model: str,
) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        tokens: list[str] = []
        if prepared.messages is None:
            yield f"data: {json.dumps({'token': DONT_KNOW}, ensure_ascii=False)}\n\n"
        else:
            async for tok in prepared.chat.stream(prepared.messages):
                tokens.append(tok)
                yield f"data: {json.dumps({'token': tok}, ensure_ascii=False)}\n\n"
        done = {
            "status": "done",
            "refs": _refs_json(prepared.ctx.refs),
            "passages": _passages_json(prepared.ctx.passages),
            "stale": False,
            "cached": False,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        if cache is not None and prepared.ctx.refs and tokens:
            await cache.upsert(
                domain_id=domain_id, question=question, answer_md="".join(tokens),
                refs=prepared.ctx.refs, passages=prepared.ctx.passages, model=model,
            )

    return gen()


@router.get(
    "/domains/{domain_id}/suggest",
    dependencies=[Depends(require_role("admin", "editor", "viewer"))],
)
async def suggest_domain(
    domain_id: uuid.UUID,
    q: str = "",
    session: AsyncSession = Depends(db),
) -> SuggestResult:
    svc = QueryCacheService(session)
    cfg = await svc.config(domain_id)
    sugg = await svc.suggest(domain_id=domain_id, q=q, top_k=cfg.suggest_top_k)
    return SuggestResult(suggestions=sugg)


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
    qsvc = QueryService(session).with_redis(get_redis())
    csvc = QueryCacheService(session).with_redis(get_redis())
    cfg = await csvc.config(domain_id)
    wants_sse = "text/event-stream" in request.headers.get("accept", "")

    if cfg.enabled and not refresh:
        hit = await csvc.lookup(domain_id=domain_id, question=body.q, cfg=cfg)
        if hit is not None:
            await csvc.touch(hit.id)
            if wants_sse:
                return StreamingResponse(_sse_cached(hit), media_type="text/event-stream")
            return _cached_result(hit)

    prepared = await qsvc.prepare(domain_id=domain_id, question=body.q)  # raises 404/422
    model = str(getattr(prepared.chat, "chat_model", ""))
    if wants_sse:
        cache = csvc if cfg.enabled else None
        return StreamingResponse(
            _sse_compute(prepared, cache, domain_id=domain_id, question=body.q, model=model),
            media_type="text/event-stream",
        )
    answer = await qsvc.complete(prepared)
    if cfg.enabled and prepared.ctx.refs:
        await csvc.upsert(
            domain_id=domain_id, question=body.q, answer_md=answer.answer_md,
            refs=prepared.ctx.refs, passages=prepared.ctx.passages, model=model,
        )
    return _to_result(answer.answer_md, prepared.ctx)
```

> Both `query_domain` and `suggest_domain` are mounted under `/api/v1` (see `main.py`), giving `/api/v1/domains/{id}/query` and `/api/v1/domains/{id}/suggest`. `suggest` is a GET, so `require_csrf` is not applied (and would be exempt anyway).

- [ ] **Step 6: Run the API tests**

Run: `uv run pytest tests/api/test_query_cache_api.py tests/api/test_suggest_api.py tests/api/test_query_api.py -q`
Expected: PASS. Cache hit/miss + `?refresh=1` prove acceptance #1, #4 (refresh half), #5.

- [ ] **Step 7: Lint + typecheck**

Run: `uv run ruff check src tests/api && uv run mypy src`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/paw/api/routers/query.py tests/api/test_query_cache_api.py tests/api/test_suggest_api.py tests/api/test_query_api.py
git commit -m "feat(api): query-cache lookup/upsert, ?refresh=1, /suggest endpoint"
```

---

## Task 9: Web UI — suggestions dropdown + stale badge + Refresh

**Files:**
- Modify: `src/paw/api/web/routes.py`, `src/paw/api/web/templates/query.html`, `src/paw/api/web/templates/_query_result.html`
- Create: `src/paw/api/web/templates/_suggestions.html`
- Test: `tests/api/test_query_cache_web.py` (create)

- [ ] **Step 1: Write the failing web test**

Create `tests/api/test_query_cache_web.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column, ensure_query_cache_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.users import UserRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class FixedEmbed:
    def __init__(self, default): self.default = default
    async def embed(self, texts, *, model=None): return [self.default for _ in texts]


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="a@b.c", pw_hash=hash_password("pw12345"), role="admin"
    )
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    emb = FixedEmbed([1.0, 0.0, 0.0, 0.0])
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(
        query_mod, "build_chat_provider",
        lambda pc, b: StubChatProvider(script=[StubChatProvider.text("**reliable** means [tcp]")]),
    )
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        c._art = art  # type: ignore[attr-defined]
        c._csrf = c.cookies.get("paw_csrf", "")  # type: ignore[attr-defined]
        yield c


async def test_query_page_has_suggestions_wiring(client):
    r = await client.get(f"/domains/{client._dom.id}/query")
    assert r.status_code == 200
    assert "/suggest" in r.text and "keyup changed delay:300ms" in r.text


async def test_web_query_then_stale_badge_and_refresh(client, db_session):
    url = f"/domains/{client._dom.id}/query"
    h = {"x-csrf-token": client._csrf}
    # first query -> live answer, no stale badge
    r1 = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "<strong>reliable</strong>" in r1.text
    assert "may be outdated" not in r1.text
    # mark the cached entry stale (simulating an article edit on the dependency)
    from paw.services.cache_seam import mark_cache_stale
    await mark_cache_stale(db_session, domain_id=client._dom.id, article_ids=[client._art.id])
    await db_session.commit()
    # second identical query -> served from cache, now flagged + Refresh present
    r2 = await client.post(url, data={"q": "what is reliable?"}, headers=h)
    assert "may be outdated" in r2.text
    assert "refresh=1" in r2.text


async def test_web_suggest_returns_fragment(client, db_session):
    repo = QueryCacheRepo(db_session)
    await ensure_query_cache_embedding_column(db_session, 4)
    cid = await repo.upsert(
        domain_id=client._dom.id, query_norm="tcp handshake", answer_md="A", refs=[],
        passages=[], model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await repo.touch(cache_id=cid)
    await db_session.commit()
    r = await client.get(f"/domains/{client._dom.id}/suggest", params={"q": "tcp"})
    assert r.status_code == 200
    assert "tcp handshake" in r.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_query_cache_web.py -q`
Expected: FAIL — no `/suggest` web route, no stale badge.

- [ ] **Step 3: Update `query.html`**

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
  <input type="text" name="q" placeholder="Ask a question…" autocomplete="off" required
         hx-get="/domains/{{ domain.id }}/suggest"
         hx-trigger="keyup changed delay:300ms"
         hx-target="#suggestions" hx-swap="innerHTML">
  <button type="submit">Ask</button>
</form>
<div id="suggestions" class="suggestions"></div>
<section id="query-result" class="query-result"></section>
{% endblock %}
```

- [ ] **Step 4: Create `_suggestions.html`**

Create `src/paw/api/web/templates/_suggestions.html`:

```html
{% if suggestions %}
<ul class="suggestion-list">
  {% for s in suggestions %}
  <li>
    <form hx-post="/domains/{{ domain.id }}/query"
          hx-headers='{"x-csrf-token": "{{ csrf }}"}'
          hx-target="#query-result" hx-swap="innerHTML">
      <input type="hidden" name="q" value="{{ s }}">
      <button type="submit" class="suggestion">{{ s }}</button>
    </form>
  </li>
  {% endfor %}
</ul>
{% endif %}
```

> A hidden input (not `hx-vals`) carries the suggestion text so quotes/special chars are HTML-attribute-escaped by Jinja and submitted intact. Clicking a suggestion runs that query — the FAQ effect.

- [ ] **Step 5: Update `_query_result.html`**

Replace `src/paw/api/web/templates/_query_result.html` with:

```html
{% if cached and stale %}
<div class="stale-badge" role="status">
  ⚠️ This answer may be outdated.
  <form hx-post="/domains/{{ domain_id }}/query?refresh=1"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}'
        hx-target="#query-result" hx-swap="innerHTML">
    <input type="hidden" name="q" value="{{ question }}">
    <button type="submit">Refresh</button>
  </form>
</div>
{% endif %}
<article class="answer">{{ answer_html | safe }}</article>
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

> `r.slug` / `p.heading_path` work for both `Ref`/`Passage` dataclasses (live answer) and plain dicts (cached) — Jinja falls back to item access.

- [ ] **Step 6: Update the web routes**

In `src/paw/api/web/routes.py`:

(a) Add imports near the existing service imports:

```python
from paw.api.deps import get_redis
from paw.services.query_cache import QueryCacheService
```
(`get_redis` is in `paw.api.deps`; add it to the existing `from paw.api.deps import (...)` block instead of a second import if you prefer — either works.)

(b) Add `csrf` to the `query_page` template context so the suggestions/refresh mini-forms can post. It already passes `csrf`; confirm the existing `query_page` returns `{"domain": domain, "csrf": csrf}` (it does — no change needed).

(c) Replace the `web_query` handler with:

```python
@router.post("/domains/{domain_id}/query", response_class=HTMLResponse)
async def web_query(
    domain_id: uuid.UUID,
    request: Request,
    q: str = Form(...),
    refresh: int = 0,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor", "viewer")),
) -> Response:
    csrf = request.cookies.get(CSRF_COOKIE, "")
    csvc = QueryCacheService(session).with_redis(get_redis())
    qsvc = QueryService(session).with_redis(get_redis())
    cfg = await csvc.config(domain_id)

    if cfg.enabled and not refresh:
        hit = await csvc.lookup(domain_id=domain_id, question=q, cfg=cfg)
        if hit is not None:
            await csvc.touch(hit.id)
            return templates.TemplateResponse(
                request,
                "_query_result.html",
                {
                    "answer_html": render_markdown(hit.answer_md),
                    "refs": hit.refs,
                    "passages": hit.passages,
                    "cached": True,
                    "stale": hit.stale,
                    "domain_id": domain_id,
                    "question": q,
                    "csrf": csrf,
                },
            )

    answer = await qsvc.answer(domain_id=domain_id, question=q)
    if cfg.enabled and answer.refs:
        await csvc.upsert(
            domain_id=domain_id, question=q, answer_md=answer.answer_md,
            refs=answer.refs, passages=answer.passages, model="",
        )
    return templates.TemplateResponse(
        request,
        "_query_result.html",
        {
            "answer_html": render_markdown(answer.answer_md),
            "refs": answer.refs,
            "passages": answer.passages,
            "cached": False,
            "stale": False,
            "domain_id": domain_id,
            "question": q,
            "csrf": csrf,
        },
    )
```

(d) Add the web suggest route right after `web_query`:

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
    domain = await DomainRepo(session).get(domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    svc = QueryCacheService(session)
    cfg = await svc.config(domain_id)
    suggestions = await svc.suggest(domain_id=domain_id, q=q, top_k=cfg.suggest_top_k)
    return templates.TemplateResponse(
        request,
        "_suggestions.html",
        {"domain": domain, "suggestions": suggestions, "csrf": csrf},
    )
```

- [ ] **Step 7: Run the web tests**

Run: `uv run pytest tests/api/test_query_cache_web.py -q`
Expected: PASS (3 passed). Proves the stale-badge half of acceptance #4 and the web side of #5.

- [ ] **Step 8: Run the existing web query tests for no regression**

Run: `uv run pytest tests/api/test_query_web.py -q`
Expected: PASS (2 passed) — sanitized answer + source chip still render.

- [ ] **Step 9: Lint + typecheck**

Run: `uv run ruff check src tests/api && uv run mypy src`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/query.html src/paw/api/web/templates/_query_result.html src/paw/api/web/templates/_suggestions.html tests/api/test_query_cache_web.py
git commit -m "feat(web): suggestions dropdown + stale badge + Refresh on Query screen"
```

---

## Task 10: Dim-change tie-in — clear/rebuild the query-cache embedding

**Files:**
- Modify: `src/paw/services/provider_settings.py` (`update_provider`)
- Test: `tests/integration/test_query_cache_dim_change.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_query_cache_dim_change.py`:

```python
from sqlalchemy import text

from paw.db.managed import ensure_query_cache_embedding_column, query_cache_embedding_dim
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.domains import DomainRepo
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService

_FERNET = "k" * 43 + "="


async def test_dim_change_clears_and_rebuilds_query_cache(db_session):
    box = SecretBox(_FERNET)
    psvc = ProviderSettingsService(db_session, box=box)
    # initial provider at dim 4 + a cache row at dim 4
    await psvc.set_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    await QueryCacheRepo(db_session).upsert(
        domain_id=dom.id, query_norm="q", answer_md="A", refs=[], passages=[],
        model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
    )
    await db_session.commit()
    assert await query_cache_embedding_dim(db_session) == 4

    # change dim -> chunks rebuild + cache cleared & rebuilt at the new dim
    await psvc.update_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    assert await query_cache_embedding_dim(db_session) == 8
    remaining = (await db_session.execute(text("SELECT count(*) FROM query_cache"))).scalar_one()
    assert remaining == 0  # stale-dim answers cleared
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_query_cache_dim_change.py -q`
Expected: FAIL — after the dim change the cache row is still present and/or the column dim is still 4.

- [ ] **Step 3: Hook the cache rebuild into `update_provider`**

In `src/paw/services/provider_settings.py`:

(a) Add `rebuild_query_cache_embedding_column` to the `from paw.db.managed import ...` line:

```python
from paw.db.managed import (
    ensure_embedding_column,
    rebuild_embedding_column,
    rebuild_query_cache_embedding_column,
)
```

(b) In `update_provider`, inside the existing dim-change branch, add the cache rebuild right after the chunk rebuild:

```python
        if current is not None and current != embedding_dim:
            await rebuild_embedding_column(self._s, embedding_dim)
            await rebuild_query_cache_embedding_column(self._s, embedding_dim)
            await self.bump_embedding_version()
        else:
            await ensure_embedding_column(self._s, embedding_dim)
```

> `rebuild_query_cache_embedding_column` truncates `query_cache` (cascading to `query_cache_articles`) and re-adds `query_embedding vector(dim)` + HNSW — the cache is an optimization, so clearing it on a dim change is safe and keeps it dim-locked like `chunks.embedding`. The table always exists post-migration, so this is safe even when the cache was never populated.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/integration/test_query_cache_dim_change.py -q`
Expected: PASS. Addresses the spec risk note (dim change reindexes/clears the cache).

- [ ] **Step 5: Confirm no regression in the existing provider-settings tests**

Run: `uv run pytest tests/integration/test_provider_settings.py tests/integration/test_reindex.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + typecheck**

Run: `uv run ruff check src tests/integration/test_query_cache_dim_change.py && uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/paw/services/provider_settings.py tests/integration/test_query_cache_dim_change.py
git commit -m "feat(cache): clear+rebuild query_cache embedding on provider dim change"
```

---

## Task 11: E2E — query → cached → edit cited article → stale → refresh → fresh

**Files:**
- Test: `tests/e2e/test_query_cache_e2e.py` (create)

This task adds no production code — it is the end-to-end proof of acceptance #1, #3, #4 in one flow, exercising the real services together.

- [ ] **Step 1: Write the E2E test**

Create `tests/e2e/test_query_cache_e2e.py`:

```python
from tests.stubs import StubChatProvider

import paw.services.query as query_mod
import paw.services.query_cache as cache_mod
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.fix import apply_fix, FixProposal
from paw.harness.ops.lint import LintIssue
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.query import QueryService
from paw.services.query_cache import QueryCacheService
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


class FixedEmbed:
    def __init__(self, default): self.default = default
    async def embed(self, texts, *, model=None): return [self.default for _ in texts]


async def test_query_cached_then_edit_marks_stale_then_refresh(db_session, monkeypatch):
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=4, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 4)
    emb = FixedEmbed([1.0, 0.0, 0.0, 0.0])
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    monkeypatch.setattr(query_mod, "build_embedding_provider", lambda pc, b: emb)
    monkeypatch.setattr(cache_mod, "build_embedding_provider", lambda pc, b: emb)

    answers = iter(["v1 reliable [tcp]", "v2 reliable [tcp]"])

    def chat(pc, b):
        return StubChatProvider(script=[StubChatProvider.text(next(answers))])

    monkeypatch.setattr(query_mod, "build_chat_provider", chat)

    qsvc = QueryService(db_session, fernet_key=_FERNET)
    csvc = QueryCacheService(db_session, fernet_key=_FERNET)
    cfg = await csvc.config(dom.id)
    Q = "is tcp reliable?"

    # 1) MISS -> compute v1 -> upsert (with art dependency)
    a1 = await qsvc.answer(domain_id=dom.id, question=Q)
    await csvc.upsert(domain_id=dom.id, question=Q, answer_md=a1.answer_md,
                      refs=a1.refs, passages=a1.passages, model="m")
    assert a1.answer_md == "v1 reliable [tcp]"

    # 2) HIT (fresh) -> cached v1, no LLM
    hit = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert hit is not None and hit.answer_md == "v1 reliable [tcp]" and hit.stale is False

    # 3) edit the cited article via the fix op (uses the real seam) -> entry goes stale
    fix_chat = StubChatProvider(script=[StubChatProvider.text("ignored")])
    await apply_fix(
        db_session, domain_id=dom.id,
        issue=LintIssue(id="i1", kind="thin", target_slug="tcp", detail="d", fix="f"),
        proposal=FixProposal(markdown="## TCP\nNow with more detail.", summary="s"),
        cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    stale_hit = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert stale_hit is not None and stale_hit.stale is True
    assert stale_hit.answer_md == "v1 reliable [tcp]"  # still served, just flagged

    # 4) refresh -> recompute v2 -> upsert clears stale
    a2 = await qsvc.answer(domain_id=dom.id, question=Q)
    await csvc.upsert(domain_id=dom.id, question=Q, answer_md=a2.answer_md,
                      refs=a2.refs, passages=a2.passages, model="m")
    fresh = await csvc.lookup(domain_id=dom.id, question=Q, cfg=cfg)
    assert fresh is not None and fresh.answer_md == "v2 reliable [tcp]" and fresh.stale is False
```

> `apply_fix`'s signature is `apply_fix(session, *, domain_id, issue, proposal, cfg, author_id)` and `LintIssue` carries `id, kind, target_slug, detail, fix` — confirm against `harness/ops/fix.py` and `harness/ops/lint.py` before running; adjust kwargs if the local `LintIssue` constructor differs.

- [ ] **Step 2: Run the E2E test**

Run: `uv run pytest tests/e2e/test_query_cache_e2e.py -q`
Expected: PASS. If `LintIssue`/`apply_fix` kwargs differ, fix the constructor call (read `harness/ops/lint.py` for the exact `LintIssue` fields) — the flow logic stays the same.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_query_cache_e2e.py
git commit -m "test(e2e): query->cache->edit->stale->refresh->fresh"
```

---

## Task 12: Full-suite gate + docs

**Files:** none (verification) · optionally docs if a `docs/wiki/` exists.

- [ ] **Step 1: Run the entire suite (CI parity)**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: all three pass. This is exactly what CI runs.

- [ ] **Step 2: Re-read the acceptance criteria against the suite**

Confirm each spec acceptance criterion maps to a green test:

1. Repeated identical query served from cache, 0 LLM calls → `test_query_cache_service.py::test_miss_then_exact_hit_skips_llm`, `test_query_cache_api.py::test_second_identical_query_served_from_cache`.
2. Paraphrase within threshold hits via ANN; below misses → `test_query_cache_service.py::test_ann_hit_within_threshold_else_miss`.
3. Editing a cited article marks dependents stale, same transaction → `test_cache_stale_seam.py` (both tests), `test_query_cache_e2e.py`.
4. Stale hit returns flagged answer; Refresh recomputes + clears stale → `test_query_cache_web.py::test_web_query_then_stale_badge_and_refresh`, `test_query_cache_api.py::test_refresh_bypasses_and_recomputes`, `test_query_cache_e2e.py`.
5. `GET /suggest?q=` ranks by hit_count → `test_suggest_api.py`, `test_query_cache_repo.py::test_suggest_ranks_by_hit_count`.
6. `gc_housekeeping` removes expired entries → `test_query_cache_gc.py`.

- [ ] **Step 3: (Only if `docs/wiki/` exists)** Update docs via iwiki

This repo currently has **no** `docs/wiki/` (the session reminder confirms iwiki is not initialised). Skip iwiki ingest/lint. If a `docs/wiki/` has since been created, run `iwiki:iwiki-ingest src/paw/services/query_cache.py` and `/iwiki-lint`.

- [ ] **Step 4: Finish the branch**

Use **superpowers:finishing-a-development-branch** to open the PR for `dev/paw-phase-3` (or a fresh `dev/paw-phase-7` cut from the up-to-date base branch — confirm the correct base, since this repo has long-lived `dev/*` branches) into the agreed target. Do not merge to `master` directly.

---

## Self-Review (performed during planning)

- **Spec coverage:** DB tables (Task 2) · exact+ANN lookup before retrieval (Task 5, 8) · eager transactional stale-marking via the seam (Task 6) · stale flag + Refresh (Task 8, 9) · suggestions ranked by hit_count (Task 8, 9) · GC TTL (Task 7) · config block global+per-domain (Task 1, 5) · per-domain isolation (cache rows keyed by `domain_id`, lookups filter on it) · dim-lock + reindex tie-in (Task 10). All in-scope items map to a task.
- **Type/name consistency:** `mark_cache_stale(session, *, domain_id, article_ids)` used identically in cache_seam + all three writers + tests. `QueryCacheConfig` fields (`enabled`, `sim_threshold`, `ttl_seconds`, `suggest_top_k`) consistent across config, service, GC, router. `CacheHit(id, answer_md, refs, passages, stale)` consistent across service + router. `QueryCacheRepo` method names (`get_by_norm`, `ann_nearest`, `upsert`, `set_deps`, `touch`, `set_stale`, `mark_stale_for_articles`, `suggest`, `delete_expired`) match all call sites. `QueryResult` gains `stale`/`cached` consistently (router + updated shape test).
- **Decisions locked:** stale precision is **article-level** (per user choice); `suggest` uses a prefix `ILIKE` (the as-you-type case) ranked by `hit_count` — a deliberate v1 simplification of the spec's "FTS/ANN" (noted in Task 4); dont-know / empty-refs answers are **not** cached (no dependencies to invalidate them); chat is never cached (untouched). TTL is measured from `COALESCE(last_hit_at, created_at)`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-paw-phase-7-query-cache.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
2. **Inline Execution** — execute tasks in this session with checkpoints. REQUIRED SUB-SKILL: superpowers:executing-plans.

Which approach?
