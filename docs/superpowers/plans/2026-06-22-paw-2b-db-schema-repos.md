---
review:
  plan_hash: bc409844890b1a52
  spec_hash: 5a6ed65bca4c1452
  last_run: 2026-06-22
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      severity: WARNING
      section: "Task 4 / ChunkRepo.set_embedding"
      text: "Vector literal is built with repr(float(x)); repr() can emit scientific notation (e.g. 1e-05) and 'inf'/'nan', which pgvector's text input parser may reject or misparse. Use format(x, 'r') guard or reject non-finite values."
      verdict: open
    - id: F-002
      severity: WARNING
      section: "Task 5 / append_log"
      text: "append_log shows an inline __import__('json') shim in the code block, then a prose instruction to replace it with a top-level import json. The literal code block as written would ship the shim if a worker copies it verbatim; the canonical final code should be the only code shown."
      verdict: open
    - id: F-003
      severity: WARNING
      section: "Task 5 / reconcile_stuck"
      text: "reconcile_stuck(older_than_seconds=0) relies on heartbeat_at IS NULL to catch a just-started running job. A job that DID heartbeat within 0s window is also matched (now() - 0 == now()); boundary semantics (>= vs >) are untested and could reconcile a live job. Acceptable for the test but worth an explicit comment."
      verdict: open
    - id: F-004
      severity: WARNING
      section: "Global Constraints / index naming"
      text: "Plan states explicit CREATE INDEX names 'must match the ix_%(column_0_label)s style where practical' but hand-written names (ix_chunks_tsv, ix_links_domain_id, etc.) are simple ix_<table>_<col>, not the label form NAMING_CONVENTION would generate. Harmless (raw SQL bypasses the convention) but the stated must-match is not actually enforced/verified."
      verdict: open
  chain:
    intent: null
    spec: docs/superpowers/specs/2026-06-22-paw-phase-2-ingest-design.md
---

# Phase 2B — DB Schema + Migration + Repos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Phase 2 persistence layer — `entities`, `article_entities`, `links`, `citations`, `chunks`, `chunk_entities`, `jobs` tables (with the GIN index on `chunks.tsv` and an index on `embedding_version`), the alembic `0002` migration, the **managed embedding-dim migration** that creates the `vector(dim)` column + HNSW index at runtime, and the repos + `graph/repo` upserts that the ingest pipeline (Plan 2C) and jobs (Plan 2D) build on.

**Architecture:** Mirror the established split: ORM models in `db/models.py`, raw-SQL DDL in `alembic/versions/0002_*.py`, one repo per aggregate under `db/repos/` (`add()`+`flush()`, never `commit()`). The `chunks.embedding` column is **not** in the alembic migration because its dimension is a runtime choice (`ProviderConfig.embedding_dim`); a managed migration (`db/managed.py`) adds `embedding vector(dim)` + HNSW idempotently. Embeddings are written/queried via raw SQL string-cast (`CAST(:v AS vector)`) to avoid a new asyncpg codec dependency. `graph/repo.py` provides entity/link upserts and the co-occurrence query the linker consumes.

**Tech Stack:** PostgreSQL 16 + `pgvector` (extension already enabled in baseline `0001`) · SQLAlchemy 2 async ORM · Alembic (raw-SQL `op.execute` style) · pytest + testcontainers (`pgvector/pgvector:pg16`).

## Global Constraints

- Python `>=3.12`; ORM models extend `paw.db.base.Base`; new repos under `src/paw/db/repos/`.
- Lint/type/test gates (must stay green): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`.
- **Transaction rule:** repos `flush()`, services `commit()`. No `commit()` in repos.
- Migration style matches `0001_baseline.py`: `op.execute("""CREATE TABLE …""")` raw SQL, enums via `CREATE TYPE`, `down_revision` chained, `downgrade()` drops in reverse FK order. **`down_revision = "0001_baseline"`**.
- Column conventions (verbatim from existing models): UUID pk `UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()`; timestamps `DateTime(timezone=True), server_default=func.now()`; JSONB `Mapped[dict[str, Any]]`/`Mapped[list[...]]` `server_default="{}"`/`"[]"`; FKs `ForeignKey("t.id", ondelete="CASCADE"|"SET NULL")`; enums `Enum(*TUPLE, name="…")`; composite keys/uniques in `__table_args__`.
- Naming convention object (`paw.db.base.NAMING_CONVENTION`) auto-names indexes/uniques; explicit `op.execute("CREATE INDEX …")` names must match the `ix_%(column_0_label)s` style where practical.
- `chunks.embedding` (`vector(dim)`) and its HNSW index are **runtime-managed**, never in an alembic revision. The alembic migration creates `chunks` without `embedding`.
- New deps: **none** (pgvector extension already present; embeddings written via raw `CAST(:v AS vector)`).

---

### Task 1: ORM models for the Phase 2 tables

**Files:**
- Modify: `src/paw/db/models.py` (append new models + `JOB_STATUS` enum tuple)
- Test: `tests/unit/test_models.py` (append assertions)

**Interfaces:**
- Produces (consumed by Tasks 2,4,5,6 + plans 2C/2D):
  - `JOB_STATUS = ("queued", "running", "succeeded", "failed", "cancelled")`
  - `Entity(id, domain_id, name, kind: str | None, created_at)` — `__tablename__="entities"`, `UniqueConstraint("domain_id", "name")`.
  - `ArticleEntity(article_id, entity_id)` — `__tablename__="article_entities"`, composite PK.
  - `Link(id, domain_id, src_article_id, dst_article_id, type: str, created_at)` — `__tablename__="links"`, `UniqueConstraint("src_article_id", "dst_article_id", "type")`.
  - `Citation(id, article_id, source_id: uuid | None, quote: str | None, locator: str | None, created_at)` — `__tablename__="citations"`.
  - `Chunk(id, article_id, domain_id, kind, ord, heading_path: str | None, text, embedding_version: int, created_at)` — `__tablename__="chunks"`. **No `embedding` / `tsv` mapped attributes** (managed/raw columns).
  - `ChunkEntity(chunk_id, entity_id)` — `__tablename__="chunk_entities"`, composite PK.
  - `Job(id, domain_id, kind, status, article_id: uuid | None, error: str | None, cancel_requested: bool, log: list, heartbeat_at, created_at, started_at, finished_at)` — `__tablename__="jobs"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_models.py`:

```python
def test_phase2_models_registered():
    from paw.db.base import Base
    from paw.db.models import JOB_STATUS

    tables = set(Base.metadata.tables)
    assert {
        "entities",
        "article_entities",
        "links",
        "citations",
        "chunks",
        "chunk_entities",
        "jobs",
    } <= tables
    assert JOB_STATUS == ("queued", "running", "succeeded", "failed", "cancelled")


def test_chunks_has_no_orm_embedding_column():
    # embedding/tsv are runtime-managed / raw — must NOT be ORM-mapped.
    from paw.db.models import Chunk

    cols = set(Chunk.__table__.columns.keys())
    assert "embedding" not in cols
    assert "tsv" not in cols
    assert "embedding_version" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py -k phase2 -v`
Expected: FAIL — `ImportError: cannot import name 'JOB_STATUS'`

- [ ] **Step 3: Write the implementation**

Append to `src/paw/db/models.py` (after the existing models). Add `Integer` to the existing `sqlalchemy` import line if not already imported:

```python
JOB_STATUS = ("queued", "running", "succeeded", "failed", "cancelled")


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("domain_id", "name"),)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArticleEntity(Base):
    __tablename__ = "article_entities"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("src_article_id", "dst_article_id", "type"),)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    src_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    dst_article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Citation(Base):
    __tablename__ = "citations"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )
    quote: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    # NOTE: `embedding vector(dim)` and `tsv tsvector` are managed/raw columns
    # (see db/managed.py + ChunkRepo raw SQL); intentionally NOT ORM-mapped.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChunkEntity(Base):
    __tablename__ = "chunk_entities"
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*JOB_STATUS, name="job_status"), nullable=False, server_default="queued"
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

If `Integer` is not yet imported, update the import block at the top of `models.py`:

```python
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary,
    String, Text, UniqueConstraint, func,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_models.py -k phase2 -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/models.py tests/unit/test_models.py
git commit -m "feat(db): Phase 2 ORM models (entities/links/citations/chunks/jobs)"
```

---

### Task 2: Alembic migration 0002

**Files:**
- Create: `alembic/versions/0002_phase2_ingest.py`
- Test: `tests/integration/test_migration.py` (append a Phase 2 schema assertion)

**Interfaces:**
- Consumes: baseline `0001` tables (`domains`, `articles`, `sources`).
- Produces: tables `entities`, `article_entities`, `links`, `citations`, `chunks` (with `tsv tsvector` + GIN index + `embedding_version` index, **no** `embedding` column), `chunk_entities`, `jobs` (+ `job_status` enum).

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_migration.py`:

```python
def test_phase2_creates_ingest_tables(pg_sync_url):
    import sqlalchemy as sa
    from sqlalchemy import create_engine

    engine = create_engine(pg_sync_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert {
        "entities",
        "article_entities",
        "links",
        "citations",
        "chunks",
        "chunk_entities",
        "jobs",
    } <= tables
    # chunks has tsv but NOT embedding (embedding is managed at runtime)
    chunk_cols = {c["name"] for c in insp.get_columns("chunks")}
    assert "tsv" in chunk_cols
    assert "embedding_version" in chunk_cols
    assert "embedding" not in chunk_cols
    # GIN index on tsv present
    idx = {i["name"] for i in insp.get_indexes("chunks")}
    assert any("tsv" in name for name in idx)
    engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_migration.py -k phase2 -v`
Expected: FAIL — `entities`/etc not in `tables` (migration 0002 does not exist yet).

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0002_phase2_ingest.py`:

```python
from alembic import op

revision = "0002_phase2_ingest"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE job_status AS ENUM ('queued','running','succeeded','failed','cancelled')")

    op.execute("""
    CREATE TABLE entities (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      name text NOT NULL, kind text,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (domain_id, name))
    """)

    op.execute("""
    CREATE TABLE article_entities (
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      entity_id uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
      PRIMARY KEY (article_id, entity_id))
    """)
    op.execute("CREATE INDEX ix_article_entities_entity_id ON article_entities(entity_id)")

    op.execute("""
    CREATE TABLE links (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      src_article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      dst_article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      type text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (src_article_id, dst_article_id, type))
    """)
    op.execute("CREATE INDEX ix_links_domain_id ON links(domain_id)")

    op.execute("""
    CREATE TABLE citations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      source_id uuid REFERENCES sources(id) ON DELETE SET NULL,
      quote text, locator text,
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("CREATE INDEX ix_citations_article_id ON citations(article_id)")

    # chunks: NO embedding column here (managed migration adds vector(dim) + HNSW).
    op.execute("""
    CREATE TABLE chunks (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      kind text NOT NULL, ord int NOT NULL, heading_path text, text text NOT NULL,
      tsv tsvector,
      embedding_version int NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("CREATE INDEX ix_chunks_article_id ON chunks(article_id)")
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_chunks_embedding_version ON chunks(embedding_version)")

    op.execute("""
    CREATE TABLE chunk_entities (
      chunk_id uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      entity_id uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
      PRIMARY KEY (chunk_id, entity_id))
    """)

    op.execute("""
    CREATE TABLE jobs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      domain_id uuid NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
      kind text NOT NULL,
      status job_status NOT NULL DEFAULT 'queued',
      article_id uuid, error text,
      cancel_requested boolean NOT NULL DEFAULT false,
      log jsonb NOT NULL DEFAULT '[]',
      heartbeat_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz, finished_at timestamptz)
    """)
    op.execute("CREATE INDEX ix_jobs_domain_id ON jobs(domain_id)")
    op.execute("CREATE INDEX ix_jobs_status ON jobs(status)")


def downgrade() -> None:
    for t in ("jobs", "chunk_entities", "chunks", "citations", "links",
              "article_entities", "entities"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP TYPE IF EXISTS job_status")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_migration.py -k phase2 -v`
Expected: PASS

Also re-run the baseline assertion to confirm no regression:

Run: `uv run pytest tests/integration/test_migration.py -v`
Expected: PASS (baseline + phase2)

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_phase2_ingest.py tests/integration/test_migration.py
git commit -m "feat(db): alembic 0002 — Phase 2 ingest tables (chunks GIN tsv, jobs)"
```

---

### Task 3: Managed embedding-dim migration

**Files:**
- Create: `src/paw/db/managed.py`
- Test: `tests/integration/test_managed_migration.py`

**Interfaces:**
- Consumes: an `AsyncSession`.
- Produces (consumed by plans 2C/2D setup wiring):
  - `async def ensure_embedding_column(session: AsyncSession, dim: int) -> None` — idempotently `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(:dim)` then `CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)`. Flushes; **caller commits**.
  - `async def embedding_dim(session: AsyncSession) -> int | None` — returns the current `vector` dimension of `chunks.embedding`, or `None` if the column does not exist yet.

**Note:** Changing dim later (ALTER + HNSW rebuild + reindex) is deferred to the Phase 6 reindex job; `ensure_embedding_column` here only performs the initial create. `dim` is validated to be a positive int before string-interpolating into DDL (DDL cannot use bind params for the type modifier).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_managed_migration.py`:

```python
import pytest

from paw.db.managed import embedding_dim, ensure_embedding_column


async def test_creates_vector_column_and_index_idempotently(db_session):
    assert await embedding_dim(db_session) is None
    await ensure_embedding_column(db_session, 8)
    await db_session.commit()
    assert await embedding_dim(db_session) == 8
    # idempotent: second call must not raise
    await ensure_embedding_column(db_session, 8)
    await db_session.commit()
    assert await embedding_dim(db_session) == 8


async def test_rejects_non_positive_dim(db_session):
    with pytest.raises(ValueError):
        await ensure_embedding_column(db_session, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_managed_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.managed'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/db/managed.py`:

```python
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_HNSW_INDEX = "ix_chunks_embedding_hnsw"


async def ensure_embedding_column(session: AsyncSession, dim: int) -> None:
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    # dim is validated above; safe to interpolate (DDL type modifiers cannot bind).
    await session.execute(
        text(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector({dim})")
    )
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_HNSW_INDEX} "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def embedding_dim(session: AsyncSession) -> int | None:
    row = await session.execute(
        text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = 'chunks' AND a.attname = 'embedding' AND NOT a.attisdropped"
        )
    )
    val = row.scalar_one_or_none()
    # pgvector stores the dimension directly in atttypmod (no -4 VARLENA offset).
    return int(val) if val is not None and val > 0 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_managed_migration.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/managed.py tests/integration/test_managed_migration.py
git commit -m "feat(db): managed embedding-dim migration (vector(dim) + HNSW, idempotent)"
```

---

### Task 4: Entity / citation / chunk repos

**Files:**
- Create: `src/paw/db/repos/entities.py`
- Create: `src/paw/db/repos/citations.py`
- Create: `src/paw/db/repos/chunks.py`
- Test: `tests/integration/test_phase2_repos.py`

**Interfaces:**
- Consumes: models from Task 1; managed `embedding` column from Task 3 (for `set_embedding`).
- Produces (consumed by 2C pipeline):
  - `EntityRepo(session)`:
    - `async def upsert(self, *, domain_id, name, kind=None) -> Entity` — get-or-create by `(domain_id, name)`; flush.
    - `async def tag_article(self, *, article_id, entity_id) -> None` — insert `ArticleEntity` if absent; flush.
    - `async def shared_with(self, *, domain_id, article_id) -> list[tuple[uuid.UUID, int]]` — other articles in the domain sharing ≥1 entity, with shared-entity counts, descending.
  - `CitationRepo(session)`: `async def create(self, *, article_id, source_id, quote, locator) -> Citation` (flush).
  - `ChunkRepo(session)`:
    - `async def create(self, *, article_id, domain_id, kind, ord, heading_path, text_body, embedding_version=1) -> uuid.UUID` — INSERT with `tsv = to_tsvector('english', :text)`; returns new chunk id; flush.
    - `async def set_embedding(self, *, chunk_id, vector: list[float], embedding_version=1) -> None` — `UPDATE chunks SET embedding = CAST(:v AS vector), embedding_version=:ev`; flush.
    - `async def tag_entity(self, *, chunk_id, entity_id) -> None` — insert `ChunkEntity` if absent; flush.
    - `async def count_for_article(self, article_id) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_phase2_repos.py`:

```python
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo


async def _seed_article(db_session, slug="a"):
    dom = await DomainRepo(db_session).create(
        name=f"d-{slug}", source_prefix="s", wiki_prefix="w"
    )
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug=slug, title=slug.upper(), storage_ref="blob:x"
    )
    return dom, art


async def test_entity_upsert_is_idempotent(db_session):
    dom, _ = await _seed_article(db_session)
    repo = EntityRepo(db_session)
    e1 = await repo.upsert(domain_id=dom.id, name="QUIC", kind="protocol")
    e2 = await repo.upsert(domain_id=dom.id, name="QUIC")
    await db_session.commit()
    assert e1.id == e2.id


async def test_shared_with_counts(db_session):
    dom, a1 = await _seed_article(db_session, "a1")
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="blob:y"
    )
    repo = EntityRepo(db_session)
    e_quic = await repo.upsert(domain_id=dom.id, name="QUIC")
    e_udp = await repo.upsert(domain_id=dom.id, name="UDP")
    for e in (e_quic, e_udp):
        await repo.tag_article(article_id=a1.id, entity_id=e.id)
        await repo.tag_article(article_id=a2.id, entity_id=e.id)
    await db_session.commit()
    shared = await repo.shared_with(domain_id=dom.id, article_id=a1.id)
    assert shared == [(a2.id, 2)]


async def test_citation_create(db_session):
    _, art = await _seed_article(db_session)
    c = await CitationRepo(db_session).create(
        article_id=art.id, source_id=None, quote="q", locator="p1"
    )
    await db_session.commit()
    assert c.article_id == art.id


async def test_chunk_create_sets_tsv_and_embedding(db_session):
    dom, art = await _seed_article(db_session)
    await ensure_embedding_column(db_session, 4)
    await db_session.commit()
    repo = ChunkRepo(db_session)
    cid = await repo.create(
        article_id=art.id, domain_id=dom.id, kind="summary", ord=0,
        heading_path=None, text_body="QUIC is a transport protocol",
    )
    await repo.set_embedding(chunk_id=cid, vector=[0.1, 0.2, 0.3, 0.4])
    await db_session.commit()
    assert await repo.count_for_article(art.id) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_phase2_repos.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.repos.entities'`

- [ ] **Step 3: Write the entity repo**

Create `src/paw/db/repos/entities.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import ArticleEntity, Entity


class EntityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, *, domain_id: uuid.UUID, name: str, kind: str | None = None
    ) -> Entity:
        res = await self._s.execute(
            select(Entity).where(Entity.domain_id == domain_id, Entity.name == name)
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            return existing
        e = Entity(domain_id=domain_id, name=name, kind=kind)
        self._s.add(e)
        await self._s.flush()
        return e

    async def tag_article(self, *, article_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        exists = await self._s.get(ArticleEntity, (article_id, entity_id))
        if exists is None:
            self._s.add(ArticleEntity(article_id=article_id, entity_id=entity_id))
            await self._s.flush()

    async def shared_with(
        self, *, domain_id: uuid.UUID, article_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, int]]:
        mine = select(ArticleEntity.entity_id).where(ArticleEntity.article_id == article_id)
        stmt = (
            select(ArticleEntity.article_id, func.count().label("shared"))
            .join(Entity, Entity.id == ArticleEntity.entity_id)
            .where(
                Entity.domain_id == domain_id,
                ArticleEntity.entity_id.in_(mine),
                ArticleEntity.article_id != article_id,
            )
            .group_by(ArticleEntity.article_id)
            .order_by(func.count().desc())
        )
        res = await self._s.execute(stmt)
        return [(row[0], int(row[1])) for row in res.all()]
```

- [ ] **Step 4: Write the citation repo**

Create `src/paw/db/repos/citations.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Citation


class CitationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        article_id: uuid.UUID,
        source_id: uuid.UUID | None,
        quote: str | None,
        locator: str | None,
    ) -> Citation:
        c = Citation(article_id=article_id, source_id=source_id, quote=quote, locator=locator)
        self._s.add(c)
        await self._s.flush()
        return c
```

- [ ] **Step 5: Write the chunk repo**

Create `src/paw/db/repos/chunks.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Chunk, ChunkEntity


class ChunkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        article_id: uuid.UUID,
        domain_id: uuid.UUID,
        kind: str,
        ord: int,
        heading_path: str | None,
        text_body: str,
        embedding_version: int = 1,
    ) -> uuid.UUID:
        row = await self._s.execute(
            text(
                "INSERT INTO chunks "
                "(article_id, domain_id, kind, ord, heading_path, text, tsv, embedding_version) "
                "VALUES (:aid, :did, :kind, :ord, :hp, :txt, "
                "to_tsvector('english', :txt), :ev) RETURNING id"
            ),
            {
                "aid": str(article_id),
                "did": str(domain_id),
                "kind": kind,
                "ord": ord,
                "hp": heading_path,
                "txt": text_body,
                "ev": embedding_version,
            },
        )
        cid = row.scalar_one()
        await self._s.flush()
        return uuid.UUID(str(cid))

    async def set_embedding(
        self, *, chunk_id: uuid.UUID, vector: list[float], embedding_version: int = 1
    ) -> None:
        literal = "[" + ",".join(repr(float(x)) for x in vector) + "]"
        await self._s.execute(
            text(
                "UPDATE chunks SET embedding = CAST(:v AS vector), embedding_version = :ev "
                "WHERE id = :id"
            ),
            {"v": literal, "ev": embedding_version, "id": str(chunk_id)},
        )
        await self._s.flush()

    async def tag_entity(self, *, chunk_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        exists = await self._s.get(ChunkEntity, (chunk_id, entity_id))
        if exists is None:
            self._s.add(ChunkEntity(chunk_id=chunk_id, entity_id=entity_id))
            await self._s.flush()

    async def count_for_article(self, article_id: uuid.UUID) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(Chunk).where(Chunk.article_id == article_id)
        )
        return int(res.scalar_one())
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_phase2_repos.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add src/paw/db/repos/entities.py src/paw/db/repos/citations.py src/paw/db/repos/chunks.py tests/integration/test_phase2_repos.py
git commit -m "feat(db): entity/citation/chunk repos (tsv insert, raw embedding write, co-occurrence query)"
```

---

### Task 5: Jobs repo

**Files:**
- Create: `src/paw/db/repos/jobs.py`
- Test: `tests/integration/test_jobs_repo.py`

**Interfaces:**
- Consumes: `Job` model (Task 1).
- Produces (consumed by 2D worker/API):
  - `JobRepo(session)`:
    - `async def create(self, *, domain_id, kind) -> Job` — status `queued`; flush.
    - `async def get(self, job_id) -> Job | None`
    - `async def set_status(self, job_id, status, *, error=None, article_id=None) -> None` — sets `started_at` when→`running`, `finished_at` when terminal; flush.
    - `async def append_log(self, job_id, entry: dict[str, Any]) -> None` — append to `log` JSONB array; flush.
    - `async def request_cancel(self, job_id) -> None` — set `cancel_requested=true`; flush.
    - `async def is_cancel_requested(self, job_id) -> bool`
    - `async def heartbeat(self, job_id) -> None` — set `heartbeat_at=now()`; flush.
    - `async def reconcile_stuck(self, *, older_than_seconds: int) -> int` — mark `running` jobs with stale `heartbeat_at` as `failed`; returns count; flush.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_jobs_repo.py`:

```python
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo


async def _domain(db_session):
    return await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")


async def test_job_lifecycle_and_log(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    assert job.status == "queued"
    await repo.set_status(job.id, "running")
    await repo.append_log(job.id, {"step": "draft", "msg": "started"})
    await repo.append_log(job.id, {"step": "write", "msg": "done"})
    await repo.set_status(job.id, "succeeded", article_id=None)
    await db_session.commit()
    got = await repo.get(job.id)
    assert got is not None
    assert got.status == "succeeded"
    assert got.started_at is not None and got.finished_at is not None
    assert len(got.log) == 2


async def test_cancel_flag(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await db_session.commit()
    assert await repo.is_cancel_requested(job.id) is False
    await repo.request_cancel(job.id)
    await db_session.commit()
    assert await repo.is_cancel_requested(job.id) is True


async def test_reconcile_marks_stale_running_failed(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.set_status(job.id, "running")
    await db_session.commit()
    # heartbeat_at is NULL right after set_status -> treated as stale
    n = await repo.reconcile_stuck(older_than_seconds=0)
    await db_session.commit()
    assert n == 1
    got = await repo.get(job.id)
    assert got is not None and got.status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_jobs_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.db.repos.jobs'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/db/repos/jobs.py`:

```python
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Job

_TERMINAL = ("succeeded", "failed", "cancelled")


class JobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, domain_id: uuid.UUID, kind: str) -> Job:
        job = Job(domain_id=domain_id, kind=kind, status="queued")
        self._s.add(job)
        await self._s.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self._s.get(Job, job_id)

    async def set_status(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        article_id: uuid.UUID | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if status == "running":
            values["started_at"] = func.now()
        if status in _TERMINAL:
            values["finished_at"] = func.now()
        if error is not None:
            values["error"] = error
        if article_id is not None:
            values["article_id"] = article_id
        await self._s.execute(update(Job).where(Job.id == job_id).values(**values))
        await self._s.flush()

    async def append_log(self, job_id: uuid.UUID, entry: dict[str, Any]) -> None:
        await self._s.execute(
            text("UPDATE jobs SET log = log || CAST(:e AS jsonb) WHERE id = :id"),
            {"e": json.dumps([entry]), "id": str(job_id)},
        )
        await self._s.flush()

    async def request_cancel(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(Job).where(Job.id == job_id).values(cancel_requested=True)
        )
        await self._s.flush()

    async def is_cancel_requested(self, job_id: uuid.UUID) -> bool:
        res = await self._s.execute(select(Job.cancel_requested).where(Job.id == job_id))
        return bool(res.scalar_one_or_none())

    async def heartbeat(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(Job).where(Job.id == job_id).values(heartbeat_at=func.now())
        )
        await self._s.flush()

    async def reconcile_stuck(self, *, older_than_seconds: int) -> int:
        res = await self._s.execute(
            text(
                "UPDATE jobs SET status='failed', error='reconciled: stale heartbeat', "
                "finished_at=now() "
                "WHERE status='running' AND "
                "(heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => :s)) "
                "RETURNING id"
            ),
            {"s": older_than_seconds},
        )
        n = len(res.all())
        await self._s.flush()
        return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_jobs_repo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/db/repos/jobs.py tests/integration/test_jobs_repo.py
git commit -m "feat(db): jobs repo (lifecycle, log append, cancel flag, reconciler)"
```

---

### Task 6: Graph repo (entity/link upserts + co-occurrence)

**Files:**
- Create: `src/paw/graph/__init__.py`
- Create: `src/paw/graph/repo.py`
- Test: `tests/integration/test_graph_repo.py`

**Interfaces:**
- Consumes: `EntityRepo` (Task 4), `Link` model (Task 1).
- Produces (consumed by 2C linker stage D):
  - `GraphRepo(session)`:
    - `async def link(self, *, domain_id, src_article_id, dst_article_id, type: str) -> bool` — insert a `Link` if the `(src,dst,type)` triple is absent; returns `True` if created, `False` if it already existed; flush. Self-links (`src == dst`) are rejected with `ValueError`.
    - `async def cooccurrence_targets(self, *, domain_id, article_id, threshold: int) -> list[uuid.UUID]` — wraps `EntityRepo.shared_with` and returns target article ids whose shared-entity count `>= threshold`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_graph_repo.py`:

```python
import pytest

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.graph.repo import GraphRepo


async def _two_articles(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    a1 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a1", title="A1", storage_ref="blob:1"
    )
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="blob:2"
    )
    return dom, a1, a2


async def test_link_is_idempotent_and_rejects_self(db_session):
    dom, a1, a2 = await _two_articles(db_session)
    repo = GraphRepo(db_session)
    assert await repo.link(domain_id=dom.id, src_article_id=a1.id, dst_article_id=a2.id,
                           type="related") is True
    assert await repo.link(domain_id=dom.id, src_article_id=a1.id, dst_article_id=a2.id,
                           type="related") is False
    await db_session.commit()
    with pytest.raises(ValueError):
        await repo.link(domain_id=dom.id, src_article_id=a1.id, dst_article_id=a1.id,
                        type="related")


async def test_cooccurrence_threshold(db_session):
    dom, a1, a2 = await _two_articles(db_session)
    ents = EntityRepo(db_session)
    for name in ("QUIC", "UDP", "TLS"):
        e = await ents.upsert(domain_id=dom.id, name=name)
        await ents.tag_article(article_id=a1.id, entity_id=e.id)
        await ents.tag_article(article_id=a2.id, entity_id=e.id)
    await db_session.commit()
    repo = GraphRepo(db_session)
    assert await repo.cooccurrence_targets(domain_id=dom.id, article_id=a1.id, threshold=3) == [a2.id]
    assert await repo.cooccurrence_targets(domain_id=dom.id, article_id=a1.id, threshold=4) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_graph_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.graph'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/graph/__init__.py` (empty):

```python
```

Create `src/paw/graph/repo.py`:

```python
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Link
from paw.db.repos.entities import EntityRepo


class GraphRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._entities = EntityRepo(session)

    async def link(
        self,
        *,
        domain_id: uuid.UUID,
        src_article_id: uuid.UUID,
        dst_article_id: uuid.UUID,
        type: str,
    ) -> bool:
        if src_article_id == dst_article_id:
            raise ValueError("cannot link an article to itself")
        res = await self._s.execute(
            select(Link.id).where(
                Link.src_article_id == src_article_id,
                Link.dst_article_id == dst_article_id,
                Link.type == type,
            )
        )
        if res.scalar_one_or_none() is not None:
            return False
        self._s.add(
            Link(
                domain_id=domain_id,
                src_article_id=src_article_id,
                dst_article_id=dst_article_id,
                type=type,
            )
        )
        await self._s.flush()
        return True

    async def cooccurrence_targets(
        self, *, domain_id: uuid.UUID, article_id: uuid.UUID, threshold: int
    ) -> list[uuid.UUID]:
        shared = await self._entities.shared_with(domain_id=domain_id, article_id=article_id)
        return [aid for aid, count in shared if count >= threshold]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_graph_repo.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Final gate + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Expected: all green.

```bash
git add src/paw/graph tests/integration/test_graph_repo.py
git commit -m "feat(graph): graph repo — link upsert + co-occurrence targets"
```

---

## Self-Review

**Spec coverage (against §In scope · DB additions, Graph writes):**
- Tables `entities`, `article_entities`, `links`, `citations`, `chunks`, `chunk_entities`, `jobs` → Tasks 1+2. ✅
- GIN index on `chunks.tsv`, index on `embedding_version` → Task 2. ✅
- HNSW `vector_cosine_ops` via **managed** migration (`vector(dim)` created at setup) → Task 3. ✅
- `graph/repo.py` entity/link upserts used by ingest → Tasks 4 (entity upsert) + 6 (link upsert). ✅
- Co-occurrence over shared `article_entities` ≥ threshold → Tasks 4 (`shared_with`) + 6 (`cooccurrence_targets`). ✅
- `upsert_article` idempotency by `slug` — the *article* upsert lives in `ArticleService`/`ArticleRepo` extension in Plan 2C (write stage C); 2B supplies the entity/chunk/citation/link writes it composes. ✅
- Jobs lifecycle storage (status enum, log, cancel, heartbeat, reconcile) → Tasks 1+5. ✅

**Deferred / consumed-from-elsewhere:** `ProviderConfig.embedding_dim` (Plan 2A) feeds `ensure_embedding_column` — wired into the setup wizard in Plan 2D; the reindex job for dim *changes* is Phase 6.

**Placeholder scan:** none — all steps carry full code or exact commands. (Task 5 Step 3 explicitly directs replacing the local `__import__("json")` shim with a clean top-level `import json`.)

**Type consistency:** `ChunkRepo.create` returns `uuid.UUID` (consumed as chunk id by 2C); `EntityRepo.shared_with` returns `list[tuple[uuid.UUID, int]]` reused by `GraphRepo.cooccurrence_targets`; `JOB_STATUS` strings match the `job_status` enum DDL in Task 2 and the `Job.status` values asserted in Task 5.
