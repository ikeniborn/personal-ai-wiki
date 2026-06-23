---
title: "Phase 5 — Graph + editing (link-aware UI) Implementation Plan"
phase: 5
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-5-graph-editing-design.md
review:
  plan_hash: 9efea1ecefa9e67b
  spec_hash: 22bb325bfbd2b06a
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
      section: "Scope decisions (read first)"
      section_hash: dc19ba2c8850f6be
      text: "Task 1 adds index ix_links_dst_article_id, a deliberate deviation from the spec's 'No schema changes' (Data model touched). The plan surfaces and justifies this (Scope decision 1): the spec's own 'Data model touched' names a links(dst_article_id) backlink index, and the change adds no table/column so 'No new tables' still holds. Intentional and documented — informational only."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Kept as designed (Scope decision 1). The index is named in the spec's 'Data model touched' and backs the new backlinks query (WHERE dst_article_id = ?); it adds no table/column, so 'No new tables' holds. Removing it would degrade the core article-page read the spec mandates."
    - id: F-002
      phase: coverage
      severity: INFO
      section: "Scope decisions (read first)"
      section_hash: dc19ba2c8850f6be
      text: "The spec's 'Data model touched' lists `entities` as a table read, but no plan task surfaces or displays entities (mirrors spec finding F-001). Plan Scope decision 8 explicitly keeps `entities` an internal Phase-2 co-occurrence input, not a Phase-5 feature. Omission is intentional and documented — informational only."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Kept as designed (Scope decision 8). `entities` is read only as the existing Phase-2 co-occurrence input that produces 'related' links; no Phase-5 acceptance criterion surfaces entities, so displaying them would be scope creep beyond the spec."
---

# Phase 5 — Graph + editing (link-aware UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Navigate the wiki by its link structure — a full-canvas Cytoscape graph shows the depth-bounded, type-filtered subgraph around any article (node click → slide-in drawer → open); the article page exposes real citations, backlinks, related/parent/child links, `[[refs]]` as in-wiki links, and a revision list with rollback; the secondary sidebar becomes a parent/child tree with a filter box.

**Architecture:** Pure builders (`graph/subgraph.py`, `graph/tree.py`) do the depth/type-filtered BFS and parent/child forest in Python (cycle-safe, unit-testable); thin repos fetch domain links/articles and feed them. `GraphRepo.subgraph()` composes the induced subgraph; `LinkRepo` serves per-article backlinks/outgoing and domain parent/child links; `CitationRepo.list_for_article()` outer-joins sources. A read-only `GraphService` resolves `GraphConfig` (global ⊕ per-domain) and clamps depth before calling the repo; a thin `GET /api/v1/graph` returns nodes + typed edges as JSON for the vendored Cytoscape canvas driven by an external, CSP-safe `graph.js`. The article page render path resolves `[[slug]]` wikilinks to `/articles/{id}` before `render_markdown`; rollback reuses the Phase 1 `ArticleService.rollback` via a web route that returns `HX-Refresh`. No new tables; one tiny migration adds the backlink index named in the spec.

**Tech Stack:** Python 3.12 · async SQLAlchemy 2.0 · PostgreSQL 16 · FastAPI · Jinja2 + HTMX · Cytoscape.js (vendored, no CDN) · pytest + testcontainers + stub-LLM.

## Global Constraints

- **Branch:** work on a `dev/paw-phase-5` branch cut from up-to-date `master`; never commit to `master`; close via PR (CLAUDE.md branch workflow).
- **Dependency tool:** `uv` only — never call `pip`/`pytest` directly; always `uv run …`.
- **CI gates (all must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- **Atomicity:** the service layer issues `session.commit()`. Repos and storage NEVER commit. Phase 5 reads are commit-free; the only writes (rollback) reuse the existing committing `ArticleService.rollback`.
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457). `IntegrityError` auto-maps to 409.
- **Async everywhere:** all DB/IO is async; tests are plain `async def` (`asyncio_mode = auto`).
- **Security:** Redis-backed sessions; `require_role(*roles)` RBAC; CSRF double-submit (`require_csrf`) on non-GET (GET graph endpoint is CSRF-exempt); rendered article + node summaries stay sanitized; CSP is `script-src 'self'` — NO inline scripts, NO CDN (Cytoscape is vendored, graph init lives in an external file, node summaries are injected via `textContent`, never `innerHTML`).
- **No new tables** (spec). The single migration in Task 1 adds one index only.
- **Docs:** this project has **no `docs/wiki/`** — skip the iwiki ingest/lint step (global rule).

---

## Scope decisions (read first)

1. **Backlink index is added (small deviation from the spec's "No schema changes").** The spec's *Data model touched* names a `links(dst_article_id)` backlink index "from Phase 2", but Phase 2's migration `0002` created only `ix_links_domain_id`. Backlinks (`WHERE dst_article_id = ?`) are the core new article-page query. Task 1 adds `ix_links_dst_article_id` via migration `0004`. This adds **no table and no column** — "No new tables" still holds — and aligns the schema with the index the spec assumes. Surfaced here because it technically touches DDL.

2. **All link readers are GENERIC over `links.type`.** Phase 2's ingest only ever writes `type="related"` links (Stage D co-occurrence). The graph filter, the article "related/parent/child" sections, and the sidebar tree are written to handle any link type present in the data; tests seed typed links (`parent`/`child`/`references`/`depends_on`) directly. With real Phase-2 data the tree renders flat (no parent/child links exist yet) — the builder renders defensively (every node a root). No producer for typed links is added in this phase.

3. **Citations render defensively.** Phase 2 writes citations with `source_id=NULL`. The article page always shows quote/locator and shows a source filename only when `source_id` resolves. Tests seed a citation *with* a source to verify the outer join. (`citations` is the table that links an article to its `sources`; `references` is a distinct *link type* between two articles — they are unrelated, resolving spec finding F-003.)

4. **Rollback + optimistic-lock 409 already exist (Phase 1).** `ArticleService.rollback`, `ArticleService.update`'s 409, and `POST /api/v1/articles/{id}/rollback` are implemented. Phase 5 adds only the UI (a per-revision Rollback button → a web route returning `HX-Refresh`) and verifies acceptance criterion 5. No service/repo change to rollback logic.

5. **Graph is domain-scoped, entered from the domain/article pages.** There is no per-domain ACL (overview §8 defers it), so "domain access" = any authenticated user, matching the Query endpoint. The graph page is `GET /domains/{domain_id}/graph`; the domain page gets a "🕸 Graph" button and each article gets an "Open in graph" link. The global rail `🕸` icon (currently `href="#"`) is left untouched — wiring a domain-less global graph is out of scope.

6. **Graph interactivity is exercised at the API layer, not in a headless browser.** Like Phase 3/4 UI, the data path (`GET /api/v1/graph` payload) is fully tested; the page tests assert the canvas container, vendored script tags, and `data-*` wiring are present and that static assets serve. Live Cytoscape rendering is verified manually.

7. **Pure-Python subgraph BFS (not a recursive CTE).** The spec lists "subgraph builder (depth + type filter)" as a **unit** test, so the builder is a pure function over an in-memory edge list (cycle-safe, undirected, depth-bounded). For team-scale corpora, loading a domain's links (a few thousand 3-tuples) and BFS-ing in Python is cheap and far simpler than a bidirectional recursive CTE. The existing `graph/traverse.py::bfs_expand` (outgoing-only SQL CTE for retrieval) is intentionally left separate — different purpose.

8. **Spec INFO findings resolved by this plan.** Check-spec raised four advisory INFO items; this plan closes them: F-002 (`[[refs]]` had no acceptance criterion) → Task 8 builds + Task 10 tests the resolver; F-004 (no depth value/ceiling) → `GraphConfig.default_depth=2`, `max_depth=4`, clamped in `GraphService` (Tasks 2, 7); F-003 (references vs citations) → Scope decision 3; F-001 (entities a dangling read) → entities stay an internal co-occurrence input (Phase 2), not surfaced — no work needed.

## File Structure

**Create:**
- `alembic/versions/0004_phase5_backlink_index.py` — adds `ix_links_dst_article_id`.
- `src/paw/graph/subgraph.py` — pure `SubEdge`, `Subgraph`, `build_subgraph`.
- `src/paw/graph/tree.py` — pure `TreeNode`, `normalize_parent_child`, `build_tree`.
- `src/paw/db/repos/links.py` — `LinkedArticle`, `LinkRepo` (backlinks / outgoing / parent_child_raw).
- `src/paw/services/graph.py` — `GraphService`, `SubgraphPayload`.
- `src/paw/api/routers/graph.py` — `GET /graph`.
- `src/paw/api/web/templates/graph.html`, `src/paw/api/web/templates/_sidebar_tree.html`.
- `src/paw/api/web/static/graph.js`, `src/paw/api/web/static/cytoscape.min.js` (vendored binary asset).
- Tests: `tests/unit/test_graph_config.py`, `tests/unit/test_subgraph.py`, `tests/unit/test_tree.py`, `tests/unit/test_wikilinks.py`, `tests/integration/test_graph_subgraph.py`, `tests/integration/test_link_repo.py`, `tests/integration/test_citation_reads.py`, `tests/integration/test_article_meta.py`, `tests/api/test_graph_api.py`, `tests/api/test_graph_web.py`, `tests/api/test_article_meta_web.py`, `tests/e2e/test_graph_editing_e2e.py`.

**Modify:**
- `src/paw/providers/config.py` — add `GraphConfig` + `GRAPH_KEY`.
- `src/paw/services/provider_settings.py` — add `get_graph()`.
- `src/paw/graph/repo.py` — add `GraphNode` + `GraphRepo.subgraph()` + private fetch helpers.
- `src/paw/db/repos/citations.py` — add `CitationView` + `CitationRepo.list_for_article()`.
- `src/paw/db/repos/articles.py` — add `ArticleRepo.slug_id_map()`.
- `src/paw/security/sanitize.py` — add `resolve_wikilinks()`.
- `src/paw/services/articles.py` — add `ArticleMeta`, `ArticleService.get_meta()`, `domain_tree()`, `slug_map()`.
- `src/paw/api/routers/articles.py` — resolve `[[refs]]` in `get_article`'s rendered HTML.
- `src/paw/main.py` — register the `graph` router.
- `src/paw/api/web/routes.py` — graph page (`GET /domains/{id}/graph`), web rollback (`POST /articles/{id}/rollback`), enrich `article_page` (meta + tree + wikilinks), switch `domain_page` sidebar to the tree.
- `src/paw/api/web/templates/article.html` — metadata sections + tree sidebar + rollback buttons + graph link.
- `src/paw/api/web/templates/domain.html` — tree sidebar + "🕸 Graph" button.
- `src/paw/api/web/templates/base.html` — add a `{% block scripts %}` hook before `</body>`.
- `src/paw/api/web/static/app.js` — tree-filter handler.
- `src/paw/api/web/static/theme.css` — graph/tree/meta styles.

---

## Task 1: Backlink index migration

**Files:**
- Create: `alembic/versions/0004_phase5_backlink_index.py`
- Test: `tests/integration/test_graph_subgraph.py` (index-presence test added here; the file grows in Task 5)

**Interfaces:**
- Produces: migration revision `"0004_phase5_backlink_index"` (down_revision `"0003_phase4_chat"`), index `ix_links_dst_article_id` on `links(dst_article_id)`.

This task needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_graph_subgraph.py`:

```python
from sqlalchemy import text


async def test_backlink_index_exists(db_session):
    res = await db_session.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_links_dst_article_id'")
    )
    assert res.scalar_one_or_none() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_graph_subgraph.py -v`
Expected: FAIL — the index does not exist yet (the session-scoped `_migrate` applies `head`, which currently stops at `0003`).

- [ ] **Step 3: Create `alembic/versions/0004_phase5_backlink_index.py`**

```python
from alembic import op

revision = "0004_phase5_backlink_index"
down_revision = "0003_phase4_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_links_dst_article_id ON links(dst_article_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_links_dst_article_id")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_graph_subgraph.py -v`
Expected: PASS (1 test). The `_migrate` fixture re-applies `head` per session, now including `0004`.

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add alembic/versions/0004_phase5_backlink_index.py tests/integration/test_graph_subgraph.py
git commit -m "feat(db): backlink index links(dst_article_id) for Phase 5"
```

---

## Task 2: GraphConfig + get_graph()

**Files:**
- Modify: `src/paw/providers/config.py`
- Modify: `src/paw/services/provider_settings.py`
- Test: `tests/unit/test_graph_config.py`

**Interfaces:**
- Produces: `GRAPH_KEY="graph"`; `GraphConfig(default_depth:int=2, max_depth:int=4, link_types:list[str]=["related","parent","child","references","depends_on"])`; `ProviderSettingsService.get_graph() -> GraphConfig`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_graph_config.py`:

```python
from paw.providers.config import GRAPH_KEY, GraphConfig


def test_graph_config_defaults():
    cfg = GraphConfig()
    assert cfg.default_depth == 2
    assert cfg.max_depth == 4
    assert cfg.link_types == ["related", "parent", "child", "references", "depends_on"]
    assert GRAPH_KEY == "graph"


def test_graph_config_override_validates():
    cfg = GraphConfig.model_validate({"default_depth": 1, "link_types": ["related"]})
    assert cfg.default_depth == 1
    assert cfg.max_depth == 4  # untouched default
    assert cfg.link_types == ["related"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_graph_config.py -v`
Expected: FAIL — `cannot import name 'GRAPH_KEY' / 'GraphConfig'`.

- [ ] **Step 3: Add `GRAPH_KEY` + `GraphConfig` to `src/paw/providers/config.py`**

Add the key constant after `CHAT_KEY = "chat"`:

```python
GRAPH_KEY = "graph"
```

Append the model after `ChatConfig`:

```python
class GraphConfig(BaseModel):
    default_depth: int = 2  # graph-view neighbourhood depth (distinct from RetrievalConfig.bfs_depth)
    max_depth: int = 4  # hard ceiling the endpoint clamps requested depth to
    link_types: list[str] = Field(
        default_factory=lambda: ["related", "parent", "child", "references", "depends_on"]
    )
```

- [ ] **Step 4: Add `get_graph()` to `ProviderSettingsService`**

In `src/paw/services/provider_settings.py`, extend the `paw.providers.config` import to add `GRAPH_KEY` and `GraphConfig` (keep the list alphabetised):

```python
from paw.providers.config import (
    CHAT_KEY,
    GRAPH_KEY,
    PROVIDER_KEY,
    RETRIEVAL_KEY,
    WIKI_KEY,
    ChatConfig,
    GraphConfig,
    ProviderConfig,
    RetrievalConfig,
    WikiConfig,
)
```

Add the method (next to `get_chat`):

```python
    async def get_graph(self) -> GraphConfig:
        raw = (await self._all()).get(GRAPH_KEY)
        return GraphConfig.model_validate(raw) if raw else GraphConfig()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_graph_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/providers/config.py src/paw/services/provider_settings.py tests/unit/test_graph_config.py
git commit -m "feat(graph): GraphConfig + get_graph() config layer"
```

---

## Task 3: Pure subgraph builder

**Files:**
- Create: `src/paw/graph/subgraph.py`
- Test: `tests/unit/test_subgraph.py`

**Interfaces:**
- Produces:
  - `SubEdge(src: uuid.UUID, dst: uuid.UUID, type: str)` (frozen dataclass)
  - `Subgraph(node_ids: set[uuid.UUID], edges: list[SubEdge])` (frozen dataclass)
  - `build_subgraph(edges: list[SubEdge], root: uuid.UUID, depth: int, types: set[str] | None = None) -> Subgraph` — undirected, depth-bounded, cycle-safe BFS from `root`; filters `edges` to `types` first (`None` = all, empty set = none); returns the reachable node set (always includes `root`) and the induced edges (both endpoints in the set).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_subgraph.py`:

```python
import uuid

from paw.graph.subgraph import SubEdge, build_subgraph


def _ids(n):
    return [uuid.uuid4() for _ in range(n)]


def test_depth_bounds_reachable_nodes():
    a, b, c, d = _ids(4)
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related"), SubEdge(c, d, "related")]
    assert build_subgraph(edges, a, 1).node_ids == {a, b}
    assert build_subgraph(edges, a, 2).node_ids == {a, b, c}
    assert build_subgraph(edges, a, 3).node_ids == {a, b, c, d}


def test_depth_zero_is_root_only():
    a, b = _ids(2)
    sg = build_subgraph([SubEdge(a, b, "related")], a, 0)
    assert sg.node_ids == {a}
    assert sg.edges == []


def test_undirected_expansion_follows_incoming_edges():
    a, b = _ids(2)
    # edge points b -> a; rooted at a we must still reach b
    assert build_subgraph([SubEdge(b, a, "related")], a, 1).node_ids == {a, b}


def test_type_filter_removes_edges_and_unreachable_nodes():
    a, b, c = _ids(3)
    edges = [SubEdge(a, b, "related"), SubEdge(a, c, "parent")]
    sg = build_subgraph(edges, a, 2, types={"related"})
    assert sg.node_ids == {a, b}
    assert [e.type for e in sg.edges] == ["related"]


def test_empty_type_set_yields_root_only():
    a, b = _ids(2)
    sg = build_subgraph([SubEdge(a, b, "related")], a, 2, types=set())
    assert sg.node_ids == {a} and sg.edges == []


def test_cycle_is_safe():
    a, b, c = _ids(3)
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related"), SubEdge(c, a, "related")]
    sg = build_subgraph(edges, a, 5)
    assert sg.node_ids == {a, b, c}
    assert len(sg.edges) == 3


def test_induced_edges_only_between_included_nodes():
    a, b, c = _ids(3)
    # c is beyond depth 1; the b->c edge must be excluded
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related")]
    sg = build_subgraph(edges, a, 1)
    assert sg.node_ids == {a, b}
    assert sg.edges == [SubEdge(a, b, "related")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_subgraph.py -v`
Expected: FAIL — module `paw.graph.subgraph` does not exist.

- [ ] **Step 3: Create `src/paw/graph/subgraph.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class SubEdge:
    src: uuid.UUID
    dst: uuid.UUID
    type: str


@dataclass(frozen=True)
class Subgraph:
    node_ids: set[uuid.UUID]
    edges: list[SubEdge]


def build_subgraph(
    edges: list[SubEdge],
    root: uuid.UUID,
    depth: int,
    types: set[str] | None = None,
) -> Subgraph:
    """Undirected, depth-bounded, cycle-safe BFS from ``root`` over ``edges``.

    ``types`` filters edges before traversal (``None`` = all types, empty = none).
    Returns the reachable node set (always incl. root) and the induced edges
    (both endpoints reachable).
    """
    if types is not None:
        edges = [e for e in edges if e.type in types]

    adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
    for e in edges:
        adjacency.setdefault(e.src, []).append(e.dst)
        adjacency.setdefault(e.dst, []).append(e.src)

    visited: set[uuid.UUID] = {root}
    frontier: list[uuid.UUID] = [root]
    for _ in range(max(0, depth)):
        nxt: list[uuid.UUID] = []
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    nxt.append(neighbour)
        if not nxt:
            break
        frontier = nxt

    induced = [e for e in edges if e.src in visited and e.dst in visited]
    return Subgraph(node_ids=visited, edges=induced)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_subgraph.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/graph/subgraph.py tests/unit/test_subgraph.py
git commit -m "feat(graph): pure depth/type-filtered subgraph builder"
```

---

## Task 4: Pure parent/child tree builder

**Files:**
- Create: `src/paw/graph/tree.py`
- Test: `tests/unit/test_tree.py`

**Interfaces:**
- Produces:
  - `TreeNode(id: uuid.UUID, slug: str, title: str, children: list[TreeNode])` (mutable dataclass)
  - `normalize_parent_child(typed_edges: list[tuple[uuid.UUID, uuid.UUID, str]]) -> list[tuple[uuid.UUID, uuid.UUID]]` — maps `child` links `(src→dst)` to `(parent=src, child=dst)` and `parent` links `(src→dst)` to `(parent=dst, child=src)`; ignores other types; dedups preserving order.
  - `build_tree(nodes: list[tuple[uuid.UUID, str, str]], parent_child: list[tuple[uuid.UUID, uuid.UUID]]) -> list[TreeNode]` — `nodes` are `(id, slug, title)`; roots are nodes that are never a child; children sorted by title; cycle-safe; nodes trapped in cycles surface as extra roots so every node appears exactly once.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tree.py`:

```python
import uuid

from paw.graph.tree import build_tree, normalize_parent_child


def test_normalize_maps_both_directions_and_dedups():
    p, c = uuid.uuid4(), uuid.uuid4()
    edges = [
        (p, c, "child"),    # p is parent of c
        (c, p, "parent"),   # c's parent is p  -> same (p, c)
        (p, c, "related"),  # ignored
    ]
    assert normalize_parent_child(edges) == [(p, c)]


def test_build_tree_nests_children_sorted_by_title():
    root = (uuid.uuid4(), "root", "Root")
    b = (uuid.uuid4(), "beta", "Beta")
    a = (uuid.uuid4(), "alpha", "Alpha")
    nodes = [root, b, a]
    pc = [(root[0], b[0]), (root[0], a[0])]
    forest = build_tree(nodes, pc)
    assert [n.title for n in forest] == ["Root"]
    assert [c.title for c in forest[0].children] == ["Alpha", "Beta"]  # title-sorted


def test_multiple_roots_when_no_links():
    a = (uuid.uuid4(), "a", "Apple")
    b = (uuid.uuid4(), "b", "Banana")
    forest = build_tree([b, a], [])
    assert [n.title for n in forest] == ["Apple", "Banana"]  # roots sorted by title
    assert all(n.children == [] for n in forest)


def test_cycle_does_not_recurse_forever_and_keeps_all_nodes():
    a = (uuid.uuid4(), "a", "A")
    b = (uuid.uuid4(), "b", "B")
    # a -> b -> a : both are children, so neither is a "never a child" root
    forest = build_tree([a, b], [(a[0], b[0]), (b[0], a[0])])
    seen = set()

    def walk(node):
        assert node.id not in seen  # each node appears exactly once
        seen.add(node.id)
        for child in node.children:
            walk(child)

    for node in forest:
        walk(node)
    assert seen == {a[0], b[0]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tree.py -v`
Expected: FAIL — module `paw.graph.tree` does not exist.

- [ ] **Step 3: Create `src/paw/graph/tree.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class TreeNode:
    id: uuid.UUID
    slug: str
    title: str
    children: list["TreeNode"] = field(default_factory=list)


def normalize_parent_child(
    typed_edges: list[tuple[uuid.UUID, uuid.UUID, str]],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Fold ``parent``/``child`` typed links into ``(parent_id, child_id)`` pairs."""
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for src, dst, link_type in typed_edges:
        if link_type == "child":
            pairs.append((src, dst))
        elif link_type == "parent":
            pairs.append((dst, src))
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    deduped: list[tuple[uuid.UUID, uuid.UUID]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            deduped.append(pair)
    return deduped


def build_tree(
    nodes: list[tuple[uuid.UUID, str, str]],
    parent_child: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[TreeNode]:
    """Forest of ``TreeNode`` from ``(id, slug, title)`` nodes + ``(parent, child)`` edges."""
    by_id = {nid: TreeNode(id=nid, slug=slug, title=title) for nid, slug, title in nodes}
    order = [nid for nid, _, _ in nodes]

    children_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    child_ids: set[uuid.UUID] = set()
    for parent, child in parent_child:
        if parent in by_id and child in by_id and parent != child:
            children_of.setdefault(parent, []).append(child)
            child_ids.add(child)

    visited: set[uuid.UUID] = set()

    def attach(nid: uuid.UUID) -> TreeNode:
        visited.add(nid)
        node = by_id[nid]
        node.children = []
        for cid in sorted(set(children_of.get(nid, ())), key=lambda c: by_id[c].title):
            if cid not in visited:
                node.children.append(attach(cid))
        return node

    roots = [nid for nid in order if nid not in child_ids]
    forest = [attach(nid) for nid in sorted(roots, key=lambda r: by_id[r].title)]
    # Defensive: nodes trapped in cycles were never a root and never visited; surface them.
    for nid in order:
        if nid not in visited:
            forest.append(attach(nid))
    return forest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tree.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/graph/tree.py tests/unit/test_tree.py
git commit -m "feat(graph): pure cycle-safe parent/child tree builder"
```

---

## Task 5: GraphRepo.subgraph (DB-backed compose)

**Files:**
- Modify: `src/paw/graph/repo.py`
- Test: `tests/integration/test_graph_subgraph.py` (extends Task 1's file)

**Interfaces:**
- Consumes: `SubEdge`, `build_subgraph` (Task 3); `Link`, `Article` models.
- Produces:
  - `GraphNode(id: uuid.UUID, slug: str, title: str, summary: str | None)` (frozen dataclass, in `graph/repo.py`)
  - `GraphRepo.subgraph(*, domain_id: uuid.UUID, root_article_id: uuid.UUID, depth: int, types: list[str] | None) -> tuple[list[GraphNode], list[SubEdge]]` — fetches the domain's links, runs `build_subgraph` (with `types` as a set, `None` = all allowed), fetches `GraphNode` briefs for the reachable ids.

Needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_graph_subgraph.py`:

```python
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo


async def _domain_with_articles(db_session, n):
    dom = await DomainRepo(db_session).create(name="g", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    arts = []
    for i in range(n):
        arts.append(
            await repo.create(
                domain_id=dom.id,
                slug=f"a{i}",
                title=f"A{i}",
                storage_ref=f"blob:{i}",
                summary=f"summary {i}",
            )
        )
    return dom, arts


async def test_subgraph_returns_nodes_and_typed_edges(db_session):
    dom, arts = await _domain_with_articles(db_session, 3)
    graph = GraphRepo(db_session)
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[1].id, type="related"
    )
    await graph.link(
        domain_id=dom.id, src_article_id=arts[1].id, dst_article_id=arts[2].id, type="related"
    )
    await db_session.commit()

    nodes, edges = await graph.subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=1, types=None
    )
    assert {n.id for n in nodes} == {arts[0].id, arts[1].id}
    assert any(n.summary == "summary 0" for n in nodes)  # briefs carry the summary
    assert {(e.src, e.dst, e.type) for e in edges} == {(arts[0].id, arts[1].id, "related")}


async def test_subgraph_type_filter_excludes_other_types(db_session):
    dom, arts = await _domain_with_articles(db_session, 3)
    graph = GraphRepo(db_session)
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[1].id, type="related"
    )
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[2].id, type="parent"
    )
    await db_session.commit()

    nodes, edges = await graph.subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=2, types=["related"]
    )
    assert {n.id for n in nodes} == {arts[0].id, arts[1].id}  # parent edge + its node excluded
    assert [e.type for e in edges] == ["related"]


async def test_subgraph_isolated_root_returns_just_itself(db_session):
    dom, arts = await _domain_with_articles(db_session, 2)
    nodes, edges = await GraphRepo(db_session).subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=2, types=None
    )
    assert [n.id for n in nodes] == [arts[0].id]
    assert edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_graph_subgraph.py -v`
Expected: FAIL — `GraphRepo` has no `subgraph` / cannot import `GraphNode`.

- [ ] **Step 3: Extend `src/paw/graph/repo.py`**

Replace the whole file with (keeps `link` + `cooccurrence_targets`, adds `GraphNode` + fetch helpers + `subgraph`):

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, Link
from paw.db.repos.entities import EntityRepo
from paw.graph.subgraph import SubEdge, build_subgraph


@dataclass(frozen=True)
class GraphNode:
    id: uuid.UUID
    slug: str
    title: str
    summary: str | None


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

    async def _domain_edges(self, domain_id: uuid.UUID) -> list[SubEdge]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id, Link.type).where(
                Link.domain_id == domain_id
            )
        )
        return [SubEdge(src=r[0], dst=r[1], type=r[2]) for r in res.all()]

    async def _briefs(self, ids: set[uuid.UUID]) -> list[GraphNode]:
        if not ids:
            return []
        res = await self._s.execute(
            select(Article.id, Article.slug, Article.title, Article.summary)
            .where(Article.id.in_(ids))
            .order_by(Article.title)
        )
        return [GraphNode(id=r[0], slug=r[1], title=r[2], summary=r[3]) for r in res.all()]

    async def subgraph(
        self,
        *,
        domain_id: uuid.UUID,
        root_article_id: uuid.UUID,
        depth: int,
        types: list[str] | None,
    ) -> tuple[list[GraphNode], list[SubEdge]]:
        edges = await self._domain_edges(domain_id)
        sg = build_subgraph(
            edges, root_article_id, depth, set(types) if types is not None else None
        )
        nodes = await self._briefs(sg.node_ids)
        return nodes, sg.edges
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_graph_subgraph.py -v`
Expected: PASS (4 tests — index presence + 3 subgraph cases).

- [ ] **Step 5: Run the existing graph-repo regression suite**

Run: `uv run pytest tests/integration/test_graph_repo.py -v`
Expected: PASS (`link` idempotency / self-link + co-occurrence unchanged).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/graph/repo.py tests/integration/test_graph_subgraph.py
git commit -m "feat(graph): GraphRepo.subgraph composes induced nodes+edges"
```

---

## Task 6: Link + citation read queries

**Files:**
- Create: `src/paw/db/repos/links.py`
- Modify: `src/paw/db/repos/citations.py`
- Test: `tests/integration/test_link_repo.py`, `tests/integration/test_citation_reads.py`

**Interfaces:**
- Produces (`src/paw/db/repos/links.py`):
  - `LinkedArticle(link_type: str, article_id: uuid.UUID, slug: str, title: str)` (frozen dataclass)
  - `LinkRepo(session)` with:
    - `backlinks(article_id) -> list[LinkedArticle]` — links where `dst == article_id`; `LinkedArticle` describes the **src** (who links here); ordered `(type, title)`.
    - `outgoing(article_id) -> list[LinkedArticle]` — links where `src == article_id`; describes the **dst**; ordered `(type, title)`.
    - `parent_child_raw(domain_id) -> list[tuple[uuid.UUID, uuid.UUID, str]]` — `(src, dst, type)` for `type in ('parent','child')` in the domain.
- Produces (`src/paw/db/repos/citations.py`):
  - `CitationView(id, quote: str | None, locator: str | None, source_id: uuid.UUID | None, source_filename: str | None)` (frozen dataclass)
  - `CitationRepo.list_for_article(article_id) -> list[CitationView]` — outer-joins `sources`; ordered by `created_at`.

Needs Docker (integration layer).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_link_repo.py`:

```python
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.links import LinkRepo
from paw.graph.repo import GraphRepo


async def _three(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    a = await repo.create(domain_id=dom.id, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=dom.id, slug="b", title="Bravo", storage_ref="b:b")
    c = await repo.create(domain_id=dom.id, slug="c", title="Charlie", storage_ref="b:c")
    return dom, a, b, c


async def test_backlinks_are_reverse_of_outgoing(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=a.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=c.id, dst_article_id=a.id, type="references")
    await db_session.commit()

    links = LinkRepo(db_session)
    back = await links.backlinks(a.id)
    assert {(x.link_type, x.article_id) for x in back} == {
        ("references", c.id),
        ("related", b.id),
    }
    # reciprocity: a is the outgoing target's backlink
    out_b = await links.outgoing(b.id)
    assert [(x.link_type, x.article_id) for x in out_b] == [("related", a.id)]


async def test_outgoing_grouped_orderable_by_type(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=c.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await db_session.commit()
    out = await LinkRepo(db_session).outgoing(a.id)
    # ordered by (type, title): child(Bravo) before related(Charlie)
    assert [x.link_type for x in out] == ["child", "related"]
    assert [x.title for x in out] == ["Bravo", "Charlie"]


async def test_parent_child_raw_filters_types(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=c.id, type="related")
    await db_session.commit()
    raw = await LinkRepo(db_session).parent_child_raw(dom.id)
    assert raw == [(a.id, b.id, "child")]
```

Create `tests/integration/test_citation_reads.py`:

```python
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo


async def test_list_for_article_outer_joins_source(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a", title="A", storage_ref="b:a"
    )
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="rfc793.txt", type="md", checksum="x"
    )
    repo = CitationRepo(db_session)
    await repo.create(article_id=art.id, source_id=src.id, quote="reliable", locator="p1")
    await repo.create(article_id=art.id, source_id=None, quote="no-source", locator=None)
    await db_session.commit()

    views = await repo.list_for_article(art.id)
    by_quote = {v.quote: v for v in views}
    assert by_quote["reliable"].source_filename == "rfc793.txt"
    assert by_quote["no-source"].source_id is None
    assert by_quote["no-source"].source_filename is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_link_repo.py tests/integration/test_citation_reads.py -v`
Expected: FAIL — `paw.db.repos.links` missing; `CitationRepo` has no `list_for_article`.

- [ ] **Step 3: Create `src/paw/db/repos/links.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, Link


@dataclass(frozen=True)
class LinkedArticle:
    link_type: str
    article_id: uuid.UUID
    slug: str
    title: str


class LinkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def backlinks(self, article_id: uuid.UUID) -> list[LinkedArticle]:
        res = await self._s.execute(
            select(Link.type, Article.id, Article.slug, Article.title)
            .join(Article, Article.id == Link.src_article_id)
            .where(Link.dst_article_id == article_id)
            .order_by(Link.type, Article.title)
        )
        return [
            LinkedArticle(link_type=r[0], article_id=r[1], slug=r[2], title=r[3])
            for r in res.all()
        ]

    async def outgoing(self, article_id: uuid.UUID) -> list[LinkedArticle]:
        res = await self._s.execute(
            select(Link.type, Article.id, Article.slug, Article.title)
            .join(Article, Article.id == Link.dst_article_id)
            .where(Link.src_article_id == article_id)
            .order_by(Link.type, Article.title)
        )
        return [
            LinkedArticle(link_type=r[0], article_id=r[1], slug=r[2], title=r[3])
            for r in res.all()
        ]

    async def parent_child_raw(
        self, domain_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id, Link.type).where(
                Link.domain_id == domain_id, Link.type.in_(("parent", "child"))
            )
        )
        return [(r[0], r[1], r[2]) for r in res.all()]
```

- [ ] **Step 4: Extend `src/paw/db/repos/citations.py`**

Replace the file with (adds `Source` import, `select`, `CitationView`, `list_for_article`; keeps `create`):

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Citation, Source


@dataclass(frozen=True)
class CitationView:
    id: uuid.UUID
    quote: str | None
    locator: str | None
    source_id: uuid.UUID | None
    source_filename: str | None


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

    async def list_for_article(self, article_id: uuid.UUID) -> list[CitationView]:
        res = await self._s.execute(
            select(
                Citation.id,
                Citation.quote,
                Citation.locator,
                Citation.source_id,
                Source.filename,
            )
            .outerjoin(Source, Source.id == Citation.source_id)
            .where(Citation.article_id == article_id)
            .order_by(Citation.created_at)
        )
        return [
            CitationView(
                id=r[0], quote=r[1], locator=r[2], source_id=r[3], source_filename=r[4]
            )
            for r in res.all()
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_link_repo.py tests/integration/test_citation_reads.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the ingest-op regression (CitationRepo is used by ingest)**

Run: `uv run pytest tests/integration/test_ingest_op.py -v`
Expected: PASS (the `CitationRepo.create` signature is unchanged).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/db/repos/links.py src/paw/db/repos/citations.py tests/integration/test_link_repo.py tests/integration/test_citation_reads.py
git commit -m "feat(db): LinkRepo (backlinks/outgoing/parent-child) + citation source join"
```

---

## Task 7: GraphService + GET /api/v1/graph

**Files:**
- Create: `src/paw/services/graph.py`
- Create: `src/paw/api/routers/graph.py`
- Modify: `src/paw/main.py`
- Test: `tests/api/test_graph_api.py`

**Interfaces:**
- Consumes: `GraphRepo`, `GraphNode`, `SubEdge`; `GraphConfig`; `ProviderSettingsService.get_graph`; `DomainRepo`, `ArticleRepo`; `ProblemError`.
- Produces:
  - `SubgraphPayload(root: uuid.UUID, depth: int, nodes: list[GraphNode], edges: list[SubEdge])` (frozen dataclass)
  - `GraphService(session)` with:
    - `config_for(domain_id) -> GraphConfig` — global `GraphConfig` ⊕ `domains.config["graph"]` (404 if domain missing).
    - `subgraph(*, domain_id, root, depth: int | None, types: list[str] | None) -> SubgraphPayload` — validates root-in-domain (404 otherwise), clamps `depth` to `[0, max_depth]` (default `default_depth`), intersects `types` with the allowlist (`None` = full allowlist), calls `GraphRepo.subgraph`.
  - Router `GET /graph?domain&root&depth&types` (CSV `types`, e.g. `related,parent`; empty string = no types) → `{"root", "depth", "nodes":[{id,slug,title,summary}], "edges":[{src,dst,type}]}`. Requires any authenticated role.

Needs Docker (api layer).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_graph_api.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
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


async def _seed(db_session, domain_id):
    repo = ArticleRepo(db_session)
    did = uuid.UUID(domain_id)
    a = await repo.create(domain_id=did, slug="a", title="A", storage_ref="b:a", summary="sa")
    b = await repo.create(domain_id=did, slug="b", title="B", storage_ref="b:b", summary="sb")
    c = await repo.create(domain_id=did, slug="c", title="C", storage_ref="b:c", summary="sc")
    graph = GraphRepo(db_session)
    await graph.link(domain_id=did, src_article_id=a.id, dst_article_id=b.id, type="related")
    await graph.link(domain_id=did, src_article_id=a.id, dst_article_id=c.id, type="parent")
    await db_session.commit()
    return a, b, c


async def test_graph_requires_auth(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        r = await c.get(f"/api/v1/graph?domain={uuid.uuid4()}&root={uuid.uuid4()}")
    assert r.status_code == 401


async def test_graph_returns_nodes_and_edges(ctx):
    c, csrf, dom, db_session = ctx
    a, b, cc = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=1")
    assert r.status_code == 200
    data = r.json()
    assert data["root"] == str(a.id)
    assert {n["id"] for n in data["nodes"]} == {str(a.id), str(b.id), str(cc.id)}
    assert any(n["summary"] == "sa" for n in data["nodes"])
    assert {(e["src"], e["dst"], e["type"]) for e in data["edges"]} == {
        (str(a.id), str(b.id), "related"),
        (str(a.id), str(cc.id), "parent"),
    }


async def test_graph_type_filter_drops_edges(ctx):
    c, csrf, dom, db_session = ctx
    a, b, _c = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=2&types=related")
    data = r.json()
    assert {n["id"] for n in data["nodes"]} == {str(a.id), str(b.id)}
    assert [e["type"] for e in data["edges"]] == ["related"]


async def test_graph_clamps_depth_to_max(ctx):
    c, csrf, dom, db_session = ctx
    a, _b, _c = await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={a.id}&depth=99")
    assert r.json()["depth"] == 4  # GraphConfig.max_depth default


async def test_graph_root_outside_domain_404(ctx):
    c, csrf, dom, db_session = ctx
    await _seed(db_session, dom)
    r = await c.get(f"/api/v1/graph?domain={dom}&root={uuid.uuid4()}")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_graph_api.py -v`
Expected: FAIL — `/api/v1/graph` route does not exist.

- [ ] **Step 3: Create `src/paw/services/graph.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphNode, GraphRepo
from paw.graph.subgraph import SubEdge
from paw.providers.config import GraphConfig
from paw.services.provider_settings import ProviderSettingsService


@dataclass(frozen=True)
class SubgraphPayload:
    root: uuid.UUID
    depth: int
    nodes: list[GraphNode]
    edges: list[SubEdge]


class GraphService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def config_for(self, domain_id: uuid.UUID) -> GraphConfig:
        cfg = await ProviderSettingsService(self._s).get_graph()
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        overrides = dom.config.get("graph") if isinstance(dom.config, dict) else None
        if isinstance(overrides, dict):
            return GraphConfig.model_validate({**cfg.model_dump(), **overrides})
        return cfg

    async def subgraph(
        self,
        *,
        domain_id: uuid.UUID,
        root: uuid.UUID,
        depth: int | None,
        types: list[str] | None,
    ) -> SubgraphPayload:
        cfg = await self.config_for(domain_id)
        art = await ArticleRepo(self._s).get(root)
        if art is None or art.domain_id != domain_id:
            raise ProblemError(status=404, title="Root article not found in domain")
        eff_depth = cfg.default_depth if depth is None else depth
        eff_depth = max(0, min(eff_depth, cfg.max_depth))
        allow = set(cfg.link_types)
        eff_types = list(allow) if types is None else [t for t in types if t in allow]
        nodes, edges = await GraphRepo(self._s).subgraph(
            domain_id=domain_id, root_article_id=root, depth=eff_depth, types=eff_types
        )
        return SubgraphPayload(root=root, depth=eff_depth, nodes=nodes, edges=edges)
```

- [ ] **Step 4: Create `src/paw/api/routers/graph.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_role
from paw.services.graph import GraphService

router = APIRouter(tags=["graph"])


@router.get(
    "/graph",
    dependencies=[Depends(require_role("admin", "editor", "viewer"))],
)
async def get_graph(
    domain: uuid.UUID,
    root: uuid.UUID,
    depth: int | None = None,
    types: str | None = None,
    session: AsyncSession = Depends(db),
) -> dict[str, object]:
    # types is a CSV; absent -> None (full allowlist); "" -> [] (no edges, root only)
    type_list = None if types is None else [t for t in types.split(",") if t]
    payload = await GraphService(session).subgraph(
        domain_id=domain, root=root, depth=depth, types=type_list
    )
    return {
        "root": str(payload.root),
        "depth": payload.depth,
        "nodes": [
            {"id": str(n.id), "slug": n.slug, "title": n.title, "summary": n.summary}
            for n in payload.nodes
        ],
        "edges": [
            {"src": str(e.src), "dst": str(e.dst), "type": e.type} for e in payload.edges
        ],
    }
```

- [ ] **Step 5: Register the router in `src/paw/main.py`**

Add the import next to the other routers:

```python
from paw.api.routers import graph as graph_router
```

Add `graph_router` to the `include_router` loop tuple (after `chat_router`):

```python
    for r in (
        auth_router,
        domains_router,
        sources_router,
        articles_router,
        setup_router,
        settings_router,
        users_router,
        jobs_router,
        query_router,
        chat_router,
        graph_router,
    ):
        app.include_router(r.router, prefix="/api/v1")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_graph_api.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/services/graph.py src/paw/api/routers/graph.py src/paw/main.py tests/api/test_graph_api.py
git commit -m "feat(graph): GraphService + GET /api/v1/graph (depth clamp, type filter)"
```

---

## Task 8: Wikilink resolver

**Files:**
- Modify: `src/paw/security/sanitize.py`
- Test: `tests/unit/test_wikilinks.py`

**Interfaces:**
- Produces: `resolve_wikilinks(text: str, slug_to_id: dict[str, uuid.UUID]) -> str` — rewrites `[[slug]]` and `[[slug|label]]` to `[label](/articles/{id})` for known slugs; unknown slugs become plain `label` text (no broken link); leaves all other markdown untouched. Run BEFORE `render_markdown`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wikilinks.py`:

```python
import uuid

from paw.security.sanitize import render_markdown, resolve_wikilinks


def test_known_slug_becomes_link():
    aid = uuid.uuid4()
    out = resolve_wikilinks("see [[tcp]] now", {"tcp": aid})
    assert out == f"see [tcp](/articles/{aid}) now"


def test_labelled_wikilink_uses_label():
    aid = uuid.uuid4()
    out = resolve_wikilinks("[[tcp|the TCP page]]", {"tcp": aid})
    assert out == f"[the TCP page](/articles/{aid})"


def test_unknown_slug_renders_plain_label():
    assert resolve_wikilinks("[[ghost]]", {}) == "ghost"
    assert resolve_wikilinks("[[ghost|Ghost]]", {}) == "Ghost"


def test_multiple_wikilinks_in_one_line():
    a, b = uuid.uuid4(), uuid.uuid4()
    out = resolve_wikilinks("[[a]] and [[b]]", {"a": a, "b": b})
    assert out == f"[a](/articles/{a}) and [b](/articles/{b})"


def test_rendered_html_carries_relative_anchor():
    aid = uuid.uuid4()
    html = render_markdown(resolve_wikilinks("[[tcp]]", {"tcp": aid}))
    assert f'href="/articles/{aid}"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_wikilinks.py -v`
Expected: FAIL — `cannot import name 'resolve_wikilinks'`.

- [ ] **Step 3: Add `resolve_wikilinks` to `src/paw/security/sanitize.py`**

Add the imports at the top (after the existing `import mistune` / `import nh3`):

```python
import re
import uuid
```

Add this regex + function below `_md` (above or below `render_markdown`):

```python
# [[slug]] or [[slug|label]] — slug has no '|' or ']'; optional label has no ']'.
_WIKILINK = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]")


def resolve_wikilinks(text: str, slug_to_id: dict[str, uuid.UUID]) -> str:
    """Rewrite [[slug]] / [[slug|label]] to markdown links for known slugs.

    Unknown slugs degrade to their plain label text (visible, not a broken link).
    Call this BEFORE render_markdown.
    """

    def _replace(match: re.Match[str]) -> str:
        slug = match.group(1).strip()
        label = (match.group(2) or slug).strip()
        article_id = slug_to_id.get(slug)
        return f"[{label}](/articles/{article_id})" if article_id is not None else label

    return _WIKILINK.sub(_replace, text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_wikilinks.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/security/sanitize.py tests/unit/test_wikilinks.py
git commit -m "feat(render): [[slug]] wikilink resolver"
```

---

## Task 9: ArticleService metadata + tree + slug map

**Files:**
- Modify: `src/paw/db/repos/articles.py`
- Modify: `src/paw/services/articles.py`
- Test: `tests/integration/test_article_meta.py`

**Interfaces:**
- Consumes: `LinkRepo`, `LinkedArticle` (T6); `CitationRepo`, `CitationView` (T6); `build_tree`, `normalize_parent_child`, `TreeNode` (T4); `ArticleRevision`, `Article` models.
- Produces:
  - `ArticleRepo.slug_id_map(domain_id) -> dict[str, uuid.UUID]`
  - `ArticleMeta(article: Article, backlinks: list[LinkedArticle], outgoing: list[LinkedArticle], citations: list[CitationView], revisions: list[ArticleRevision])` (dataclass, in `services/articles.py`)
  - `ArticleService.get_meta(article_id) -> ArticleMeta` (404 if article missing)
  - `ArticleService.domain_tree(domain_id) -> list[TreeNode]`
  - `ArticleService.slug_map(domain_id) -> dict[str, uuid.UUID]`

Needs Docker (integration layer).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_article_meta.py`:

```python
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.graph.repo import GraphRepo
from paw.services.articles import ArticleService


async def _seed(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    a = await repo.create(domain_id=dom.id, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=dom.id, slug="b", title="Bravo", storage_ref="b:b")
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="src.md", type="md", checksum="x"
    )
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=a.id, type="related")
    await CitationRepo(db_session).create(
        article_id=a.id, source_id=src.id, quote="q", locator="l"
    )
    await db_session.commit()
    return dom, a, b


async def test_get_meta_aggregates_links_citations_revisions(db_session):
    dom, a, b = await _seed(db_session)
    meta = await ArticleService(db_session).get_meta(a.id)
    assert {x.article_id for x in meta.backlinks} == {b.id}  # b -> a (related)
    assert {(x.link_type, x.article_id) for x in meta.outgoing} == {("child", b.id)}
    assert meta.citations[0].source_filename == "src.md"
    assert meta.revisions == []  # repo.create makes no revision row by itself


async def test_domain_tree_nests_child_links(db_session):
    dom, a, b = await _seed(db_session)
    tree = await ArticleService(db_session).domain_tree(dom.id)
    # a --child--> b : a is the parent root, b nested under it
    assert [n.title for n in tree] == ["Alpha"]
    assert [c.title for c in tree[0].children] == ["Bravo"]


async def test_slug_map(db_session):
    dom, a, b = await _seed(db_session)
    smap = await ArticleService(db_session).slug_map(dom.id)
    assert smap == {"a": a.id, "b": b.id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_article_meta.py -v`
Expected: FAIL — `ArticleService` has no `get_meta` / `slug_id_map` missing.

- [ ] **Step 3: Add `slug_id_map` to `src/paw/db/repos/articles.py`**

Add the method to `ArticleRepo` (after `list_by_domain`):

```python
    async def slug_id_map(self, domain_id: uuid.UUID) -> dict[str, uuid.UUID]:
        res = await self._s.execute(
            select(Article.slug, Article.id).where(Article.domain_id == domain_id)
        )
        return {row[0]: row[1] for row in res.all()}
```

- [ ] **Step 4: Extend `src/paw/services/articles.py`**

The current top imports are:

```python
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.storage.postgres import PostgresStorage
```

Change the `paw.db.models` import to add `ArticleRevision`, and add four new imports:

```python
from paw.db.models import Article, ArticleRevision
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo, CitationView
from paw.db.repos.links import LinkRepo, LinkedArticle
from paw.graph.tree import TreeNode, build_tree, normalize_parent_child
from paw.storage.postgres import PostgresStorage
```

Add the dataclass after `ArticleBody`:

```python
@dataclass
class ArticleMeta:
    article: Article
    backlinks: list[LinkedArticle]
    outgoing: list[LinkedArticle]
    citations: list[CitationView]
    revisions: list[ArticleRevision]
```

Add the methods to `ArticleService` (after `list_by_domain`):

```python
    async def get_meta(self, article_id: uuid.UUID) -> ArticleMeta:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        links = LinkRepo(self._s)
        return ArticleMeta(
            article=art,
            backlinks=await links.backlinks(article_id),
            outgoing=await links.outgoing(article_id),
            citations=await CitationRepo(self._s).list_for_article(article_id),
            revisions=await self._repo.list_revisions(article_id),
        )

    async def domain_tree(self, domain_id: uuid.UUID) -> list[TreeNode]:
        articles = await self._repo.list_by_domain(domain_id)
        nodes = [(a.id, a.slug, a.title) for a in articles]
        typed = await LinkRepo(self._s).parent_child_raw(domain_id)
        return build_tree(nodes, normalize_parent_child(typed))

    async def slug_map(self, domain_id: uuid.UUID) -> dict[str, uuid.UUID]:
        return await self._repo.slug_id_map(domain_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_article_meta.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/db/repos/articles.py src/paw/services/articles.py tests/integration/test_article_meta.py
git commit -m "feat(articles): get_meta + domain_tree + slug_map"
```

---

## Task 10: Article page — metadata sections, wikilinks, rollback UI

**Files:**
- Modify: `src/paw/api/routers/articles.py`
- Modify: `src/paw/api/web/routes.py`
- Modify: `src/paw/api/web/templates/article.html`
- Modify: `src/paw/api/web/static/app.js`
- Modify: `src/paw/api/web/static/theme.css`
- Create: `src/paw/api/web/templates/_sidebar_tree.html`
- Test: `tests/api/test_article_meta_web.py`

**Interfaces:**
- Consumes: `ArticleService.get_meta`, `.domain_tree`, `.slug_map`, `.rollback` (T9, Phase 1); `resolve_wikilinks` (T8); `DomainRepo`.
- Produces:
  - API `get_article` now resolves `[[refs]]` before rendering HTML.
  - Web `GET /articles/{id}` passes `meta`, `tree`, `domain_name`, wikilinked `html`.
  - Web `POST /articles/{id}/rollback` (Form `rev_no`) → calls `ArticleService.rollback`, returns `204` with `HX-Refresh: true`.
  - `article.html` renders Citations / Backlinks / Related-parent-child (grouped by type) / Revisions (per-revision Rollback buttons) + a sidebar tree + an "Open in graph" link.
  - `app.js` filters `.tree-item` by `#tree-filter` input.

Needs Docker (api layer).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_article_meta_web.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
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


async def test_wikilink_renders_as_anchor_in_api_html(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="A", storage_ref="b:a")
    await db_session.commit()
    # article B references [[a]] -> must resolve to /articles/{a.id}
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "b", "title": "B", "markdown": "see [[a]]"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert f'href="/articles/{a.id}"' in g.json()["html"]


async def test_article_page_shows_meta_sections(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=did, slug="b", title="Bravo", storage_ref="b:b")
    src = await SourceRepo(db_session).create(
        domain_id=did, storage_ref="b:s", filename="rfc.txt", type="md", checksum="x"
    )
    graph = GraphRepo(db_session)
    await graph.link(domain_id=did, src_article_id=b.id, dst_article_id=a.id, type="related")
    await graph.link(domain_id=did, src_article_id=a.id, dst_article_id=b.id, type="child")
    await CitationRepo(db_session).create(
        article_id=a.id, source_id=src.id, quote="reliable", locator="p1"
    )
    await db_session.commit()

    page = await c.get(f"/articles/{a.id}")
    assert page.status_code == 200
    assert "Backlinks" in page.text
    assert f'href="/articles/{b.id}"' in page.text  # backlink + outgoing both point at b
    assert "rfc.txt" in page.text  # citation source filename
    assert "Citations" in page.text
    assert f"/domains/{dom}/graph?root={a.id}" in page.text  # open-in-graph link
    assert 'id="tree-filter"' in page.text  # sidebar tree filter box


async def test_web_rollback_returns_hx_refresh(ctx):
    c, csrf, dom, db_session = ctx
    art = (
        await c.post(
            f"/api/v1/domains/{dom}/articles",
            json={"slug": "tls", "title": "TLS", "markdown": "# v1"},
            headers={"x-csrf-token": csrf},
        )
    ).json()
    await c.put(
        f"/api/v1/articles/{art['id']}",
        json={"title": "TLS", "markdown": "# v2", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    rb = await c.post(
        f"/articles/{art['id']}/rollback",
        data={"rev_no": 1},
        headers={"x-csrf-token": csrf},
    )
    assert rb.status_code == 204
    assert rb.headers.get("HX-Refresh") == "true"
    g = await c.get(f"/api/v1/articles/{art['id']}")
    assert "v1" in g.json()["html"]
    assert g.json()["current_rev"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_article_meta_web.py -v`
Expected: FAIL — API html lacks the anchor; web page lacks meta sections; rollback web route 404/405.

- [ ] **Step 3: Resolve `[[refs]]` in the API `get_article` (`src/paw/api/routers/articles.py`)**

Replace the import line `from paw.security.sanitize import render_markdown` with:

```python
from paw.security.sanitize import render_markdown, resolve_wikilinks
```

Then replace the `get_article` handler body:

```python
@router.get("/articles/{article_id}", response_model=ArticleDetail)
async def get_article(
    article_id: uuid.UUID,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> ArticleDetail:
    svc = ArticleService(session)
    body = await svc.get_body(article_id)
    slug_map = await svc.slug_map(body.article.domain_id)
    html = render_markdown(resolve_wikilinks(body.markdown, slug_map))
    return ArticleDetail(
        id=str(body.article.id),
        slug=body.article.slug,
        title=body.article.title,
        current_rev=body.article.current_rev,
        html=html,
    )
```

- [ ] **Step 4: Update the web `article_page` + add web rollback (`src/paw/api/web/routes.py`)**

Replace the import line `from paw.security.sanitize import render_markdown` with:

```python
from paw.security.sanitize import render_markdown, resolve_wikilinks
```

(`Form`, `Response`, `require_role`, `DomainRepo`, `ArticleService` are already imported.) Replace the `article_page` handler and add `web_rollback` after it:

```python
@router.get("/articles/{article_id}", response_class=HTMLResponse)
async def article_page(
    article_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    svc = ArticleService(session)
    body = await svc.get_body(article_id)
    meta = await svc.get_meta(article_id)
    tree = await svc.domain_tree(body.article.domain_id)
    slug_map = await svc.slug_map(body.article.domain_id)
    domain = await DomainRepo(session).get(body.article.domain_id)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "article": body.article,
            "html": render_markdown(resolve_wikilinks(body.markdown, slug_map)),
            "markdown": body.markdown,
            "meta": meta,
            "tree": tree,
            "domain_name": domain.name if domain else "",
            "csrf": csrf,
        },
    )


@router.post("/articles/{article_id}/rollback")
async def web_rollback(
    article_id: uuid.UUID,
    rev_no: int = Form(...),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    user: User = Depends(require_role("admin", "editor")),
) -> Response:
    await ArticleService(session).rollback(
        article_id=article_id, rev_no=rev_no, author_id=user.id
    )
    # HTMX reloads the page so the new revision + metadata sections refresh.
    return Response(status_code=204, headers={"HX-Refresh": "true"})
```

- [ ] **Step 5: Rewrite `src/paw/api/web/templates/article.html`**

```html
{% extends "base.html" %}
{% block title %}{{ article.title }} · Personal AI Wiki{% endblock %}
{% block sidebar %}{% include "_sidebar_tree.html" %}{% endblock %}
{% block content %}
<div id="conflict-banner" class="banner" style="display:none">
  This article changed on the server (409 conflict). Reload the page before saving again.
</div>
<div class="content-header">
  <a class="btn" href="/domains/{{ article.domain_id }}/graph?root={{ article.id }}">🕸 Open in graph</a>
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

<section class="meta">
  <h3>Citations / Sources</h3>
  {% if meta.citations %}
  <ul>{% for c in meta.citations %}
    <li>{% if c.quote %}“{{ c.quote }}”{% endif %}{% if c.locator %} <small>({{ c.locator }})</small>{% endif %}
      — {% if c.source_filename %}{{ c.source_filename }}{% else %}<em class="muted">no source</em>{% endif %}</li>
  {% endfor %}</ul>
  {% else %}<p class="muted">No citations.</p>{% endif %}
</section>

<section class="meta">
  <h3>Backlinks</h3>
  {% if meta.backlinks %}
  <ul>{% for b in meta.backlinks %}
    <li><a href="/articles/{{ b.article_id }}">{{ b.title }}</a> <small>({{ b.link_type }})</small></li>
  {% endfor %}</ul>
  {% else %}<p class="muted">No backlinks.</p>{% endif %}
</section>

<section class="meta">
  <h3>Related / Parent / Child</h3>
  {% if meta.outgoing %}
  {% for group, items in meta.outgoing | groupby("link_type") %}
  <h4>{{ group }}</h4>
  <ul>{% for o in items %}<li><a href="/articles/{{ o.article_id }}">{{ o.title }}</a></li>{% endfor %}</ul>
  {% endfor %}
  {% else %}<p class="muted">No outgoing links.</p>{% endif %}
</section>

<section class="meta">
  <h3>Revisions</h3>
  <ul>{% for r in meta.revisions %}
    <li>v{{ r.rev_no }} · {{ r.origin }}
      {% if r.rev_no != article.current_rev %}
      <button type="button" hx-post="/articles/{{ article.id }}/rollback"
              hx-vals='{"rev_no": {{ r.rev_no }}}'
              hx-headers='{"x-csrf-token": "{{ csrf }}"}'>Rollback</button>
      {% endif %}
    </li>
  {% endfor %}</ul>
</section>
{% endblock %}
```

- [ ] **Step 6: Create the shared sidebar partial `src/paw/api/web/templates/_sidebar_tree.html`**

```html
<h3>{{ domain_name }}</h3>
<input type="text" id="tree-filter" placeholder="Filter…" autocomplete="off">
<ul class="tree">
{% for node in tree recursive %}
  <li class="tree-item" data-title="{{ node.title | lower }}">
    <a href="/articles/{{ node.id }}">{{ node.title }}</a>
    {% if node.children %}<ul>{{ loop(node.children) }}</ul>{% endif %}
  </li>
{% endfor %}
</ul>
```

- [ ] **Step 7: Add the tree-filter handler to `src/paw/api/web/static/app.js`**

Append:

```javascript
// Sidebar parent/child tree filter (CSP-safe: external file, no inline handlers).
document.addEventListener("input", (e) => {
  if (e.target.id !== "tree-filter") return;
  const needle = e.target.value.toLowerCase();
  document.querySelectorAll(".tree-item").forEach((li) => {
    li.style.display = li.dataset.title.includes(needle) ? "" : "none";
  });
});
```

- [ ] **Step 8: Add styles to `src/paw/api/web/static/theme.css`**

Append:

```css
.content-header { display:flex; gap:.6rem; align-items:center; margin-bottom:1rem; }
.btn { display:inline-block; text-decoration:none; background:var(--accent); color:#fff;
       padding:.4rem .8rem; border-radius:6px; }
.meta { margin-top:1.5rem; border-top:1px solid var(--border); padding-top:.8rem; }
.meta h4 { margin:.4rem 0 .2rem; text-transform:capitalize; }
.muted { color:#888; }
.tree { list-style:none; padding-left:0; }
.tree ul { list-style:none; padding-left:1rem; }
.tree-item { padding:.1rem 0; }
```

- [ ] **Step 9: Run test to verify it passes**

Run: `uv run pytest tests/api/test_article_meta_web.py -v`
Expected: PASS (3 tests).

- [ ] **Step 10: Run the article API regression suite**

Run: `uv run pytest tests/api/test_articles.py -v`
Expected: PASS (existing create/get/update/rollback tests still pass — `get_article` change is backward compatible).

- [ ] **Step 11: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/routers/articles.py src/paw/api/web/routes.py src/paw/api/web/templates/article.html src/paw/api/web/templates/_sidebar_tree.html src/paw/api/web/static/app.js src/paw/api/web/static/theme.css tests/api/test_article_meta_web.py
git commit -m "feat(web): article metadata sections + wikilinks + rollback UI + tree sidebar"
```

---

## Task 11: Domain page sidebar tree + graph button

**Files:**
- Modify: `src/paw/api/web/routes.py`
- Modify: `src/paw/api/web/templates/domain.html`
- Test: `tests/api/test_graph_web.py` (created here; the graph page test is added in Task 12)

**Interfaces:**
- Consumes: `ArticleService.domain_tree` (T9); `_sidebar_tree.html` (T10).
- Produces: `domain_page` passes `tree` + `domain_name`; `domain.html` renders the tree sidebar (replacing the flat list) and a "🕸 Graph" button in the content header.

Needs Docker (api layer).

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_graph_web.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
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


async def test_domain_page_renders_tree_sidebar_and_graph_button(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=did, slug="b", title="Bravo", storage_ref="b:b")
    await GraphRepo(db_session).link(
        domain_id=did, src_article_id=a.id, dst_article_id=b.id, type="child"
    )
    await db_session.commit()

    page = await c.get(f"/domains/{dom}")
    assert page.status_code == 200
    assert 'id="tree-filter"' in page.text  # tree sidebar replaced the flat list
    assert 'class="tree"' in page.text
    assert f'href="/domains/{dom}/graph"' in page.text  # graph button
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_graph_web.py -v`
Expected: FAIL — domain page has no tree / no graph button.

- [ ] **Step 3: Pass `tree` + `domain_name` from `domain_page` (`src/paw/api/web/routes.py`)**

Replace the `domain_page` handler's template-context dict so it includes the tree (it already loads `articles`/`sources`):

```python
@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domain_page(
    domain_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    sources = await SourceRepo(session).list_by_domain(domain_id)
    tree = await ArticleService(session).domain_tree(domain_id)
    latest_source_id = str(sources[-1].id) if sources else None
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "domain.html",
        {
            "domain": domain,
            "articles": articles,
            "tree": tree,
            "domain_name": domain.name if domain else "",
            "csrf": csrf,
            "latest_source_id": latest_source_id,
        },
    )
```

- [ ] **Step 4: Update `src/paw/api/web/templates/domain.html`**

```html
{% extends "base.html" %}
{% block title %}{{ domain.name }} · Personal AI Wiki{% endblock %}
{% block sidebar %}{% include "_sidebar_tree.html" %}{% endblock %}
{% block content %}
<h1>{{ domain.name }}</h1>
<div class="content-header">
  <form hx-post="/domains/{{ domain.id }}/ingest"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <input type="hidden" name="source_id" value="{{ latest_source_id | default('') }}">
    <button type="submit" {% if not latest_source_id %}disabled{% endif %}>Ingest latest source</button>
  </form>
  <a class="btn" href="/domains/{{ domain.id }}/query">🔍 Query</a>
  <a class="btn" href="/domains/{{ domain.id }}/graph">🕸 Graph</a>
</div>
<aside id="job-drawer" class="drawer"></aside>
{% endblock %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_graph_web.py -v`
Expected: PASS (1 test).

- [ ] **Step 6: Run the web-pages regression suite**

Run: `uv run pytest tests/api/test_web_pages.py -v`
Expected: PASS (`test_domain_page_has_ingest_action` still finds the ingest form + `#job-drawer`).

- [ ] **Step 7: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/domain.html tests/api/test_graph_web.py
git commit -m "feat(web): domain sidebar parent/child tree + graph entry button"
```

---

## Task 12: Graph UI page + vendored Cytoscape

**Files:**
- Modify: `src/paw/api/web/routes.py`
- Modify: `src/paw/api/web/templates/base.html`
- Create: `src/paw/api/web/templates/graph.html`
- Create: `src/paw/api/web/static/graph.js`
- Create: `src/paw/api/web/static/cytoscape.min.js` (vendored)
- Modify: `src/paw/api/web/static/theme.css`
- Test: `tests/api/test_graph_web.py` (extends Task 11's file)

**Interfaces:**
- Consumes: `GraphService.config_for` (T7); `ArticleRepo.list_by_domain` (Phase 1); `DomainRepo`.
- Produces:
  - Web `GET /domains/{domain_id}/graph?root=<id?>` — renders `graph.html` with the domain, its articles (root selector), the resolved `default_depth`/`max_depth`/`link_types`, and the chosen `root_id` (query param `root`, else the first article).
  - `base.html` gains `{% block scripts %}{% endblock %}` before `</body>`.
  - `graph.html` overrides `scripts` to load vendored Cytoscape + `graph.js`; renders top controls + `#cy` canvas (with `data-domain`/`data-root`/`data-depth`) + `#graph-drawer`.
  - `graph.js` (CSP-safe IIFE): reads controls/dataset, `fetch`es `/api/v1/graph`, renders Cytoscape, node tap → drawer (summary via `textContent`, "Open" link to `/articles/{id}`).

Needs Docker (api layer).

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_graph_web.py`:

```python
async def test_graph_page_renders_canvas_and_vendored_scripts(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="Alpha", storage_ref="b:a")
    await db_session.commit()

    page = await c.get(f"/domains/{dom}/graph?root={a.id}")
    assert page.status_code == 200
    assert 'id="cy"' in page.text
    assert f'data-domain="{dom}"' in page.text
    assert f'data-root="{a.id}"' in page.text
    assert 'src="/static/cytoscape.min.js"' in page.text
    assert 'src="/static/graph.js"' in page.text
    assert 'id="graph-root"' in page.text  # root selector
    assert 'id="graph-depth"' in page.text  # depth slider


async def test_graph_static_assets_served(ctx):
    c, csrf, dom, db_session = ctx
    cy = await c.get("/static/cytoscape.min.js")
    gj = await c.get("/static/graph.js")
    assert cy.status_code == 200
    assert gj.status_code == 200


async def test_graph_page_defaults_root_to_first_article(ctx):
    c, csrf, dom, db_session = ctx
    repo = ArticleRepo(db_session)
    did = uuid.UUID(dom)
    a = await repo.create(domain_id=did, slug="a", title="Alpha", storage_ref="b:a")
    await db_session.commit()
    page = await c.get(f"/domains/{dom}/graph")  # no ?root
    assert f'data-root="{a.id}"' in page.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_graph_web.py -v`
Expected: FAIL — `/domains/{id}/graph` page route missing; static `cytoscape.min.js`/`graph.js` 404.

- [ ] **Step 3: Vendor Cytoscape into `src/paw/api/web/static/cytoscape.min.js`**

Download a pinned release (no CDN at runtime — one-time dev step; the file is committed):

```bash
curl -fsSL https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js \
  -o src/paw/api/web/static/cytoscape.min.js
test -s src/paw/api/web/static/cytoscape.min.js && echo "vendored OK"
```

Expected: `vendored OK` and a ~400 KB file. (If unpkg is unreachable, fetch the same `cytoscape@3.30.2/dist/cytoscape.min.js` from any mirror — only the committed file matters; tests check presence, not integrity.)

- [ ] **Step 4: Create `src/paw/api/web/static/graph.js`**

```javascript
// CSP-safe: external file, no inline handlers, no eval. Renders the subgraph via vendored Cytoscape.
(function () {
  const cy = document.getElementById("cy");
  if (!cy || typeof cytoscape === "undefined") return;
  const domain = cy.dataset.domain;
  const rootSel = document.getElementById("graph-root");
  const depth = document.getElementById("graph-depth");
  const depthVal = document.getElementById("graph-depth-val");
  const drawer = document.getElementById("graph-drawer");
  let graph = null;

  function selectedTypes() {
    return Array.from(document.querySelectorAll(".graph-type:checked")).map((c) => c.value);
  }

  function showDrawer(node) {
    drawer.textContent = "";
    const h = document.createElement("h3");
    h.textContent = node.label;
    const p = document.createElement("p");
    p.textContent = node.summary || "";
    const a = document.createElement("a");
    a.href = "/articles/" + node.id;
    a.textContent = "Open";
    drawer.appendChild(h);
    drawer.appendChild(p);
    drawer.appendChild(a);
    drawer.hidden = false;
  }

  function render(data) {
    const elements = [];
    for (const n of data.nodes) {
      elements.push({ data: { id: n.id, label: n.title, summary: n.summary, slug: n.slug } });
    }
    for (const e of data.edges) {
      elements.push({
        data: { id: e.src + "_" + e.dst + "_" + e.type, source: e.src, target: e.dst, label: e.type },
      });
    }
    graph = cytoscape({
      container: cy,
      elements: elements,
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": "10px" } },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            "font-size": "8px",
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
          },
        },
      ],
      layout: { name: "cose" },
    });
    graph.on("tap", "node", (evt) => showDrawer(evt.target.data()));
  }

  async function load() {
    const root = rootSel ? rootSel.value : cy.dataset.root;
    if (!root) return;
    const types = selectedTypes().join(",");
    const url =
      "/api/v1/graph?domain=" + domain + "&root=" + root + "&depth=" + depth.value + "&types=" + types;
    const resp = await fetch(url, { headers: { accept: "application/json" } });
    if (!resp.ok) return;
    render(await resp.json());
  }

  if (depth && depthVal) {
    depth.addEventListener("input", () => {
      depthVal.textContent = depth.value;
      load();
    });
  }
  if (rootSel) rootSel.addEventListener("change", load);
  document.querySelectorAll(".graph-type").forEach((c) => c.addEventListener("change", load));
  load();
})();
```

- [ ] **Step 5: Add the `scripts` block to `src/paw/api/web/templates/base.html`**

Insert immediately before `</body>`:

```html
  {% block scripts %}{% endblock %}
</body>
```

- [ ] **Step 6: Create `src/paw/api/web/templates/graph.html`**

```html
{% extends "base.html" %}
{% block title %}Graph · {{ domain.name }}{% endblock %}
{% block sidebar %}<h3>{{ domain.name }}</h3>{% endblock %}
{% block content %}
<h1>🕸 Graph · {{ domain.name }}</h1>
<div class="graph-controls">
  <label>Root
    <select id="graph-root">
      {% for a in articles %}
      <option value="{{ a.id }}" {% if a.id|string == root_id|string %}selected{% endif %}>{{ a.title }}</option>
      {% endfor %}
    </select>
  </label>
  <label>Depth
    <input type="range" id="graph-depth" min="1" max="{{ max_depth }}" value="{{ default_depth }}">
    <span id="graph-depth-val">{{ default_depth }}</span>
  </label>
  <span class="graph-types">
    {% for t in link_types %}
    <label><input type="checkbox" class="graph-type" value="{{ t }}" checked> {{ t }}</label>
    {% endfor %}
  </span>
</div>
<div id="cy" data-domain="{{ domain.id }}" data-root="{{ root_id }}" data-depth="{{ default_depth }}"></div>
<aside id="graph-drawer" class="drawer" hidden></aside>
{% endblock %}
{% block scripts %}
<script src="/static/cytoscape.min.js" defer></script>
<script src="/static/graph.js" defer></script>
{% endblock %}
```

- [ ] **Step 7: Add the graph page route to `src/paw/api/web/routes.py`**

Add the import near the other services:

```python
from paw.services.graph import GraphService
```

Add the handler (e.g. after `query_page`):

```python
@router.get("/domains/{domain_id}/graph", response_class=HTMLResponse)
async def graph_page(
    domain_id: uuid.UUID,
    request: Request,
    root: uuid.UUID | None = None,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> Response:
    if not await _current_uid(request, store):
        return RedirectResponse("/login", status_code=307)
    domain = await DomainRepo(session).get(domain_id)
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    cfg = await GraphService(session).config_for(domain_id)
    root_id = root or (articles[0].id if articles else None)
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request,
        "graph.html",
        {
            "domain": domain,
            "articles": articles,
            "root_id": root_id,
            "default_depth": cfg.default_depth,
            "max_depth": cfg.max_depth,
            "link_types": cfg.link_types,
            "csrf": csrf,
        },
    )
```

- [ ] **Step 8: Add graph canvas styles to `src/paw/api/web/static/theme.css`**

Append:

```css
.graph-controls { display:flex; gap:1rem; align-items:center; flex-wrap:wrap; margin-bottom:.8rem; }
.graph-types label { margin-right:.6rem; font-size:.9rem; }
#cy { width:100%; height:calc(100vh - 160px); border:1px solid var(--border); border-radius:8px;
      background:var(--surface); }
.drawer { position:fixed; right:0; top:0; width:320px; height:100vh; background:var(--surface);
          border-left:1px solid var(--border); padding:1rem; overflow:auto; box-shadow:-2px 0 8px rgba(0,0,0,.1); }
```

- [ ] **Step 9: Run test to verify it passes**

Run: `uv run pytest tests/api/test_graph_web.py -v`
Expected: PASS (4 tests — Task 11's + the 3 added here).

- [ ] **Step 10: Run the web-shell regression suite**

Run: `uv run pytest tests/api/test_web_shell.py -v`
Expected: PASS (the new `{% block scripts %}` did not break the base frame / static serving).

- [ ] **Step 11: Lint + commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/base.html src/paw/api/web/templates/graph.html src/paw/api/web/static/graph.js src/paw/api/web/static/cytoscape.min.js src/paw/api/web/static/theme.css tests/api/test_graph_web.py
git commit -m "feat(web): full-canvas Cytoscape graph page (vendored) + drawer"
```

---

## Task 13: End-to-end — ingest → graph → backlinks → edit/rollback

**Files:**
- Create: `tests/e2e/test_graph_editing_e2e.py`

**Interfaces:**
- Consumes: `run_ingest` (Phase 2); `GraphRepo`, `CitationRepo`, `SourceRepo` (seed a typed link + sourced citation); the `/api/v1/graph`, `/articles/{id}` (web), `/api/v1/articles/{id}` (PUT/rollback) endpoints.

This task needs Docker (e2e layer).

- [ ] **Step 1: Write the E2E test**

Create `tests/e2e/test_graph_editing_e2e.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.ingest import run_ingest
from paw.main import create_app
from paw.providers.config import WikiConfig
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService

_FERNET = "k" * 43 + "="


def _ingest_chat(slug: str, title: str) -> StubChatProvider:
    # extraction then drafting; two shared entities (TCP, IP) -> co-occurrence link on 2nd ingest
    extraction = {"entities": ["TCP", "IP"], "key_points": ["reliable delivery"]}
    draft = {
        "slug": slug,
        "title": title,
        "summary": f"{title} summary",
        "markdown": f"## Overview\n{title} relies on TCP and IP.",
        "entities": ["TCP", "IP"],
        "citations": [{"quote": "reliable delivery", "locator": None}],
    }
    payloads = iter([extraction, draft])

    def responder(messages, tools):
        return StubChatProvider.tool("emit_result", next(payloads))

    return StubChatProvider(responder=responder)


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
        yield c, csrf, db_session


async def test_graph_editing_roundtrip(ctx):
    c, csrf, db_session = ctx
    box = SecretBox(_FERNET)
    await ProviderSettingsService(db_session, box=box).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)

    # Ingest two articles sharing >= hub_threshold(=2) entities -> a "related" link a2 -> a1.
    r1 = await run_ingest(
        db_session, domain_id=dom.id, source_md="# TCP\n\nreliable delivery",
        chat=_ingest_chat("tcp", "TCP"), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    r2 = await run_ingest(
        db_session, domain_id=dom.id, source_md="# IP\n\naddressing",
        chat=_ingest_chat("ip", "IP"), embedder=emb, cfg=WikiConfig(), dim=8,
    )
    await db_session.commit()
    a1, a2 = r1.article_id, r2.article_id

    # Seed a typed parent/child link + a sourced citation to exercise tree + citation join.
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="rfc793.txt", type="md", checksum="z"
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=a1, dst_article_id=a2, type="child"
    )
    await CitationRepo(db_session).create(
        article_id=a1, source_id=src.id, quote="reliable", locator="p1"
    )
    await db_session.commit()

    # 1) Graph shows the co-occurrence + child links around a1.
    g = await c.get(f"/api/v1/graph?domain={dom.id}&root={a1}&depth=1")
    assert g.status_code == 200
    data = g.json()
    assert {n["id"] for n in data["nodes"]} == {str(a1), str(a2)}
    assert {(e["src"], e["dst"], e["type"]) for e in data["edges"]} == {
        (str(a2), str(a1), "related"),
        (str(a1), str(a2), "child"),
    }

    # 2) Article page: a1 has a backlink from a2 (related), a child link to a2, sourced citation.
    page = await c.get(f"/articles/{a1}")
    assert page.status_code == 200
    assert f'href="/articles/{a2}"' in page.text
    assert "rfc793.txt" in page.text
    assert "Backlinks" in page.text

    # 3) Sidebar tree nests a2 (child) under a1.
    assert 'class="tree"' in page.text
    assert "TCP" in page.text and "IP" in page.text

    # 4) Edit a1 (new revision) then roll back to rev 1, and confirm 409 on a stale write.
    put = await c.put(
        f"/api/v1/articles/{a1}",
        json={"title": "TCP", "markdown": "## Overview\nedited body", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert put.status_code == 200 and put.json()["current_rev"] == 2

    stale = await c.put(
        f"/api/v1/articles/{a1}",
        json={"title": "TCP", "markdown": "## Overview\nv3", "expected_rev": 1},
        headers={"x-csrf-token": csrf},
    )
    assert stale.status_code == 409  # optimistic lock holds

    rb = await c.post(
        f"/articles/{a1}/rollback", data={"rev_no": 1}, headers={"x-csrf-token": csrf}
    )
    assert rb.status_code == 204 and rb.headers.get("HX-Refresh") == "true"

    after = await c.get(f"/api/v1/articles/{a1}")
    assert after.json()["current_rev"] == 3
    assert "TCP and IP" in after.json()["html"]  # original rev-1 body restored
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/e2e/test_graph_editing_e2e.py -v`
Expected: PASS (1 test). `ensure_embedding_column` is called before `run_ingest` so the embed stage has its column.

- [ ] **Step 3: Full gate**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_graph_editing_e2e.py
git commit -m "test(e2e): graph + backlinks + edit/rollback round-trip"
```

---

## Self-review (spec coverage)

Checked against `…paw-phase-5-graph-editing-design.md`:

- **Graph read (subgraph, bounded depth, type filter; `GET /graph?domain=&root=&depth=`)** → Tasks 3, 5, 7. The endpoint also accepts `types`; depth is clamped to `GraphConfig.max_depth`. ✔ (AC-1)
- **Graph UI (full-canvas Cytoscape vendored, top controls, node click → drawer → open)** → Task 12 (+ entry points in Tasks 10/11). Data path tested at API; rendering manual per Scope decision 6. ✔ (AC-2)
- **Link-aware article page (citations→sources, backlinks, related/parent/child, revisions, `[[refs]]`)** → Tasks 6, 8, 9, 10. ✔ (AC-3)
- **Secondary sidebar parent/child tree + filter** → Tasks 4, 9, 10 (partial), 11 (domain page). ✔ (AC-4)
- **Editing / rollback / optimistic-lock 409** → Phase 1 logic reused; UI in Task 10; verified in Tasks 10 + 13. ✔ (AC-5)
- **Config (`default_depth`, link-type allowlist, per-domain overrides)** → Tasks 2, 7. ✔
- **Security (domain-scoped reads, sanitized render/summaries, rollback = new revision)** → Task 7 (auth), Tasks 8/10 (`render_markdown` + `textContent`), Phase 1 rollback. ✔
- **Tests: unit (subgraph, backlinks, tree), integration (`/graph` on seeded links, backlink reciprocity), API (graph, rollback, metadata), E2E** → Tasks 3/4/8 (unit), 5/6/9 (integration), 7/10/11/12 (API), 13 (E2E). ✔
- **Risks: Cytoscape vendored (no CDN), CSP-no-inline; cycle-safe tree** → Task 12 (vendored + `{% block scripts %}` + external `graph.js` + `textContent`), Task 4 (cycle guard + trapped-node fallback). ✔

Deviation surfaced: the backlink index (Task 1) is added rather than assumed-from-Phase-2 (Scope decision 1). No new tables. Type/method names are consistent across tasks (`SubEdge`, `GraphNode`, `LinkedArticle`, `CitationView`, `ArticleMeta`, `TreeNode`, `build_subgraph`, `build_tree`, `normalize_parent_child`, `resolve_wikilinks`, `get_meta`, `domain_tree`, `slug_map`, `config_for`, `subgraph`).
