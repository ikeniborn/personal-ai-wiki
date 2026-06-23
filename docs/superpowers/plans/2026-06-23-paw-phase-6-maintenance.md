---
title: "Phase 6 — Maintenance (lint / fix / format / reindex) Implementation Plan"
phase: 6
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-6-maintenance-design.md
review:
  plan_hash: 44d66caecfb26ec3
  spec_hash: 641de51a9bd13d23
  last_run: 2026-06-23
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: INFO
      section: "Task 7: Fix job + queue + service + API"
      section_hash: 12068b3679ea5e6f
      text: "The api-layer smoke test test_fix_endpoint_returns_job_id posts issue_ids=['abc123'] (a non-existent id) and only asserts a 202 + a job_id; it does not exercise that a real selected issue is resolved. End-to-end Fix resolution is covered instead by the integration test test_fix_task_resolves_selected_issue (Task 7 Step 1) and the e2e round-trip (Task 14), so spec acceptance criterion 2 ('a re-run of Lint shows them gone') is still verified — just not by this particular endpoint test."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Accepted. The endpoint test is an intentional thin smoke test (route wiring + 202 + job_id), matching the existing api-layer convention (e.g. test_jobs_api start_ingest). Real Fix resolution — an ai revision written + a fresh Lint no longer emitting the id — is covered by the integration test test_fix_task_resolves_selected_issue (Task 7) and the e2e round-trip (Task 14), so spec acceptance criterion 2 stays verified."
---

# Phase 6 — Maintenance (lint / fix / format / reindex) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a domain's corpus healthy — **Lint** scans a domain read-only and reports issues (broken `[[refs]]`, orphans, stale articles, duplicate entities); **Fix** applies LLM-proposed revisions per selected issue; **Format** normalizes prose without changing facts; **Reindex** re-embeds chunks under a new embedding version — all as domain-locked `arq` jobs with streaming progress and cooperative cancel.

**Architecture:** Lint is **deterministic** — pure detectors (`harness/ops/lint.py`) over data thin repos fetch (article bodies, links, entities), assembled by `run_lint` into a `LintResult{issues[]}` whose issues carry a stable content hash id. Fix and Format are **LLM ops**: per issue/article a structured `chat.structured(...)` proposal is validated against a Pydantic schema, then written via the existing committing-free `upsert_article` (origin=`ai`) plus optional typed links; both write paths call a no-op **query-cache stale seam** that Phase 7 will implement. Reindex is a pure batch planner (`vector/reindex.py`) plus a `ChunkRepo` stale-chunk reader; the "current" embedding version becomes settings-backed so search filters it and a model/dim change can bump it. Four `arq` tasks in `jobs/tasks.py` run each op under the existing per-domain `domain_lock` with the Phase 2 pub/sub + SSE progress infra; a `MaintenanceService` + a `maintenance` router expose the four POST endpoints; the domain page gains Lint/Format/Reindex actions, the job drawer, a Lint-results view and a Fix selection form.

**Tech Stack:** Python 3.12 · `uv` · async SQLAlchemy 2.0 · PostgreSQL 16 + `pgvector` · Redis + `arq` · FastAPI · Jinja2 + HTMX · pytest + testcontainers + stub-LLM.

## Global Constraints

- **Branch:** work on a `dev/paw-phase-6` branch cut from up-to-date `master`; never commit to `master`; close via PR (CLAUDE.md branch workflow).
- **Dependency tool:** `uv` only — never call `pip`/`pytest` directly; always `uv run …`.
- **CI gates (all must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- **Atomicity:** the service/job layer owns the single `session.commit()` per operation. Repos, storage, `upsert_article`, and the ops NEVER commit. A maintenance job batches all its writes and commits once (mirrors `ingest_domain`).
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457). `IntegrityError` auto-maps to 409.
- **Async everywhere:** all DB/IO is async; tests are plain `async def` (`asyncio_mode = auto`).
- **Security:** Redis-backed sessions; `require_role(*roles)` RBAC; CSRF double-submit (`require_csrf`) on every non-GET endpoint. Lint is read-only (any authenticated role); Fix/Format/Reindex are writes → `admin`/`editor` only. Every Fix/Format article write is audited via `paw.audit.log.record`. Domain job-lock prevents concurrent writers.
- **No schema changes** (spec). No new tables/columns. Lint issues are surfaced through the existing `jobs.log` JSONB; the current embedding version lives in the existing `app_settings.settings` JSONB.
- **`testcontainers` for integration/api/e2e** (real Postgres + Redis); only the `unit` layer runs without Docker.
- **Docs:** this project has **no `docs/wiki/`** — skip the iwiki ingest/lint step (global rule).

---

## Scope decisions (read first)

1. **Lint detectors are deterministic, not an LLM walk.** The spec frames Lint as a harness op with a `report_issue` collect tool, but every Lint acceptance criterion and every listed Lint unit test (broken-ref, orphan, duplicate-entity, stale) is deterministic. Phase 6 therefore implements Lint as pure detectors over repo-fetched data — cheaper, faster, fully reproducible, and trivially unit-testable. The `report_issue` collect tool (wired in Phase 4, `harness/tools.py::COLLECT_TOOLS`) stays available and untouched; it is simply not on Lint's critical path. This is the same "pure builder + thin repo, LLM out of the detection loop" choice Phase 5 made for the graph. (Resolves spec finding F-001.)

2. **The "current" embedding version becomes settings-backed.** Phase 3 hard-codes `vector/search.py::CURRENT_EMBEDDING_VERSION = 1`. Reindex (acceptance criterion 4) requires search to follow a *new* version after re-embedding. Phase 6 stores the current version in `app_settings` (`EmbeddingConfig.version`, default 1) and reads it in the two retrieval call sites (`QueryService.prepare`, `ChatService.prepare_turn`). The module constant stays as the default for direct unit/integration calls that pass `embedding_version=` explicitly. An embedding **dim change** (`update_provider`) already drops every embedding via `rebuild_embedding_column`; Phase 6 makes that path also **bump** the version so the now-NULL-embedding chunks are excluded from search until Reindex re-embeds them (a real correctness fix — `vector_arm` would otherwise rank NULL-embedding rows). Reindex itself never bumps; it drains stale chunks up to the configured current version.

3. **Fix targets article-scoped issues; duplicate-entity is report-only.** Fix resolves `broken_ref`, `orphan`, and `stale` issues (each names a target article) by writing a new article revision (origin=`ai`) and optional typed links. `duplicate_entity` issues have no single target article — their fix is an entity *merge*, which is deferred (no acceptance criterion fixes it; criterion 1 only requires Lint to *report* it). A Fix job skips a `duplicate_entity` issue and returns `False`. (Resolves spec finding F-002.)

4. **Fix/Format do not re-chunk or re-embed.** A revised article's chunks go stale w.r.t. search; refreshing them is Reindex's job (run separately) and the query-cache invalidation is the Phase 7 seam. No Phase 6 acceptance criterion requires search to reflect a fix/format immediately. Keeping re-embedding out of Fix/Format keeps each op single-purpose.

5. **The query-cache stale hook is a real callable seam.** `services/cache_seam.py::mark_domain_cache_stale(session, domain_id)` is a no-op `async` function in Phase 6 (the `query_cache` table does not exist yet). Fix and Format call it on every write so Phase 7 implements the body without refactoring the writers (spec "Risks / notes").

6. **UI interactivity is exercised at the API/partial layer, not a headless browser** (Phase 3/4/5 precedent). Web tests assert the action buttons/forms, the rendered Lint-results list, and the Fix checkboxes are present and correctly wired; live HTMX/SSE rendering is verified manually.

7. **Issue identity is a content hash.** `LintIssue.id = sha256("{kind}|{target}|{detail}")[:16]`. Because detectors are deterministic over DB state and the Fix endpoint re-runs Lint before writing, the ids the user selected still resolve; once a write resolves an issue, a fresh Lint no longer emits that id (acceptance criterion 2's "re-run shows them gone").

## File Structure

**Create:**
- `src/paw/services/cache_seam.py` — `mark_domain_cache_stale` no-op Phase 7 seam.
- `src/paw/harness/ops/lint.py` — `LintIssue`, `LintResult`, pure detectors, `run_lint`.
- `src/paw/harness/ops/fix.py` — `FixLink`, `FixProposal`, `propose_fix`, `apply_fix`, `run_fix_issue`.
- `src/paw/harness/ops/format.py` — `FormatProposal`, `check_format_invariant`, `run_format_article`.
- `src/paw/vector/reindex.py` — `plan_batches`, `reindex_domain_chunks`.
- `src/paw/services/maintenance.py` — `MaintenanceService` (start_lint/fix/format/reindex + config resolve).
- `src/paw/api/routers/maintenance.py` — `POST /domains/{id}/lint|fix|format|reindex`.
- `src/paw/api/web/templates/_lint_results.html` — issues list + Fix selection form.
- Tests: `tests/unit/test_maintenance_config.py`, `tests/unit/test_cache_seam.py`, `tests/unit/test_lint_detectors.py`, `tests/unit/test_format_invariant.py`, `tests/unit/test_reindex_planner.py`, `tests/integration/test_lint_op.py`, `tests/integration/test_fix_op.py`, `tests/integration/test_format_op.py`, `tests/integration/test_reindex.py`, `tests/integration/test_embedding_version.py`, `tests/integration/test_maintenance_tasks.py`, `tests/api/test_maintenance_api.py`, `tests/api/test_maintenance_web.py`, `tests/e2e/test_maintenance_e2e.py`.

**Modify:**
- `src/paw/providers/config.py` — add `MAINTENANCE_KEY`, `MaintenanceConfig`, `EMBEDDING_KEY`, `EmbeddingConfig`.
- `src/paw/services/provider_settings.py` — add `get_maintenance`, `get_embedding_version`, `bump_embedding_version`; bump on dim change in `update_provider`.
- `src/paw/security/sanitize.py` — add `extract_wikilink_targets`.
- `src/paw/db/repos/chunks.py` — add `count_stale`, `fetch_stale_batch`.
- `src/paw/db/repos/links.py` — add `domain_link_pairs`.
- `src/paw/db/repos/articles.py` — add `entity_names_for`.
- `src/paw/harness/prompts/__init__.py` — add `fix` + `format` overlays.
- `src/paw/services/query.py` — read configured embedding version.
- `src/paw/services/chat.py` — read configured embedding version.
- `src/paw/jobs/queue.py` — `enqueue_lint`, `enqueue_fix`, `enqueue_format`, `enqueue_reindex`.
- `src/paw/jobs/tasks.py` — `lint_domain`, `fix_issues`, `format_articles`, `reindex_domain` + `MaintenanceCancelled`.
- `src/paw/worker.py` — register the four task functions.
- `src/paw/main.py` — register the `maintenance` router.
- `src/paw/api/web/routes.py` — web Lint/Format/Reindex actions, Lint-results view, web Fix action.
- `src/paw/api/web/templates/domain.html` — Lint/Format/Reindex buttons + results target.

---

## Task 1: MaintenanceConfig + EmbeddingConfig + service accessors

**Files:**
- Modify: `src/paw/providers/config.py`
- Modify: `src/paw/services/provider_settings.py`
- Test: `tests/unit/test_maintenance_config.py`

**Interfaces:**
- Produces:
  - `MAINTENANCE_KEY = "maintenance"`; `MaintenanceConfig(enabled_ops: list[str] = ["lint","fix","format","reindex"], reindex_batch_size: int = 128, stale_days: int = 180)`.
  - `EMBEDDING_KEY = "embedding"`; `EmbeddingConfig(version: int = 1)`.
  - `ProviderSettingsService.get_maintenance() -> MaintenanceConfig`.
  - `ProviderSettingsService.get_embedding_version() -> int`.
  - `ProviderSettingsService.bump_embedding_version() -> int` — increments and persists the version to the session **without committing** (caller owns the commit boundary), returns the new version.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_maintenance_config.py`:

```python
from paw.providers.config import (
    EMBEDDING_KEY,
    MAINTENANCE_KEY,
    EmbeddingConfig,
    MaintenanceConfig,
)


def test_maintenance_config_defaults():
    cfg = MaintenanceConfig()
    assert cfg.enabled_ops == ["lint", "fix", "format", "reindex"]
    assert cfg.reindex_batch_size == 128
    assert cfg.stale_days == 180
    assert MAINTENANCE_KEY == "maintenance"


def test_maintenance_config_override_validates():
    cfg = MaintenanceConfig.model_validate({"enabled_ops": ["lint"], "stale_days": 30})
    assert cfg.enabled_ops == ["lint"]
    assert cfg.stale_days == 30
    assert cfg.reindex_batch_size == 128  # untouched default


def test_embedding_config_default_version():
    assert EmbeddingConfig().version == 1
    assert EMBEDDING_KEY == "embedding"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maintenance_config.py -v`
Expected: FAIL — `cannot import name 'MAINTENANCE_KEY'`.

- [ ] **Step 3: Add the config keys + models to `src/paw/providers/config.py`**

After `GRAPH_KEY = "graph"` add:

```python
MAINTENANCE_KEY = "maintenance"
EMBEDDING_KEY = "embedding"
```

Append after `GraphConfig`:

```python
class MaintenanceConfig(BaseModel):
    enabled_ops: list[str] = Field(
        default_factory=lambda: ["lint", "fix", "format", "reindex"]
    )
    reindex_batch_size: int = 128  # chunks re-embedded per batch
    stale_days: int = 180  # an article older than this (no update) is flagged stale


class EmbeddingConfig(BaseModel):
    version: int = 1  # the embedding_version search filters on; bumped on a model/dim change
```

- [ ] **Step 4: Add the accessors to `ProviderSettingsService`**

In `src/paw/services/provider_settings.py`, extend the `paw.providers.config` import block to also import `EMBEDDING_KEY`, `MAINTENANCE_KEY`, `EmbeddingConfig`, `MaintenanceConfig` (keep it alphabetised). Then add, next to `get_graph`:

```python
    async def get_maintenance(self) -> MaintenanceConfig:
        raw = (await self._all()).get(MAINTENANCE_KEY)
        return MaintenanceConfig.model_validate(raw) if raw else MaintenanceConfig()

    async def get_embedding_version(self) -> int:
        raw = (await self._all()).get(EMBEDDING_KEY)
        return EmbeddingConfig.model_validate(raw).version if raw else EmbeddingConfig().version

    async def bump_embedding_version(self) -> int:
        settings = await self._all()
        raw = settings.get(EMBEDDING_KEY)
        current = EmbeddingConfig.model_validate(raw).version if raw else EmbeddingConfig().version
        nxt = current + 1
        settings[EMBEDDING_KEY] = EmbeddingConfig(version=nxt).model_dump()
        await self._repo.upsert(settings)
        return nxt
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_maintenance_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/providers/config.py src/paw/services/provider_settings.py tests/unit/test_maintenance_config.py
git commit -m "feat(config): MaintenanceConfig + settings-backed embedding version"
```

---

## Task 2: Query-cache stale seam (Phase 7 no-op)

**Files:**
- Create: `src/paw/services/cache_seam.py`
- Test: `tests/unit/test_cache_seam.py`

**Interfaces:**
- Produces: `async def mark_domain_cache_stale(session: AsyncSession, domain_id: uuid.UUID) -> None` — returns `None`; no DB writes in Phase 6.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cache_seam.py`:

```python
import uuid

from paw.services.cache_seam import mark_domain_cache_stale


async def test_seam_is_a_noop_and_returns_none():
    # Passing None for the session proves the Phase 6 seam touches no DB.
    result = await mark_domain_cache_stale(None, uuid.uuid4())  # type: ignore[arg-type]
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cache_seam.py -v`
Expected: FAIL — module `paw.services.cache_seam` does not exist.

- [ ] **Step 3: Create `src/paw/services/cache_seam.py`**

```python
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def mark_domain_cache_stale(session: AsyncSession, domain_id: uuid.UUID) -> None:
    """Phase 7 seam: invalidate cached query answers for a domain after a write.

    No-op until the ``query_cache`` table exists (Phase 7). Fix/Format call this on
    every article write so Phase 7 implements the body without touching the writers.
    """
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cache_seam.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/services/cache_seam.py tests/unit/test_cache_seam.py
git commit -m "feat(services): query-cache stale seam (Phase 7 no-op)"
```

---

## Task 3: Lint pure detectors + wikilink extractor

**Files:**
- Modify: `src/paw/security/sanitize.py`
- Create: `src/paw/harness/ops/lint.py`
- Test: `tests/unit/test_lint_detectors.py`

**Interfaces:**
- Produces (`src/paw/security/sanitize.py`):
  - `extract_wikilink_targets(text: str) -> list[str]` — the `slug` of every `[[slug]]` / `[[slug|label]]` (label dropped), in order, stripped.
- Produces (`src/paw/harness/ops/lint.py`):
  - `LINT_KINDS = ("broken_ref", "orphan", "stale", "duplicate_entity")`.
  - `LintIssue(id: str, kind: str, target_slug: str | None, detail: str, fix: str | None)` (frozen dataclass).
  - `LintResult(issues: list[LintIssue])` (frozen dataclass).
  - `issue_id(kind: str, target: str, detail: str) -> str` — `sha256("{kind}|{target}|{detail}")[:16]`.
  - `find_broken_refs(bodies: list[tuple[str, str]], known_slugs: set[str]) -> list[tuple[str, str]]` — `bodies` are `(article_slug, markdown)`; returns `(article_slug, missing_target_slug)` for each `[[ref]]` not in `known_slugs`.
  - `find_orphans(node_ids: list[T], edges: list[tuple[T, T]]) -> list[T]` — ids that appear in no edge (either endpoint).
  - `find_stale(items: list[tuple[T, datetime]], *, now: datetime, stale_days: int) -> list[T]` — ids whose timestamp is older than `now - stale_days`.
  - `find_duplicate_entities(names: list[str]) -> list[list[str]]` — groups of names equal under `strip().lower()` with more than one member; group order follows first appearance.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_lint_detectors.py`:

```python
from datetime import UTC, datetime, timedelta

from paw.harness.ops.lint import (
    find_broken_refs,
    find_duplicate_entities,
    find_orphans,
    find_stale,
    issue_id,
)
from paw.security.sanitize import extract_wikilink_targets


def test_extract_wikilink_targets_drops_labels():
    md = "See [[tcp]] and [[quic|QUIC protocol]] but not [broken](x)."
    assert extract_wikilink_targets(md) == ["tcp", "quic"]


def test_find_broken_refs_flags_unknown_targets():
    bodies = [("intro", "links to [[tcp]] and [[ghost]]")]
    assert find_broken_refs(bodies, {"intro", "tcp"}) == [("intro", "ghost")]


def test_find_orphans_returns_unlinked_nodes():
    assert find_orphans(["a", "b", "c"], [("a", "b")]) == ["c"]


def test_find_stale_uses_cutoff():
    now = datetime(2026, 6, 23, tzinfo=UTC)
    fresh = now - timedelta(days=10)
    old = now - timedelta(days=400)
    assert find_stale([("a", fresh), ("b", old)], now=now, stale_days=180) == ["b"]


def test_find_duplicate_entities_groups_case_insensitively():
    groups = find_duplicate_entities(["QUIC", "quic", "TCP", " Quic "])
    assert groups == [["QUIC", "quic", " Quic "]]


def test_issue_id_is_stable_and_short():
    a = issue_id("broken_ref", "intro", "ghost")
    b = issue_id("broken_ref", "intro", "ghost")
    assert a == b and len(a) == 16
    assert issue_id("broken_ref", "intro", "other") != a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_lint_detectors.py -v`
Expected: FAIL — `cannot import name 'extract_wikilink_targets'` / module `paw.harness.ops.lint` missing.

- [ ] **Step 3: Add `extract_wikilink_targets` to `src/paw/security/sanitize.py`**

Below the `_WIKILINK` regex definition, add:

```python
def extract_wikilink_targets(text: str) -> list[str]:
    """Return the slug of every [[slug]] / [[slug|label]] occurrence, in order."""
    return [m.group(1).strip() for m in _WIKILINK.finditer(text)]
```

- [ ] **Step 4: Create `src/paw/harness/ops/lint.py`**

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar

from paw.security.sanitize import extract_wikilink_targets

LINT_KINDS = ("broken_ref", "orphan", "stale", "duplicate_entity")

_T = TypeVar("_T")


@dataclass(frozen=True)
class LintIssue:
    id: str
    kind: str
    target_slug: str | None
    detail: str
    fix: str | None


@dataclass(frozen=True)
class LintResult:
    issues: list[LintIssue]


def issue_id(kind: str, target: str, detail: str) -> str:
    return hashlib.sha256(f"{kind}|{target}|{detail}".encode()).hexdigest()[:16]


def find_broken_refs(
    bodies: list[tuple[str, str]], known_slugs: set[str]
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for slug, markdown in bodies:
        for target in extract_wikilink_targets(markdown):
            if target not in known_slugs:
                out.append((slug, target))
    return out


def find_orphans(node_ids: list[_T], edges: list[tuple[_T, _T]]) -> list[_T]:
    linked: set[_T] = set()
    for src, dst in edges:
        linked.add(src)
        linked.add(dst)
    return [n for n in node_ids if n not in linked]


def find_stale(
    items: list[tuple[_T, datetime]], *, now: datetime, stale_days: int
) -> list[_T]:
    cutoff = now - timedelta(days=stale_days)
    return [node for node, ts in items if ts < cutoff]


def find_duplicate_entities(names: list[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(name.strip().lower(), []).append(name)
    return [members for members in groups.values() if len(members) > 1]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_lint_detectors.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/security/sanitize.py src/paw/harness/ops/lint.py tests/unit/test_lint_detectors.py
git commit -m "feat(lint): pure detectors + wikilink target extractor"
```

---

## Task 4: run_lint assembly over repos

**Files:**
- Modify: `src/paw/harness/ops/lint.py`
- Modify: `src/paw/db/repos/links.py`
- Test: `tests/integration/test_lint_op.py`

**Interfaces:**
- Consumes: `ArticleRepo.list_by_domain`, `EntityRepo.list_by_domain`, `PostgresStorage.get`, `MaintenanceConfig`.
- Produces:
  - `LinkRepo.domain_link_pairs(domain_id) -> list[tuple[uuid.UUID, uuid.UUID]]` (added to `src/paw/db/repos/links.py`) — `(src, dst)` for every link in the domain, any type.
  - `async def run_lint(session, *, domain_id: uuid.UUID, cfg: MaintenanceConfig, now: datetime) -> LintResult` — reads articles (slug, id, updated_at, markdown body), domain link pairs, and entity names; returns one `LintIssue` per detector hit. `broken_ref`/`orphan`/`stale` issues set `target_slug` to the article slug; `duplicate_entity` sets `target_slug = None` and lists the variant names in `detail`. Writes nothing.

This task needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_lint_op.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import text

from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.lint import run_lint
from paw.providers.config import MaintenanceConfig
from paw.services.ingest_write import upsert_article


async def _plant(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    # intro -> links to a real [[tcp]] and a broken [[ghost]]
    intro, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[tcp]] and [[ghost]].", summary="", author_id=None,
    )
    tcp, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="tcp", title="TCP",
        markdown="TCP body.", summary="", author_id=None,
    )
    # orphan: no links at all
    orphan, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="lonely", title="Lonely",
        markdown="No links.", summary="", author_id=None,
    )
    # a real link intro -> tcp so neither is an orphan
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=intro.id, dst_article_id=tcp.id, type="related"
    )
    # duplicate entities
    await EntityRepo(db_session).upsert(domain_id=dom.id, name="QUIC")
    await EntityRepo(db_session).upsert(domain_id=dom.id, name="quic")
    # make 'lonely' stale
    await db_session.execute(
        text("UPDATE articles SET updated_at = :t WHERE id = :i"),
        {"t": datetime(2024, 1, 1, tzinfo=UTC), "i": str(orphan.id)},
    )
    await db_session.commit()
    return dom


async def test_run_lint_reports_all_kinds_and_writes_nothing(db_session):
    dom = await _plant(db_session)
    before = (await db_session.execute(text("SELECT count(*) FROM article_revisions"))).scalar_one()

    result = await run_lint(
        db_session, domain_id=dom.id, cfg=MaintenanceConfig(stale_days=180),
        now=datetime(2026, 6, 23, tzinfo=UTC),
    )
    kinds = {i.kind for i in result.issues}
    assert {"broken_ref", "orphan", "stale", "duplicate_entity"} <= kinds
    broken = next(i for i in result.issues if i.kind == "broken_ref")
    assert broken.target_slug == "intro" and "ghost" in broken.detail
    orphan = next(i for i in result.issues if i.kind == "orphan")
    assert orphan.target_slug == "lonely"
    # ids are unique
    assert len({i.id for i in result.issues}) == len(result.issues)

    after = (await db_session.execute(text("SELECT count(*) FROM article_revisions"))).scalar_one()
    assert after == before  # read-only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_lint_op.py -v`
Expected: FAIL — `run_lint` not defined.

- [ ] **Step 3: Add `domain_link_pairs` to `src/paw/db/repos/links.py`**

Append to `LinkRepo`:

```python
    async def domain_link_pairs(
        self, domain_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id).where(Link.domain_id == domain_id)
        )
        return [(r[0], r[1]) for r in res.all()]
```

- [ ] **Step 4: Add `run_lint` to `src/paw/harness/ops/lint.py`**

Add these imports at the top of the file (after the existing imports):

```python
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.entities import EntityRepo
from paw.db.repos.links import LinkRepo
from paw.providers.config import MaintenanceConfig
from paw.storage.postgres import PostgresStorage
```

Append at the end of the file:

```python
async def run_lint(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    cfg: MaintenanceConfig,
    now: datetime,
) -> LintResult:
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    store = PostgresStorage(session)
    known_slugs = {a.slug for a in articles}
    slug_of = {a.id: a.slug for a in articles}

    bodies: list[tuple[str, str]] = []
    for a in articles:
        markdown = (await store.get(a.storage_ref)).decode()
        bodies.append((a.slug, markdown))

    edges = await LinkRepo(session).domain_link_pairs(domain_id)
    entity_names = [e.name for e in await EntityRepo(session).list_by_domain(domain_id)]

    issues: list[LintIssue] = []

    for article_slug, missing in find_broken_refs(bodies, known_slugs):
        issues.append(
            LintIssue(
                id=issue_id("broken_ref", article_slug, missing),
                kind="broken_ref",
                target_slug=article_slug,
                detail=f"broken wikilink [[{missing}]]",
                fix=f"remove or correct the [[{missing}]] link",
            )
        )

    for aid in find_orphans([a.id for a in articles], edges):
        slug = slug_of[aid]
        issues.append(
            LintIssue(
                id=issue_id("orphan", slug, ""),
                kind="orphan",
                target_slug=slug,
                detail="article has no incoming or outgoing links",
                fix="add a link connecting this article to a related one",
            )
        )

    for aid in find_stale(
        [(a.id, a.updated_at) for a in articles], now=now, stale_days=cfg.stale_days
    ):
        slug = slug_of[aid]
        issues.append(
            LintIssue(
                id=issue_id("stale", slug, ""),
                kind="stale",
                target_slug=slug,
                detail=f"not updated in over {cfg.stale_days} days",
                fix="review and refresh the article content",
            )
        )

    for group in find_duplicate_entities(entity_names):
        detail = "duplicate entity names: " + ", ".join(group)
        issues.append(
            LintIssue(
                id=issue_id("duplicate_entity", group[0].strip().lower(), detail),
                kind="duplicate_entity",
                target_slug=None,
                detail=detail,
                fix="merge the duplicate entities (deferred)",
            )
        )

    return LintResult(issues=issues)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_lint_op.py -v`
Expected: PASS (1 test).

- [ ] **Step 6: Run the link-repo regression**

Run: `uv run pytest tests/integration/test_link_repo.py -v`
Expected: PASS (the added method doesn't change existing queries).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/harness/ops/lint.py src/paw/db/repos/links.py tests/integration/test_lint_op.py
git commit -m "feat(lint): run_lint assembles LintResult from repos (read-only)"
```

---

## Task 5: Lint job + queue + MaintenanceService + API

**Files:**
- Create: `src/paw/services/maintenance.py`
- Create: `src/paw/api/routers/maintenance.py`
- Modify: `src/paw/jobs/queue.py`, `src/paw/jobs/tasks.py`, `src/paw/worker.py`, `src/paw/main.py`
- Test: `tests/integration/test_maintenance_tasks.py`, `tests/api/test_maintenance_api.py`

**Interfaces:**
- Produces:
  - `MaintenanceCancelled(Exception)` (in `jobs/tasks.py`).
  - `enqueue_lint(redis=None, *, job_id, domain_id) -> None` (in `jobs/queue.py`).
  - `lint_domain(ctx, job_id, domain_id) -> str` (in `jobs/tasks.py`) — under `domain_lock`; runs `run_lint`; appends `{"step":"issues","issues":[...]}` (each issue as a dict) to the job log; status `succeeded`/`failed`/`cancelled`; returns the status string.
  - `MaintenanceService(session)` with `start_lint(*, domain_id) -> Job` — checks `lint` is enabled, creates a `kind="lint"` job, commits, enqueues, returns the job.
  - `MaintenanceService._resolved_config(domain_id) -> MaintenanceConfig` — global ⊕ `domains.config["maintenance"]` (404 if domain missing).
  - Router `POST /domains/{domain_id}/lint` → `{"job_id": str}` (admin/editor; CSRF).

This task needs Docker (integration + api layers).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_maintenance_tasks.py`:

```python
from __future__ import annotations

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.lint import issue_id
from paw.services.ingest_write import upsert_article


async def _seed_lintable(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[ghost]].", summary="", author_id=None,
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="lint")
    await db_session.commit()
    return dom, job


async def test_lint_task_records_issues_and_writes_nothing(db_session, redis_client, wired_settings):
    dom, job = await _seed_lintable(db_session)
    out = await tasks_mod.lint_domain({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    got = await JobRepo(db_session).get(job.id)
    assert got is not None and got.status == "succeeded"
    issues_entry = next(e for e in got.log if e.get("step") == "issues")
    ids = {i["id"] for i in issues_entry["issues"]}
    assert issue_id("broken_ref", "intro", "broken wikilink [[ghost]]") in ids
```

Create `tests/api/test_maintenance_api.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def ctx(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        csrf = c.cookies.get("paw_csrf")
        dom = (
            await c.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
        ).json()
        yield c, csrf, dom["id"]


async def test_lint_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/lint", headers={"x-csrf-token": csrf})
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])


async def test_lint_requires_csrf(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/lint")
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py -v`
Expected: FAIL — `lint_domain` / `/lint` route missing.

- [ ] **Step 3: Add `enqueue_lint` to `src/paw/jobs/queue.py`**

```python
async def enqueue_lint(
    redis: Any | None = None, *, job_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("lint_domain", str(job_id), str(domain_id))
```

- [ ] **Step 4: Add `MaintenanceCancelled` + `lint_domain` to `src/paw/jobs/tasks.py`**

Add the exception near `IngestCancelled`:

```python
class MaintenanceCancelled(Exception):
    pass
```

Append the task:

```python
async def lint_domain(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from datetime import datetime

    from paw.harness.ops.lint import run_lint
    from paw.services.provider_settings import ProviderSettingsService

    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            try:
                if await jobs.is_cancel_requested(jid):
                    raise MaintenanceCancelled()
                cfg = await ProviderSettingsService(data_s, box=box).get_maintenance()
                result = await run_lint(
                    data_s, domain_id=did, cfg=cfg, now=datetime.now(UTC)
                )
                payload = [
                    {
                        "id": i.id,
                        "kind": i.kind,
                        "target_slug": i.target_slug,
                        "detail": i.detail,
                        "fix": i.fix,
                    }
                    for i in result.issues
                ]
                await jobs.append_log(jid, {"step": "issues", "issues": payload})
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "done"})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": len(payload)}
                )
                return "succeeded"
            except MaintenanceCancelled:
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except Exception as e:  # noqa: BLE001
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
```

- [ ] **Step 5: Create `src/paw/services/maintenance.py`**

```python
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import Job
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.jobs.queue import enqueue_lint
from paw.providers.config import MaintenanceConfig
from paw.services.provider_settings import ProviderSettingsService


class MaintenanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = JobRepo(session)

    async def _resolved_config(self, domain_id: uuid.UUID) -> MaintenanceConfig:
        cfg = await ProviderSettingsService(self._s).get_maintenance()
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        overrides = dom.config.get("maintenance") if isinstance(dom.config, dict) else None
        if isinstance(overrides, dict):
            return MaintenanceConfig.model_validate({**cfg.model_dump(), **overrides})
        return cfg

    async def _require_enabled(self, domain_id: uuid.UUID, op: str) -> None:
        cfg = await self._resolved_config(domain_id)
        if op not in cfg.enabled_ops:
            raise ProblemError(
                status=422, title="Operation disabled", detail=f"{op} is not enabled for this domain"
            )

    async def start_lint(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "lint")
        job = await self._repo.create(domain_id=domain_id, kind="lint")
        await self._s.commit()
        await enqueue_lint(None, job_id=job.id, domain_id=domain_id)
        return job
```

- [ ] **Step 6: Create `src/paw/api/routers/maintenance.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_csrf, require_role
from paw.services.maintenance import MaintenanceService

router = APIRouter(tags=["maintenance"])


@router.post(
    "/domains/{domain_id}/lint",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_lint(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_lint(domain_id=domain_id)
    return {"job_id": str(job.id)}
```

- [ ] **Step 7: Register the task in `src/paw/worker.py` and the router in `src/paw/main.py`**

In `worker.py`, extend the import and the `functions` list:

```python
from paw.jobs.tasks import gc_housekeeping, ingest_domain, lint_domain
```
```python
    functions = [heartbeat, ingest_domain, gc_housekeeping, lint_domain]
```

In `main.py`, add `from paw.api.routers import maintenance as maintenance_router` with the other router imports, and add `maintenance_router` to the `include_router` tuple.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/services/maintenance.py src/paw/api/routers/maintenance.py src/paw/jobs/queue.py src/paw/jobs/tasks.py src/paw/worker.py src/paw/main.py tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py
git commit -m "feat(maintenance): lint job + service + POST /domains/{id}/lint"
```

---

## Task 6: Fix op (propose + apply)

**Files:**
- Create: `src/paw/harness/ops/fix.py`
- Modify: `src/paw/harness/prompts/__init__.py`
- Test: `tests/integration/test_fix_op.py`

**Interfaces:**
- Consumes: `chat.structured`, `upsert_article`, `GraphRepo.link`, `ArticleRepo`, `PostgresStorage.get`, `mark_domain_cache_stale`, `LintIssue`, `WikiConfig`.
- Produces:
  - Prompt overlay `"fix"` in `harness/prompts/__init__.py`.
  - `FixLink(dst_slug: str, type: str)` (Pydantic model).
  - `FixProposal(markdown: str, summary: str = "", add_links: list[FixLink] = [])` (Pydantic model).
  - `async def propose_fix(chat, *, article_title, article_markdown, issue: LintIssue, cfg: WikiConfig) -> FixProposal`.
  - `async def apply_fix(session, *, domain_id, issue: LintIssue, proposal: FixProposal, author_id) -> bool` — resolves the issue's target article by slug (returns `False` if `target_slug` is `None`/unknown), `upsert_article`s the corrected markdown (new revision, origin=`ai`), resolves each `add_links.dst_slug` → id and `GraphRepo.link`s it (skipping unknown/self/disallowed types), records an audit `tool:fix` entry, calls `mark_domain_cache_stale`, returns `True`.
  - `async def run_fix_issue(session, *, domain_id, issue: LintIssue, chat, cfg: WikiConfig, author_id) -> bool` — fetches the target article, calls `propose_fix` then `apply_fix`; returns `False` for unsupported (no target) issues without calling the LLM.

This task needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_fix_op.py`:

```python
from tests.stubs import StubChatProvider

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.fix import run_fix_issue
from paw.harness.ops.lint import LintIssue, issue_id
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


async def test_fix_resolves_broken_ref_with_ai_revision(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[ghost]].", summary="", author_id=None,
    )
    await db_session.commit()

    issue = LintIssue(
        id=issue_id("broken_ref", "intro", "broken wikilink [[ghost]]"),
        kind="broken_ref", target_slug="intro",
        detail="broken wikilink [[ghost]]", fix="remove or correct the [[ghost]] link",
    )
    # stub returns corrected markdown with the broken link removed
    chat = StubChatProvider(
        [StubChatProvider.tool("emit_result", {"markdown": "See the overview.", "summary": ""})]
    )

    ok = await run_fix_issue(
        db_session, domain_id=dom.id, issue=issue, chat=chat,
        cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is True

    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 2  # new ai revision
    revs = await ArticleRepo(db_session).list_revisions(art.id)
    assert revs[0].origin == "ai"
    body = (await PostgresStorage(db_session).get(refreshed.storage_ref)).decode()
    assert "ghost" not in body  # the broken ref is gone -> a fresh lint would not re-flag it


async def test_fix_skips_issue_without_target(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    issue = LintIssue(
        id="x", kind="duplicate_entity", target_slug=None,
        detail="duplicate entity names: QUIC, quic", fix="merge (deferred)",
    )
    chat = StubChatProvider([])  # must not be called
    ok = await run_fix_issue(
        db_session, domain_id=dom.id, issue=issue, chat=chat, cfg=WikiConfig(), author_id=None
    )
    assert ok is False
    assert chat.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_fix_op.py -v`
Expected: FAIL — module `paw.harness.ops.fix` missing.

- [ ] **Step 3: Add the `fix` prompt overlay**

In `src/paw/harness/prompts/__init__.py`, add to `_OVERLAYS`:

```python
    "fix": (
        "You are repairing one wiki article to resolve a specific quality ISSUE. "
        "You are given the article markdown and the issue. Return corrected article "
        "markdown that resolves the issue WITHOUT inventing facts: keep all real "
        "content, only fix the specific problem (e.g. remove or correct a broken "
        "[[link]]). Headings '##' only. Optionally propose typed links to add."
    ),
```

- [ ] **Step 4: Create `src/paw/harness/ops/fix.py`**

```python
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from paw.audit.log import record
from paw.db.repos.articles import ArticleRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.lint import LintIssue
from paw.harness.prompts import get_prompt
from paw.providers.base import ChatProvider, Message
from paw.providers.config import WikiConfig
from paw.services.cache_seam import mark_domain_cache_stale
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


class FixLink(BaseModel):
    dst_slug: str
    type: str


class FixProposal(BaseModel):
    markdown: str
    summary: str = ""
    add_links: list[FixLink] = Field(default_factory=list)


async def propose_fix(
    chat: ChatProvider,
    *,
    article_title: str,
    article_markdown: str,
    issue: LintIssue,
    cfg: WikiConfig,
) -> FixProposal:
    system = get_prompt(
        "fix", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    user = (
        f"ISSUE ({issue.kind}): {issue.detail}\nSUGGESTED FIX: {issue.fix}\n\n"
        f"ARTICLE TITLE: {article_title}\nARTICLE MARKDOWN:\n{article_markdown}"
    )
    return await chat.structured(  # type: ignore[attr-defined]
        [Message(role="system", content=system), Message(role="user", content=user)],
        FixProposal,
        retries=cfg.max_retries,
    )


async def apply_fix(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    issue: LintIssue,
    proposal: FixProposal,
    author_id: uuid.UUID | None,
) -> bool:
    if issue.target_slug is None:
        return False
    repo = ArticleRepo(session)
    slug_map = await repo.slug_id_map(domain_id)
    target_id = slug_map.get(issue.target_slug)
    if target_id is None:
        return False
    target = await repo.get(target_id)
    if target is None:
        return False
    art, _ = await upsert_article(
        session,
        domain_id=domain_id,
        slug=target.slug,
        title=target.title,
        markdown=proposal.markdown,
        summary=proposal.summary or (target.summary or ""),
        author_id=author_id,
    )
    graph = GraphRepo(session)
    allowed = WikiConfig().link_types
    for link in proposal.add_links:
        dst_id = slug_map.get(link.dst_slug)
        if dst_id is None or dst_id == art.id or link.type not in allowed:
            continue
        await graph.link(
            domain_id=domain_id, src_article_id=art.id, dst_article_id=dst_id, type=link.type
        )
    await record(
        session,
        user_id=author_id,
        action="tool:fix",
        target_type="article",
        target_id=art.id,
        meta={"issue_kind": issue.kind, "issue_id": issue.id},
    )
    await mark_domain_cache_stale(session, domain_id)
    return True


async def run_fix_issue(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    issue: LintIssue,
    chat: ChatProvider,
    cfg: WikiConfig,
    author_id: uuid.UUID | None,
) -> bool:
    if issue.target_slug is None:
        return False
    repo = ArticleRepo(session)
    slug_map = await repo.slug_id_map(domain_id)
    target_id = slug_map.get(issue.target_slug)
    if target_id is None:
        return False
    target = await repo.get(target_id)
    if target is None:
        return False
    markdown = (await PostgresStorage(session).get(target.storage_ref)).decode()
    proposal = await propose_fix(
        chat,
        article_title=target.title,
        article_markdown=markdown,
        issue=issue,
        cfg=cfg,
    )
    return await apply_fix(
        session, domain_id=domain_id, issue=issue, proposal=proposal, author_id=author_id
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_fix_op.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the prompts regression**

Run: `uv run pytest tests/unit/test_prompts.py -v`
Expected: PASS (adding an overlay key does not break existing prompts).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/harness/ops/fix.py src/paw/harness/prompts/__init__.py tests/integration/test_fix_op.py
git commit -m "feat(fix): LLM-proposed per-issue article revision (origin=ai) + links"
```

---

## Task 7: Fix job + queue + service + API

**Files:**
- Modify: `src/paw/jobs/queue.py`, `src/paw/jobs/tasks.py`, `src/paw/worker.py`, `src/paw/services/maintenance.py`, `src/paw/api/routers/maintenance.py`
- Test: `tests/integration/test_maintenance_tasks.py` (extend), `tests/api/test_maintenance_api.py` (extend)

**Interfaces:**
- Consumes: `run_lint`, `run_fix_issue`, `_build_providers`, `model_lock`.
- Produces:
  - `enqueue_fix(redis=None, *, job_id, domain_id, issue_ids: list[str]) -> None`.
  - `fix_issues(ctx, job_id, domain_id, issue_ids: list[str]) -> str` — under `domain_lock` + `model_lock`; re-runs `run_lint`, selects issues whose `id ∈ issue_ids`, calls `run_fix_issue` per issue (cancel-checked between issues), commits the data session once at the end; logs a `{"step":"fixed","count":n}` entry.
  - `MaintenanceService.start_fix(*, domain_id, issue_ids: list[str]) -> Job`.
  - Router `POST /domains/{domain_id}/fix` body `{"issue_ids": [...]}` → `{"job_id": str}`.

This task needs Docker (integration + api layers).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_maintenance_tasks.py`:

```python
async def test_fix_task_resolves_selected_issue(db_session, redis_client, wired_settings, monkeypatch):
    from datetime import UTC, datetime

    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.harness.ops.lint import run_lint
    from paw.providers.config import MaintenanceConfig, WikiConfig

    dom, _ = await _seed_lintable(db_session)  # 'intro' with a broken [[ghost]]
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="fix")
    await db_session.commit()

    issues = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(),
            now=datetime.now(UTC),
        )
    ).issues
    broken = next(i for i in issues if i.kind == "broken_ref")

    async def fake_build(session, box):
        chat = StubChatProvider(
            [StubChatProvider.tool("emit_result", {"markdown": "Clean body.", "summary": ""})]
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.fix_issues(
        {"redis": redis_client}, str(job.id), str(dom.id), [broken.id]
    )
    assert out == "succeeded"
    # a fresh lint no longer reports the broken ref
    after = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    assert broken.id not in {i.id for i in after}
```

Append to `tests/api/test_maintenance_api.py`:

```python
async def test_fix_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(
        f"/api/v1/domains/{dom}/fix",
        json={"issue_ids": ["abc123"]},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py::test_fix_task_resolves_selected_issue tests/api/test_maintenance_api.py::test_fix_endpoint_returns_job_id -v`
Expected: FAIL — `fix_issues` / `/fix` route missing.

- [ ] **Step 3: Add `enqueue_fix` to `src/paw/jobs/queue.py`**

```python
async def enqueue_fix(
    redis: Any | None = None,
    *,
    job_id: uuid.UUID,
    domain_id: uuid.UUID,
    issue_ids: list[str],
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("fix_issues", str(job_id), str(domain_id), issue_ids)
```

- [ ] **Step 4: Add `fix_issues` to `src/paw/jobs/tasks.py`**

```python
async def fix_issues(
    ctx: dict[str, Any], job_id: str, domain_id: str, issue_ids: list[str]
) -> str:
    from datetime import datetime

    from paw.harness.ops.fix import run_fix_issue
    from paw.harness.ops.lint import run_lint
    from paw.services.provider_settings import ProviderSettingsService

    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    selected = set(issue_ids)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            try:
                chat, _embedder, wiki, _dim = await _build_providers(data_s, box)
                psvc = ProviderSettingsService(data_s, box=box)
                mcfg = await psvc.get_maintenance()
                issues = (
                    await run_lint(data_s, domain_id=did, cfg=mcfg, now=datetime.now(UTC))
                ).issues
                targets = [i for i in issues if i.id in selected]
                fixed = 0
                async with model_lock(redis, getattr(chat, "chat_model", "default")):
                    for issue in targets:
                        if await jobs.is_cancel_requested(jid):
                            raise MaintenanceCancelled()
                        if await run_fix_issue(
                            data_s, domain_id=did, issue=issue, chat=chat,
                            cfg=wiki, author_id=None,
                        ):
                            fixed += 1
                        await jobs.heartbeat(jid)
                        await jobs.append_log(jid, {"step": "fix", "issue_id": issue.id})
                        await job_s.commit()
                        await _safe_publish(redis, jid, {"step": "fix", "issue_id": issue.id})
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "fixed", "count": fixed})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": fixed}
                )
                return "succeeded"
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
```

- [ ] **Step 5: Add `start_fix` to `MaintenanceService`**

In `src/paw/services/maintenance.py`, extend the queue import to `from paw.jobs.queue import enqueue_fix, enqueue_lint` and add:

```python
    async def start_fix(self, *, domain_id: uuid.UUID, issue_ids: list[str]) -> Job:
        await self._require_enabled(domain_id, "fix")
        job = await self._repo.create(domain_id=domain_id, kind="fix")
        await self._s.commit()
        await enqueue_fix(None, job_id=job.id, domain_id=domain_id, issue_ids=issue_ids)
        return job
```

- [ ] **Step 6: Add the `/fix` route**

In `src/paw/api/routers/maintenance.py`, add a `BaseModel` import (`from pydantic import BaseModel`) and:

```python
class FixRequest(BaseModel):
    issue_ids: list[str]


@router.post(
    "/domains/{domain_id}/fix",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_fix(
    domain_id: uuid.UUID, body: FixRequest, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_fix(
        domain_id=domain_id, issue_ids=body.issue_ids
    )
    return {"job_id": str(job.id)}
```

- [ ] **Step 7: Register `fix_issues` in `src/paw/worker.py`**

Extend the import and `functions` list to include `fix_issues`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py -v`
Expected: PASS (all maintenance task + api tests).

- [ ] **Step 9: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/jobs/queue.py src/paw/jobs/tasks.py src/paw/worker.py src/paw/services/maintenance.py src/paw/api/routers/maintenance.py tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py
git commit -m "feat(fix): domain-locked fix job + POST /domains/{id}/fix {issue_ids}"
```

---

## Task 8: Format op (invariant guard + run)

**Files:**
- Create: `src/paw/harness/ops/format.py`
- Modify: `src/paw/harness/prompts/__init__.py`
- Test: `tests/unit/test_format_invariant.py`, `tests/integration/test_format_op.py`

**Interfaces:**
- Consumes: `chat.structured`, `upsert_article`, `mark_domain_cache_stale`, `Article` model, `WikiConfig`.
- Produces:
  - Prompt overlay `"format"`.
  - `FormatProposal(markdown: str)` (Pydantic model).
  - `check_format_invariant(entities: list[str], citations: list[str], new_markdown: str) -> bool` — `True` iff every entity name and every citation quote (case-insensitive substring) still appears in `new_markdown`.
  - `async def run_format_article(session, *, domain_id, article: Article, entity_names: list[str], citation_quotes: list[str], chat, cfg: WikiConfig, author_id) -> bool` — `chat.structured(FormatProposal)`; if the invariant fails, writes nothing and returns `False`; otherwise `upsert_article`s a new revision (origin=`ai`), records `tool:format`, calls `mark_domain_cache_stale`, returns `True`.

This task needs Docker (integration layer for the second test file).

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_format_invariant.py`:

```python
from paw.harness.ops.format import check_format_invariant


def test_invariant_holds_when_facts_preserved():
    assert check_format_invariant(
        ["QUIC", "UDP"], ["runs over UDP"],
        new_markdown="## Overview\n\nQUIC runs over UDP. Reformatted prose.",
    )


def test_invariant_fails_when_entity_dropped():
    assert not check_format_invariant(
        ["QUIC", "UDP"], [], new_markdown="## Overview\n\nQUIC only, no transport named.",
    )


def test_invariant_fails_when_citation_dropped():
    assert not check_format_invariant(
        ["QUIC"], ["runs over UDP"], new_markdown="## Overview\n\nQUIC is a protocol.",
    )
```

- [ ] **Step 2: Run unit test to verify it fails**

Run: `uv run pytest tests/unit/test_format_invariant.py -v`
Expected: FAIL — module `paw.harness.ops.format` missing.

- [ ] **Step 3: Add the `format` prompt overlay**

In `src/paw/harness/prompts/__init__.py`, add to `_OVERLAYS`:

```python
    "format": (
        "Reformat and normalize the article markdown for readability (headings '##' "
        "only, consistent lists and spacing) WITHOUT changing any facts. Every named "
        "entity and every quoted citation present in the original MUST remain present "
        "verbatim. Do not add or remove information. Return only the reformatted markdown."
    ),
```

- [ ] **Step 4: Create `src/paw/harness/ops/format.py`**

```python
from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.audit.log import record
from paw.db.models import Article
from paw.harness.prompts import get_prompt
from paw.providers.base import ChatProvider, Message
from paw.providers.config import WikiConfig
from paw.services.cache_seam import mark_domain_cache_stale
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


class FormatProposal(BaseModel):
    markdown: str


def check_format_invariant(
    entities: list[str], citations: list[str], new_markdown: str
) -> bool:
    haystack = new_markdown.lower()
    for needle in [*entities, *citations]:
        if needle and needle.lower() not in haystack:
            return False
    return True


async def run_format_article(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    article: Article,
    entity_names: list[str],
    citation_quotes: list[str],
    chat: ChatProvider,
    cfg: WikiConfig,
    author_id: uuid.UUID | None,
) -> bool:
    markdown = (await PostgresStorage(session).get(article.storage_ref)).decode()
    system = get_prompt(
        "format", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    proposal = await chat.structured(  # type: ignore[attr-defined]
        [
            Message(role="system", content=system),
            Message(role="user", content=f"ARTICLE MARKDOWN:\n{markdown}"),
        ],
        FormatProposal,
        retries=cfg.max_retries,
    )
    if not check_format_invariant(entity_names, citation_quotes, proposal.markdown):
        return False
    art, _ = await upsert_article(
        session,
        domain_id=domain_id,
        slug=article.slug,
        title=article.title,
        markdown=proposal.markdown,
        summary=article.summary or "",
        author_id=author_id,
    )
    await record(
        session,
        user_id=author_id,
        action="tool:format",
        target_type="article",
        target_id=art.id,
        meta={"slug": article.slug},
    )
    await mark_domain_cache_stale(session, domain_id)
    return True
```

- [ ] **Step 5: Write the failing integration test**

Create `tests/integration/test_format_op.py`:

```python
from tests.stubs import StubChatProvider

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.format import run_format_article
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


async def test_format_writes_revision_preserving_facts(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC runs over UDP.", summary="", author_id=None,
    )
    await db_session.commit()

    # stub keeps the facts, changes the formatting
    chat = StubChatProvider(
        [StubChatProvider.tool(
            "emit_result", {"markdown": "## Overview\n\nQUIC runs over UDP.\n"}
        )]
    )
    ok = await run_format_article(
        db_session, domain_id=dom.id, article=art,
        entity_names=["QUIC", "UDP"], citation_quotes=["runs over UDP"],
        chat=chat, cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is True
    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 2
    body = (await PostgresStorage(db_session).get(refreshed.storage_ref)).decode()
    assert "QUIC runs over UDP" in body and body.startswith("## Overview")


async def test_format_rejects_fact_drift(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC runs over UDP.", summary="", author_id=None,
    )
    await db_session.commit()

    # stub drops the 'UDP' fact -> invariant guard must reject the write
    chat = StubChatProvider(
        [StubChatProvider.tool("emit_result", {"markdown": "## Overview\n\nQUIC is fast."})]
    )
    ok = await run_format_article(
        db_session, domain_id=dom.id, article=art,
        entity_names=["QUIC", "UDP"], citation_quotes=[],
        chat=chat, cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is False
    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 1  # unchanged
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_format_invariant.py tests/integration/test_format_op.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/harness/ops/format.py src/paw/harness/prompts/__init__.py tests/unit/test_format_invariant.py tests/integration/test_format_op.py
git commit -m "feat(format): fact-preserving reformat with entity/citation invariant guard"
```

---

## Task 9: Format job + queue + service + API

**Files:**
- Modify: `src/paw/jobs/queue.py`, `src/paw/jobs/tasks.py`, `src/paw/worker.py`, `src/paw/services/maintenance.py`, `src/paw/api/routers/maintenance.py`, `src/paw/db/repos/articles.py`
- Test: `tests/integration/test_maintenance_tasks.py` (extend), `tests/api/test_maintenance_api.py` (extend)

**Interfaces:**
- Consumes: `run_format_article`, `CitationRepo.list_for_article`, `_build_providers`, `model_lock`.
- Produces:
  - `ArticleRepo.entity_names_for(article_id) -> list[str]` (added to `src/paw/db/repos/articles.py`) — names of entities tagged on the article.
  - `enqueue_format(redis=None, *, job_id, domain_id) -> None`.
  - `format_articles(ctx, job_id, domain_id) -> str` — under `domain_lock` + `model_lock`; for each article in the domain gathers its entity names + citation quotes, runs `run_format_article` (cancel-checked between articles), commits once; logs `{"step":"formatted","count":n}`.
  - `MaintenanceService.start_format(*, domain_id) -> Job`.
  - Router `POST /domains/{domain_id}/format` → `{"job_id": str}`.

This task needs Docker (integration + api layers).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_maintenance_tasks.py`:

```python
async def test_format_task_revises_articles(db_session, redis_client, wired_settings, monkeypatch):
    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.db.repos.articles import ArticleRepo
    from paw.providers.config import WikiConfig
    from paw.services.ingest_write import upsert_article

    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC over UDP.", summary="", author_id=None,
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="format")
    await db_session.commit()

    async def fake_build(session, box):
        chat = StubChatProvider(
            responder=lambda msgs, tools: StubChatProvider.tool(
                "emit_result", {"markdown": "## Overview\n\nQUIC over UDP."}
            )
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.format_articles({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 2
```

Append to `tests/api/test_maintenance_api.py`:

```python
async def test_format_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/format", headers={"x-csrf-token": csrf})
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py::test_format_task_revises_articles tests/api/test_maintenance_api.py::test_format_endpoint_returns_job_id -v`
Expected: FAIL — `format_articles` / `/format` route missing.

- [ ] **Step 3: Add `entity_names_for` to `src/paw/db/repos/articles.py`**

Change the model import line to `from paw.db.models import Article, ArticleEntity, ArticleRevision, Entity`, then add to `ArticleRepo`:

```python
    async def entity_names_for(self, article_id: uuid.UUID) -> list[str]:
        res = await self._s.execute(
            select(Entity.name)
            .join(ArticleEntity, ArticleEntity.entity_id == Entity.id)
            .where(ArticleEntity.article_id == article_id)
            .order_by(Entity.name)
        )
        return [r[0] for r in res.all()]
```

- [ ] **Step 4: Add `enqueue_format` to `src/paw/jobs/queue.py`**

```python
async def enqueue_format(
    redis: Any | None = None, *, job_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("format_articles", str(job_id), str(domain_id))
```

- [ ] **Step 5: Add `format_articles` to `src/paw/jobs/tasks.py`**

```python
async def format_articles(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from paw.db.repos.articles import ArticleRepo
    from paw.db.repos.citations import CitationRepo
    from paw.harness.ops.format import run_format_article

    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            try:
                chat, _embedder, wiki, _dim = await _build_providers(data_s, box)
                repo = ArticleRepo(data_s)
                citations = CitationRepo(data_s)
                articles = await repo.list_by_domain(did)
                formatted = 0
                async with model_lock(redis, getattr(chat, "chat_model", "default")):
                    for art in articles:
                        if await jobs.is_cancel_requested(jid):
                            raise MaintenanceCancelled()
                        names = await repo.entity_names_for(art.id)
                        quotes = [
                            c.quote
                            for c in await citations.list_for_article(art.id)
                            if c.quote
                        ]
                        if await run_format_article(
                            data_s, domain_id=did, article=art, entity_names=names,
                            citation_quotes=quotes, chat=chat, cfg=wiki, author_id=None,
                        ):
                            formatted += 1
                        await jobs.heartbeat(jid)
                        await jobs.append_log(jid, {"step": "format", "slug": art.slug})
                        await job_s.commit()
                        await _safe_publish(redis, jid, {"step": "format", "slug": art.slug})
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "formatted", "count": formatted})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": formatted}
                )
                return "succeeded"
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
```

- [ ] **Step 6: Add `start_format` to `MaintenanceService`**

Extend the queue import to include `enqueue_format` and add:

```python
    async def start_format(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "format")
        job = await self._repo.create(domain_id=domain_id, kind="format")
        await self._s.commit()
        await enqueue_format(None, job_id=job.id, domain_id=domain_id)
        return job
```

- [ ] **Step 7: Add the `/format` route**

In `src/paw/api/routers/maintenance.py`:

```python
@router.post(
    "/domains/{domain_id}/format",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_format(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_format(domain_id=domain_id)
    return {"job_id": str(job.id)}
```

- [ ] **Step 8: Register `format_articles` in `src/paw/worker.py`**

Extend the import and `functions` list to include `format_articles`.

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py -v`
Expected: PASS.

- [ ] **Step 10: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/jobs/queue.py src/paw/jobs/tasks.py src/paw/worker.py src/paw/services/maintenance.py src/paw/api/routers/maintenance.py src/paw/db/repos/articles.py tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py
git commit -m "feat(format): domain-locked format job + POST /domains/{id}/format"
```

---

## Task 10: Reindex core — batch planner + stale-chunk reader

**Files:**
- Create: `src/paw/vector/reindex.py`
- Modify: `src/paw/db/repos/chunks.py`
- Test: `tests/unit/test_reindex_planner.py`, `tests/integration/test_reindex.py`

**Interfaces:**
- Produces (`src/paw/vector/reindex.py`):
  - `plan_batches(total: int, batch_size: int) -> list[int]` — pure; `[]` for `total <= 0`; raises `ValueError` for `batch_size <= 0`; otherwise full batches of `batch_size` plus a final remainder (e.g. `(250, 100) -> [100, 100, 50]`).
  - `async def reindex_domain_chunks(session, *, domain_id, target_version: int, embedder, batch_size: int, on_batch=None) -> int` — drains chunks whose `embedding_version != target_version`: counts them, plans batches, and per batch fetches the next `batch_size` stale chunks, re-embeds their text, and `set_embedding(..., embedding_version=target_version)`; returns the number re-embedded. `on_batch(done: int, total: int)` is awaited after each batch when provided.
- Produces (`src/paw/db/repos/chunks.py`):
  - `ChunkRepo.count_stale(*, domain_id, target_version) -> int`.
  - `ChunkRepo.fetch_stale_batch(*, domain_id, target_version, limit) -> list[tuple[uuid.UUID, str]]` — `(chunk_id, text)` ordered by id, `LIMIT limit`.

This task needs Docker (integration layer for the second test file).

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_reindex_planner.py`:

```python
import pytest

from paw.vector.reindex import plan_batches


def test_plan_batches_splits_with_remainder():
    assert plan_batches(250, 100) == [100, 100, 50]


def test_plan_batches_exact_multiple():
    assert plan_batches(200, 100) == [100, 100]


def test_plan_batches_empty_for_zero_or_negative_total():
    assert plan_batches(0, 100) == []
    assert plan_batches(-5, 100) == []


def test_plan_batches_rejects_bad_batch_size():
    with pytest.raises(ValueError):
        plan_batches(10, 0)
```

- [ ] **Step 2: Run unit test to verify it fails**

Run: `uv run pytest tests/unit/test_reindex_planner.py -v`
Expected: FAIL — module `paw.vector.reindex` missing.

- [ ] **Step 3: Add the stale readers to `src/paw/db/repos/chunks.py`**

Add to `ChunkRepo`:

```python
    async def count_stale(self, *, domain_id: uuid.UUID, target_version: int) -> int:
        res = await self._s.execute(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE domain_id = :d AND embedding_version != :v"
            ),
            {"d": str(domain_id), "v": target_version},
        )
        return int(res.scalar_one())

    async def fetch_stale_batch(
        self, *, domain_id: uuid.UUID, target_version: int, limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        res = await self._s.execute(
            text(
                "SELECT id, text FROM chunks "
                "WHERE domain_id = :d AND embedding_version != :v "
                "ORDER BY id LIMIT :k"
            ),
            {"d": str(domain_id), "v": target_version, "k": limit},
        )
        return [(uuid.UUID(str(r[0])), r[1]) for r in res.all()]
```

- [ ] **Step 4: Create `src/paw/vector/reindex.py`**

```python
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.chunks import ChunkRepo
from paw.providers.base import EmbeddingProvider

OnBatch = Callable[[int, int], Awaitable[None]]


def plan_batches(total: int, batch_size: int) -> list[int]:
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size!r}")
    if total <= 0:
        return []
    full, remainder = divmod(total, batch_size)
    sizes = [batch_size] * full
    if remainder:
        sizes.append(remainder)
    return sizes


async def reindex_domain_chunks(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    target_version: int,
    embedder: EmbeddingProvider,
    batch_size: int,
    on_batch: OnBatch | None = None,
) -> int:
    repo = ChunkRepo(session)
    total = await repo.count_stale(domain_id=domain_id, target_version=target_version)
    done = 0
    for _ in plan_batches(total, batch_size):
        batch = await repo.fetch_stale_batch(
            domain_id=domain_id, target_version=target_version, limit=batch_size
        )
        if not batch:
            break
        vectors = await embedder.embed([txt for _, txt in batch])
        for (cid, _txt), vec in zip(batch, vectors, strict=True):
            await repo.set_embedding(
                chunk_id=cid, vector=vec, embedding_version=target_version
            )
        done += len(batch)
        if on_batch is not None:
            await on_batch(done, total)
    return done
```

- [ ] **Step 5: Write the failing integration test**

Create `tests/integration/test_reindex.py`:

```python
from sqlalchemy import text
from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig
from paw.vector.embed import embed_and_write
from paw.vector.reindex import reindex_domain_chunks
from paw.vector.search import hybrid_search


async def _seed(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:1", summary="sum"
    )
    await ensure_embedding_column(db_session, dim)
    specs = [
        ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP summary"),
        ChunkSpec(kind="section", ord=1, heading_path="Reliability", text="TCP is reliable"),
    ]
    ids = await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id, specs=specs,
        embedder=StubEmbeddingProvider(dim=dim), embedding_version=1,
    )
    await db_session.commit()
    return dom, art, ids


async def test_reindex_flips_version_and_search_follows_it(db_session):
    dom, art, ids = await _seed(db_session)
    # simulate a model change: target version is now 2; all chunks are stale (v1)
    n = await reindex_domain_chunks(
        db_session, domain_id=dom.id, target_version=2,
        embedder=StubEmbeddingProvider(dim=8), batch_size=1,
    )
    await db_session.commit()
    assert n == len(ids)

    rows = await db_session.execute(
        text("SELECT DISTINCT embedding_version FROM chunks WHERE domain_id = :d"),
        {"d": str(dom.id)},
    )
    assert [r[0] for r in rows.all()] == [2]  # everything flipped to current

    cfg = RetrievalConfig(k1=10, k2=10, top_n=5)
    qvec = StubEmbeddingProvider(dim=8)._vec("reliable")
    # search at the new version returns the reindexed chunk...
    new_hits = await hybrid_search(
        db_session, domain_id=dom.id, query="reliable", query_vector=qvec,
        cfg=cfg, embedding_version=2,
    )
    assert any(h.chunk_id in ids for h in new_hits)
    # ...and the old version's vector arm ignores them
    old_hits = await hybrid_search(
        db_session, domain_id=dom.id, query="zzzznomatch", query_vector=qvec,
        cfg=cfg, embedding_version=1,
    )
    assert not {h.chunk_id for h in old_hits} & set(ids)


async def test_reindex_is_noop_when_nothing_stale(db_session):
    dom, art, ids = await _seed(db_session)
    n = await reindex_domain_chunks(
        db_session, domain_id=dom.id, target_version=1,  # already current
        embedder=StubEmbeddingProvider(dim=8), batch_size=10,
    )
    assert n == 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_reindex_planner.py tests/integration/test_reindex.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/vector/reindex.py src/paw/db/repos/chunks.py tests/unit/test_reindex_planner.py tests/integration/test_reindex.py
git commit -m "feat(reindex): batch planner + stale-chunk re-embed to a target version"
```

---

## Task 11: Settings-backed embedding version wired into search

**Files:**
- Modify: `src/paw/services/query.py`, `src/paw/services/chat.py`, `src/paw/services/provider_settings.py`
- Test: `tests/integration/test_embedding_version.py`

**Interfaces:**
- Consumes: `ProviderSettingsService.get_embedding_version`, `bump_embedding_version` (Task 1).
- Produces:
  - `QueryService.prepare` and `ChatService.prepare_turn` pass `embedding_version=await psvc.get_embedding_version()` to `retrieve` (replacing the hard-coded `CURRENT_EMBEDDING_VERSION`).
  - `ProviderSettingsService.update_provider` calls `bump_embedding_version()` in the dim-change branch (after `rebuild_embedding_column`, before commit), so post-change chunks are excluded from search until reindexed.

This task needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_embedding_version.py`:

```python
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def test_get_and_bump_embedding_version(db_session, wired_settings):
    from paw.config import get_settings

    box = SecretBox(get_settings().fernet_key)
    svc = ProviderSettingsService(db_session, box=box)
    assert await svc.get_embedding_version() == 1  # default
    nxt = await svc.bump_embedding_version()
    await db_session.commit()
    assert nxt == 2
    assert await svc.get_embedding_version() == 2


async def test_update_provider_dim_change_bumps_version(db_session, wired_settings):
    from paw.config import get_settings

    box = SecretBox(get_settings().fernet_key)
    svc = ProviderSettingsService(db_session, box=box)
    # establish a provider at dim 8 (creates the embedding column at 8)
    await svc.update_provider(
        base_url="http://x", chat_model="c", embedding_model="e",
        embedding_dim=8, api_key="k",
    )
    assert await svc.get_embedding_version() == 1
    # change the dim -> rebuild + version bump
    await svc.update_provider(
        base_url="http://x", chat_model="c", embedding_model="e",
        embedding_dim=16, api_key="k",
    )
    assert await svc.get_embedding_version() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_embedding_version.py -v`
Expected: FAIL — `update_provider` does not yet bump the version (second assertion fails).

- [ ] **Step 3: Bump the version on dim change in `update_provider`**

In `src/paw/services/provider_settings.py::update_provider`, change the dim-change branch to bump after the rebuild:

```python
        if current is not None and current != embedding_dim:
            await rebuild_embedding_column(self._s, embedding_dim)
            await self.bump_embedding_version()
        else:
            await ensure_embedding_column(self._s, embedding_dim)
        await self._s.commit()
        return pc
```

- [ ] **Step 4: Read the configured version in `QueryService.prepare`**

In `src/paw/services/query.py`, replace the `embedding_version=CURRENT_EMBEDDING_VERSION` argument to `retrieve` with:

```python
            embedding_version=await psvc.get_embedding_version(),
```

The `CURRENT_EMBEDDING_VERSION` import is now unused — remove the line `from paw.vector.search import CURRENT_EMBEDDING_VERSION`.

- [ ] **Step 5: Read the configured version in `ChatService.prepare_turn`**

In `src/paw/services/chat.py`, replace `embedding_version=CURRENT_EMBEDDING_VERSION` in the `retrieve` call with:

```python
            embedding_version=await psvc.get_embedding_version(),
```

Remove the now-unused `from paw.vector.search import CURRENT_EMBEDDING_VERSION` import.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_embedding_version.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the query/chat/settings regressions**

Run: `uv run pytest tests/integration/test_query_service.py tests/integration/test_chat_service.py tests/integration/test_provider_settings.py tests/integration/test_managed_migration.py tests/api/test_settings_provider.py -v`
Expected: PASS (the version defaults to 1, so existing single-version corpora are unaffected).

- [ ] **Step 8: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/services/query.py src/paw/services/chat.py src/paw/services/provider_settings.py tests/integration/test_embedding_version.py
git commit -m "feat(vector): search follows the settings-backed embedding version; bump on dim change"
```

---

## Task 12: Reindex job + queue + service + API

**Files:**
- Modify: `src/paw/jobs/queue.py`, `src/paw/jobs/tasks.py`, `src/paw/worker.py`, `src/paw/services/maintenance.py`, `src/paw/api/routers/maintenance.py`
- Test: `tests/integration/test_maintenance_tasks.py` (extend), `tests/api/test_maintenance_api.py` (extend)

**Interfaces:**
- Consumes: `reindex_domain_chunks`, `_build_providers`, `ProviderSettingsService.get_embedding_version`, `ensure_embedding_column`, `model_lock`.
- Produces:
  - `enqueue_reindex(redis=None, *, job_id, domain_id) -> None`.
  - `reindex_domain(ctx, job_id, domain_id) -> str` — under `domain_lock` + `model_lock`; ensures the embedding column exists at the provider dim, reads the configured current version, drains stale chunks via `reindex_domain_chunks` with a per-batch progress callback (cancel-checked), commits once; logs `{"step":"reindexed","count":n}`.
  - `MaintenanceService.start_reindex(*, domain_id) -> Job`.
  - Router `POST /domains/{domain_id}/reindex` → `{"job_id": str}`.

This task needs Docker (integration + api layers).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_maintenance_tasks.py`:

```python
async def test_reindex_task_flips_stale_chunks(db_session, redis_client, wired_settings, monkeypatch):
    from sqlalchemy import text

    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.config import get_settings
    from paw.db.managed import ensure_embedding_column
    from paw.db.repos.articles import ArticleRepo
    from paw.ingest.chunking import ChunkSpec
    from paw.providers.config import WikiConfig
    from paw.security.secrets import SecretBox
    from paw.services.provider_settings import ProviderSettingsService
    from paw.vector.embed import embed_and_write

    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:1", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP")],
        embedder=StubEmbeddingProvider(dim=8), embedding_version=1,
    )
    # bump current version to 2 -> the v1 chunk is now stale
    box = SecretBox(get_settings().fernet_key)
    await ProviderSettingsService(db_session, box=box).bump_embedding_version()
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="reindex")
    await db_session.commit()

    async def fake_build(session, box):
        return StubChatProvider([]), StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.reindex_domain({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    rows = await db_session.execute(
        text("SELECT DISTINCT embedding_version FROM chunks WHERE domain_id = :d"),
        {"d": str(dom.id)},
    )
    assert [r[0] for r in rows.all()] == [2]
```

Append to `tests/api/test_maintenance_api.py`:

```python
async def test_reindex_endpoint_returns_job_id(ctx):
    c, csrf, dom = ctx
    r = await c.post(f"/api/v1/domains/{dom}/reindex", headers={"x-csrf-token": csrf})
    assert r.status_code == 202
    assert uuid.UUID(r.json()["job_id"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py::test_reindex_task_flips_stale_chunks tests/api/test_maintenance_api.py::test_reindex_endpoint_returns_job_id -v`
Expected: FAIL — `reindex_domain` / `/reindex` route missing.

- [ ] **Step 3: Add `enqueue_reindex` to `src/paw/jobs/queue.py`**

```python
async def enqueue_reindex(
    redis: Any | None = None, *, job_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    pool = redis or await get_arq_pool()
    await pool.enqueue_job("reindex_domain", str(job_id), str(domain_id))
```

- [ ] **Step 4: Add `reindex_domain` to `src/paw/jobs/tasks.py`**

```python
async def reindex_domain(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from paw.db.managed import ensure_embedding_column
    from paw.services.provider_settings import ProviderSettingsService
    from paw.vector.reindex import reindex_domain_chunks

    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()

            async def on_batch(done: int, total: int) -> None:
                if await jobs.is_cancel_requested(jid):
                    raise MaintenanceCancelled()
                await jobs.heartbeat(jid)
                await jobs.append_log(jid, {"step": "batch", "done": done, "total": total})
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "batch", "done": done, "total": total})

            try:
                chat, embedder, _wiki, dim = await _build_providers(data_s, box)
                psvc = ProviderSettingsService(data_s, box=box)
                target = await psvc.get_embedding_version()
                mcfg = await psvc.get_maintenance()
                await ensure_embedding_column(data_s, dim)
                async with model_lock(redis, getattr(chat, "chat_model", "default")):
                    count = await reindex_domain_chunks(
                        data_s, domain_id=did, target_version=target,
                        embedder=embedder, batch_size=mcfg.reindex_batch_size,
                        on_batch=on_batch,
                    )
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "reindexed", "count": count})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": count}
                )
                return "succeeded"
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
```

- [ ] **Step 5: Add `start_reindex` to `MaintenanceService`**

Extend the queue import to include `enqueue_reindex` and add:

```python
    async def start_reindex(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "reindex")
        job = await self._repo.create(domain_id=domain_id, kind="reindex")
        await self._s.commit()
        await enqueue_reindex(None, job_id=job.id, domain_id=domain_id)
        return job
```

- [ ] **Step 6: Add the `/reindex` route**

In `src/paw/api/routers/maintenance.py`:

```python
@router.post(
    "/domains/{domain_id}/reindex",
    status_code=202,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def start_reindex(
    domain_id: uuid.UUID, session: AsyncSession = Depends(db)
) -> dict[str, str]:
    job = await MaintenanceService(session).start_reindex(domain_id=domain_id)
    return {"job_id": str(job.id)}
```

- [ ] **Step 7: Register `reindex_domain` in `src/paw/worker.py`**

Extend the import and `functions` list to include `reindex_domain`. The final list reads:

```python
    functions = [
        heartbeat,
        ingest_domain,
        gc_housekeeping,
        lint_domain,
        fix_issues,
        format_articles,
        reindex_domain,
    ]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py -v`
Expected: PASS (all maintenance task + api tests).

- [ ] **Step 9: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/jobs/queue.py src/paw/jobs/tasks.py src/paw/worker.py src/paw/services/maintenance.py src/paw/api/routers/maintenance.py tests/integration/test_maintenance_tasks.py tests/api/test_maintenance_api.py
git commit -m "feat(reindex): domain-locked reindex job + POST /domains/{id}/reindex"
```

---

## Task 13: Web UI — domain actions, Lint results, Fix selection

**Files:**
- Modify: `src/paw/api/web/routes.py`, `src/paw/api/web/templates/domain.html`
- Create: `src/paw/api/web/templates/_lint_results.html`
- Test: `tests/api/test_maintenance_web.py`

**Interfaces:**
- Consumes: `MaintenanceService`, `JobRepo`, `CSRF_COOKIE`, `_current_uid`, the existing `_job_drawer.html`.
- Produces:
  - Web `POST /domains/{id}/lint` → starts the lint job, returns the `_job_drawer.html` partial (SSE-wired, like ingest).
  - Web `POST /domains/{id}/format` and `POST /domains/{id}/reindex` → same drawer partial.
  - Web `GET /domains/{id}/lint/{job_id}/results` → renders `_lint_results.html` from the finished job's `{"step":"issues"}` log entry: a checkbox per issue + a Fix form that posts the checked `issue_ids`.
  - Web `POST /domains/{id}/fix` (form field `issue_ids` repeated) → starts the fix job, returns the drawer partial.

This task needs Docker (api layer).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_maintenance_web.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.jobs import JobRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def ctx(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        csrf = c.cookies.get("paw_csrf")
        dom = (
            await c.post("/api/v1/domains", json={"name": "net"}, headers={"x-csrf-token": csrf})
        ).json()
        yield c, csrf, dom["id"], db_session


async def test_domain_page_has_maintenance_actions(ctx):
    c, csrf, dom, _ = ctx
    html = (await c.get(f"/domains/{dom}")).text
    assert f"/domains/{dom}/lint" in html
    assert f"/domains/{dom}/format" in html
    assert f"/domains/{dom}/reindex" in html


async def test_web_lint_returns_job_drawer(ctx):
    c, csrf, dom, _ = ctx
    r = await c.post(f"/domains/{dom}/lint", data={}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert "sse-connect" in r.text  # the job drawer partial


async def test_lint_results_view_lists_issues_with_fix_form(ctx):
    c, csrf, dom, db_session = ctx
    # craft a finished lint job carrying one issue in its log
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=uuid.UUID(dom), kind="lint")
    await repo.append_log(
        job.id,
        {
            "step": "issues",
            "issues": [
                {
                    "id": "deadbeefdeadbeef",
                    "kind": "broken_ref",
                    "target_slug": "intro",
                    "detail": "broken wikilink [[ghost]]",
                    "fix": "remove it",
                }
            ],
        },
    )
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()

    html = (await c.get(f"/domains/{dom}/lint/{job.id}/results")).text
    assert "deadbeefdeadbeef" in html
    assert "broken_ref" in html
    assert f"/domains/{dom}/fix" in html  # the Fix form action
    assert 'name="issue_ids"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_maintenance_web.py -v`
Expected: FAIL — the web routes / template do not exist.

- [ ] **Step 3: Add the maintenance actions to `src/paw/api/web/templates/domain.html`**

Replace the `<div class="content-header"> … </div>` block with one that adds the three maintenance actions:

```html
<div class="content-header">
  <form hx-post="/domains/{{ domain.id }}/ingest"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <input type="hidden" name="source_id" value="{{ latest_source_id | default('') }}">
    <button type="submit" {% if not latest_source_id %}disabled{% endif %}>Ingest latest source</button>
  </form>
  <a class="btn" href="/domains/{{ domain.id }}/query">🔍 Query</a>
  <a class="btn" href="/domains/{{ domain.id }}/graph">🕸 Graph</a>
  <form hx-post="/domains/{{ domain.id }}/lint"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <button type="submit">🧹 Lint</button>
  </form>
  <form hx-post="/domains/{{ domain.id }}/format"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <button type="submit">✨ Format</button>
  </form>
  <form hx-post="/domains/{{ domain.id }}/reindex"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <button type="submit">♻ Reindex</button>
  </form>
</div>
```

- [ ] **Step 4: Create `src/paw/api/web/templates/_lint_results.html`**

```html
<form hx-post="/domains/{{ domain_id }}/fix"
      hx-headers='{"x-csrf-token": "{{ csrf }}"}'
      hx-target="#job-drawer" hx-swap="innerHTML">
  <ul class="lint-issues">
    {% for issue in issues %}
    <li>
      <label>
        <input type="checkbox" name="issue_ids" value="{{ issue.id }}"
               {% if issue.kind == 'duplicate_entity' %}disabled{% endif %}>
        <strong>{{ issue.kind }}</strong>
        {% if issue.target_slug %}<code>{{ issue.target_slug }}</code>{% endif %}
        — {{ issue.detail }}
      </label>
    </li>
    {% else %}
    <li>No issues found.</li>
    {% endfor %}
  </ul>
  {% if issues %}<button type="submit">Fix selected</button>{% endif %}
</form>
```

- [ ] **Step 5: Add the web routes to `src/paw/api/web/routes.py`**

`Form` is already imported. Add `from paw.db.repos.jobs import JobRepo` and `from paw.services.maintenance import MaintenanceService` with the other imports. Then append these routes (after `web_start_ingest`):

```python
async def _web_start_maintenance(
    domain_id: uuid.UUID, request: Request, session: AsyncSession, op: str
) -> Response:
    svc = MaintenanceService(session)
    starter = {
        "lint": svc.start_lint,
        "format": svc.start_format,
        "reindex": svc.start_reindex,
    }[op]
    job = await starter(domain_id=domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "_job_drawer.html", {"job_id": job.id, "csrf": csrf})


@router.post("/domains/{domain_id}/lint", response_class=HTMLResponse)
async def web_lint(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "lint")


@router.post("/domains/{domain_id}/format", response_class=HTMLResponse)
async def web_format(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "format")


@router.post("/domains/{domain_id}/reindex", response_class=HTMLResponse)
async def web_reindex(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    return await _web_start_maintenance(domain_id, request, session, "reindex")


@router.get("/domains/{domain_id}/lint/{job_id}/results", response_class=HTMLResponse)
async def web_lint_results(
    domain_id: uuid.UUID,
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    job = await JobRepo(session).get(job_id)
    issues: list[dict[str, object]] = []
    if job is not None:
        for entry in job.log:
            if entry.get("step") == "issues":
                issues = entry.get("issues", [])  # type: ignore[assignment]
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "_lint_results.html",
        {"domain_id": domain_id, "issues": issues, "csrf": csrf},
    )


@router.post("/domains/{domain_id}/fix", response_class=HTMLResponse)
async def web_fix(
    domain_id: uuid.UUID,
    request: Request,
    issue_ids: list[str] = Form(default=[]),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    job = await MaintenanceService(session).start_fix(domain_id=domain_id, issue_ids=issue_ids)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(request, "_job_drawer.html", {"job_id": job.id, "csrf": csrf})
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_maintenance_web.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the web-pages regression**

Run: `uv run pytest tests/api/test_web_pages.py -v`
Expected: PASS (the domain page still renders for existing assertions).

- [ ] **Step 8: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/domain.html src/paw/api/web/templates/_lint_results.html tests/api/test_maintenance_web.py
git commit -m "feat(web): domain Lint/Format/Reindex actions + lint-results Fix form"
```

---

## Task 14: E2E — plant → lint → fix → lint clean

**Files:**
- Test: `tests/e2e/test_maintenance_e2e.py`

**Interfaces:**
- Consumes: the full stack — the `lint`/`fix` ops + the `fix_issues` task run inline (driven directly, as the other e2e tests do), a stub provider injected via `monkeypatch.setattr(tasks_mod, "_build_providers", ...)`, real Postgres + Redis.

This task needs Docker (e2e layer).

- [ ] **Step 1: Write the end-to-end test**

Create `tests/e2e/test_maintenance_e2e.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.lint import run_lint
from paw.providers.config import MaintenanceConfig, WikiConfig
from paw.services.ingest_write import upsert_article


async def test_plant_lint_fix_lint_clean(db_session, redis_client, wired_settings, monkeypatch):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    # plant a broken [[ref]]
    await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="Welcome. See [[ghost]].", summary="", author_id=None,
    )
    await db_session.commit()

    # 1) Lint reports the broken ref (run the deterministic op directly)
    issues = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    broken = next(i for i in issues if i.kind == "broken_ref")

    # 2) Fix the selected issue via the job (stub LLM removes the broken link)
    async def fake_build(session, box):
        chat = StubChatProvider(
            [StubChatProvider.tool(
                "emit_result", {"markdown": "Welcome to the overview.", "summary": ""}
            )]
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    fix_job = await JobRepo(db_session).create(domain_id=dom.id, kind="fix")
    await db_session.commit()
    out = await tasks_mod.fix_issues(
        {"redis": redis_client}, str(fix_job.id), str(dom.id), [broken.id]
    )
    assert out == "succeeded"

    # 3) Re-run Lint — the broken ref is gone
    after = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    assert broken.id not in {i.id for i in after}
```

- [ ] **Step 2: Run the e2e test to verify it passes**

Run: `uv run pytest tests/e2e/test_maintenance_e2e.py -v`
Expected: PASS (1 test). (Implementation already exists from Tasks 4–7; this test only wires the full flow.)

- [ ] **Step 3: Run the whole suite**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: PASS (all layers green).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_maintenance_e2e.py
git commit -m "test(e2e): plant -> lint -> fix -> lint-clean round-trip"
```

---

## Acceptance criteria → task map

| Spec acceptance criterion | Covered by |
| --- | --- |
| 1. Lint reports broken-ref / orphan / stale / duplicate-entity, writes nothing | Tasks 3–5 (`tests/integration/test_lint_op.py` asserts all four kinds + read-only) |
| 2. Fix writes ai revisions that resolve issues; re-lint shows them gone | Tasks 6–7, 14 (`test_fix_op`, `test_fix_task_resolves_selected_issue`, e2e) |
| 3. Format revision preserves entities/citations but changes formatting | Task 8 (`test_format_invariant`, `test_format_op`) |
| 4. Reindex re-embeds to a new version; search follows it, ignores old | Tasks 10–12 (`test_reindex`, `test_embedding_version`, `test_reindex_task_flips_stale_chunks`) |
| 5. All four run as domain-locked jobs with streaming progress + cooperative cancel | Tasks 5, 7, 9, 12 (each task: `domain_lock`, `_safe_publish` SSE events, `is_cancel_requested` checks) |

## Out of scope (per spec)

- Query-cache stale-marking is the **no-op seam** only (Task 2); the `query_cache` table + invalidation land in Phase 7.
- Scheduled cron lint/reindex/GC (backlog) and MCP (Phase 8) are not built.
- Fix of `duplicate_entity` (entity merge) is deferred (Scope decision 3).
- Fix/Format do not re-chunk/re-embed; refreshing search is a separate Reindex run (Scope decision 4).
