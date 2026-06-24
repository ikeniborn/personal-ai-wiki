---
title: "Phase 10 — Apache AGE graph engine + GraphRAG retrieval"
phase: 10
status: design
date: 2026-06-24
depends_on: [2, 3, 5, 6]
review:
  spec_hash: e8888cb9f04d62ce
  last_run: 2026-06-24
  phases:
    structure:    { status: passed }
    coverage:     { status: passed }
    clarity:      { status: passed }
    consistency:  { status: passed }
  findings:
    - id: F-001
      phase: clarity
      severity: INFO
      section: "Config (LLD §10)"
      section_hash: 480491f84b00513c
      text: "Prose refers to the flag as `graph_engine == \"age\"` (In scope, Data flow), but the config field is defined as `GraphConfig.engine`. Canonical path is `graph.engine`; reconcile the `graph_engine` shorthand with the field name to avoid implying a separate `graph_engine` key."
      verdict: open
      verdict_at: null
    - id: F-002
      phase: consistency
      severity: INFO
      section: "Graph schema (per domain)"
      section_hash: b328e1eeb7bd597a
      text: "`Chunk {id, article_id, ord}` assumes a chunk-ordinal column `ord` exists on `chunks`; the source survey listed `embedding`/`tsv`/`embedding_version` but did not confirm `ord`. Verify the column name during planning (it may be `ord`, `seq`, `idx`, or absent) before projecting it."
      verdict: open
      verdict_at: null
    - id: F-003
      phase: clarity
      severity: INFO
      section: "Acceptance criteria (verifiable)"
      section_hash: 0be419e7630c96e3
      text: "Goal claims retrieval quality improves, but acceptance criteria verify the mechanism (entity-bridged neighbours returned + deterministic ranking + provenance), not a quality delta. Acceptable for a design spec; note that a quality metric (e.g. share of seed articles with no `links` edges for which entity-bridge finds neighbours) lives downstream in the plan, not here."
      verdict: open
      verdict_at: null
chain:
  intent: null
---

# Phase 10 — Apache AGE graph engine + GraphRAG retrieval

**Goal / vertical value:** turn the graph from an article-only link map traversed by a
recursive CTE into a first-class property graph (articles, entities, chunks) stored in
Apache AGE, and use it to make retrieval **entity-aware** — seed chunks reach related
material through shared concepts, not only through hand-authored `links`. The headline
deliverable is a GraphRAG retrieval path that improves answer context; everything else
(graph UI, path-finding, centrality) is deferred to fast-follow phases.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## Context — what exists today

- **Edges:** `links` table (Article→Article, typed). `entities` + `article_entities` +
  `chunk_entities` exist relationally but are **not** graph nodes — entities are only used
  for co-occurrence auto-linking at ingest.
- **Traversal A (retrieval):** `graph/traverse.py::bfs_expand()` — `WITH RECURSIVE … CYCLE`
  over `links`, outgoing-only, depth-bounded. Consumed in `harness/retrieve.py`: seed
  chunks → seed articles → BFS neighbours → summaries injected as `[related]` blocks.
- **Traversal B (UI):** `graph/subgraph.py::build_subgraph()` — fetches **all** domain edges
  and does an in-memory BFS for the Cytoscape view.
- **Stack:** async SQLAlchemy 2.0 + **asyncpg**, one lazy process-global engine
  (`db/session.py`). DB image is `pgvector/pgvector:pg16`. Services are the single commit
  boundary; repos/storage never commit (CLAUDE.md Atomicity).

## Decisions (brainstorming 2026-06-24)

| Axis | Decision | Rationale |
|------|----------|-----------|
| Node model | **Article + Entity + Chunk** as graph nodes | Full GraphRAG: concept→concept reachability through chunks/articles |
| Source of truth | **Relational = truth; AGE graph = derived projection** (incremental in-txn + rebuild job) | Nothing existing breaks; FK integrity stays in relational |
| Headline | **GraphRAG retrieval** (entity-aware expansion in `retrieve.py`) | Measurable answer-quality gain; core value of a RAG wiki |
| Rollout | **Feature flag + coexistence**, CTE path kept as fallback | AGE maturity risk; instant rollback; A/B per domain |
| Namespacing | **One AGE graph per domain** (`create_graph` per domain) | Hard isolation matches the project's per-domain RBAC/write-scope; closes cross-domain leakage and Cypher-injection blast radius by construction |
| Connection | **Single engine, `statement_cache_size=0`**, search_path via `server_settings` | Graph writes stay in the same txn as relational writes (atomicity); prepared-statement cache collides with AGE; perf cost negligible at personal/team scale |

## In scope

- **AGE infra:** custom Postgres image (`pgvector` + AGE `release/PG16/1.5.0`); migration
  enabling `CREATE EXTENSION age`; asyncpg `connect_args` (`server_settings` search_path +
  `statement_cache_size=0`) in `db/session.py`.
- **Per-domain graph lifecycle:** `graph/age/naming.py` (deterministic `domain_id` → graph
  name) and `graph/age/schema.py` (idempotent `create_graph` + vlabel/elabel + property
  indexes), bootstrapped at domain creation and in rebuild (separate commit).
- **Projection (in-txn):** `graph/age/projection.py` mirrors relational writes into the
  domain graph inside the **same** service transaction during ingest / edit / add-link /
  rollback, gated by `graph_engine == "age"`.
- **Safe Cypher layer:** `graph/age/cypher.py` — executes `SELECT * FROM cypher('g', $$…$$,
  $params) AS (…)`, binds all user-derived strings as **agtype parameters** (never
  interpolated into the body), deserializes agtype results.
- **GraphRAG retrieval:** `graph/age/query.py::graph_expand()` + an AGE branch in
  `harness/retrieve.py` that expands seed chunks via shared entities and `LINKS`, ranked,
  with provenance ("via concepts X, Y"); falls back to `bfs_expand` on flag-off or error.
- **Rebuild job:** arq `graph_rebuild(domain_id)`, domain-locked (reuses Phase-6 reindex
  job-session/locking + progress), full-rebuild semantics for MVP.
- **Config:** `GraphConfig.engine` (`cte` default), `expand_depth`, `max_entities`,
  `max_neighbors`; layered env ⊕ app_settings ⊕ domain override.

## Out of scope (deferred to fast-follow)

AGE-backed Cytoscape view + path-finding UI (Phase 11) · explicit `Entity-RELATED->Entity`
edges · centrality / hub detection · recommendations · clustering · scheduled/periodic
rebuild · delta reconciliation for deletes (MVP covers deletes only via full rebuild) ·
joining pgvector `<=>` and `cypher()` in a single SQL statement (possible, not required for
MVP). UI keeps the existing `build_subgraph` path unchanged.

## Graph schema (per domain)

Graph name: deterministic `g_<uuid_hex>` from `domain_id` (valid AGE identifier). Created
at domain creation (own commit) and in rebuild.

**Vertices** (minimal properties — large text stays relational; the graph holds keys to
join back plus light labels for traversal/display):

| Label | Properties | Source |
|-------|------------|--------|
| `Article` | `id`, `slug`, `title` | `articles` |
| `Entity` | `id`, `name`, `kind` | `entities` |
| `Chunk` | `id`, `article_id`, `ord` | `chunks` |

**Edges:**

| Label | Direction | Property | Source |
|-------|-----------|----------|--------|
| `LINKS` | Article→Article | `type` | `links.type` |
| `MENTIONS` | Article→Entity | — | `article_entities` |
| `IN_ARTICLE` | Chunk→Article | — | `chunks.article_id` |
| `CHUNK_MENTIONS` | Chunk→Entity | — | `chunk_entities` |

Concept→concept reachability needs **no** explicit Entity→Entity edge — it traverses
`Entity <-MENTIONS- Article -MENTIONS-> Entity` (or the chunk-level equivalent). This keeps
write volume down; explicit entity edges are deferred.

**Uniqueness:** AGE has no native unique constraint, so projection uses
`MERGE (a:Article {id: …})`. `schema.py` creates a btree index on each label's `id`
property (and on `Chunk.article_id`) — without it MERGE and rebuild are slow.

## Module layout (extends `graph/`, no new layering cycles)

```
src/paw/graph/age/
  __init__.py
  naming.py      # domain_id -> graph name (pure function)
  schema.py      # create_graph + create_vlabel/elabel + property indexes (idempotent DDL)
  cypher.py      # safe executor: cypher() wrapper, agtype param binding, result deserialization
  projection.py  # upsert/detach vertices+edges, called by services IN-TXN
  query.py       # read queries: graph_expand (retrieval); subgraph/shortest_path are fast-follow stubs
```

Dependencies: `graph/age → db, config` (leaf-ward). Services call `projection` on writes;
`harness/retrieve.py` and the graph service call `query`. No cycle; existing dependency
rules (LLD §1) hold.

## Data flow

### Projection (write path, same transaction)

1. **Graph bootstrap** (`create_graph` + labels + indexes) is DDL-like and has AGE's
   non-autocommit visibility caveat, so it runs at **domain creation** (its own commit) and
   in rebuild — never mid-write. By the time a write projects, labels already exist.
2. **Ingest** (`harness/ops/ingest.py`): after the relational writes for an article and
   before the service commit, when `graph_engine == "age"`, call `projection.upsert_article`
   / `upsert_chunks` / `upsert_entities` and `merge_*` edges on the **same** `AsyncSession`.
   The single `session.commit()` covers both relational and graph writes; rollback leaves no
   orphan nodes (AGE shares the transaction).
3. **Edit / add-link / rollback** (services): mirror the relational delta into the graph in
   the same transaction.
4. **Delete:** relational `ON DELETE CASCADE` does **not** cascade into AGE. Phase-5 only
   inserts links/articles today, so MVP covers deletes via full rebuild; `projection` ships
   `detach_*` helpers (used by rebuild and future delete paths). See Risks.

### GraphRAG retrieval (read path)

Flag off → unchanged: hybrid search → seed articles → `bfs_expand` → `[related]` summaries.

Flag on (`graph_engine == "age"`):

1. Hybrid search → top-N seed chunks (unchanged).
2. `graph_expand()` runs one Cypher in the domain graph, unioning two neighbour sources:
   - **Entity-bridge** (GraphRAG core): `Chunk -CHUNK_MENTIONS-> Entity <-CHUNK_MENTIONS-
     Chunk -IN_ARTICLE-> Article`, ranked by count of shared entities.
   - **Link-expand** (preserves current behaviour): `Article -LINKS-> Article` to
     `expand_depth`.
3. Returns ranked `article_id`s + provenance (`via` = shared entity names). Bounds from
   `GraphConfig` (`max_entities`, `max_neighbors`, `expand_depth`); deterministic order
   (`ORDER BY shared DESC, article_id`).
4. Result feeds the existing `fetch_summaries` → `[related]` blocks, now carrying "via
   concepts" provenance.
5. **Fallback:** flag off or any AGE error → `bfs_expand`; logged + counter. Retrieval never
   hard-fails because of the graph.

Core Cypher (parameters bound as agtype, never interpolated):

```cypher
MATCH (c:Chunk)-[:CHUNK_MENTIONS]->(e:Entity)<-[:CHUNK_MENTIONS]-(c2:Chunk)-[:IN_ARTICLE]->(a:Article)
WHERE c.id IN $seed_ids AND NOT a.id IN $seed_article_ids
RETURN a.id AS article_id, count(DISTINCT e) AS shared, collect(DISTINCT e.name)[..5] AS via
ORDER BY shared DESC, article_id
LIMIT $k
```

### Rebuild (`graph_rebuild(domain_id)`)

arq job, **domain-locked**, reusing the Phase-6 reindex job-session/locking + SSE progress.

1. `ensure_graph` + labels + indexes (idempotent).
2. MVP full rebuild: `drop_graph(cascade)` + recreate + project all rows in batches
   (articles, chunks, entities, links, mentions). Simple and correct; the domain lock
   prevents races. Delta reconciliation is fast-follow.
3. Progress reported through the existing job-progress drawer.

Triggers: enabling the flag for a domain → one rebuild backfills the existing domain (button
in domain-actions, like Phase-6 Reindex); on-demand repair. Scheduled rebuild is deferred.

## Infra

- **Docker image:** new image `FROM pgvector/pgvector:pg16`, build AGE `release/PG16/1.5.0`
  (`make && make install`). `compose` and the testcontainers image both switch to it. The
  Dockerfile lives in-repo.
- **Migration:** Alembic `CREATE EXTENSION IF NOT EXISTS age;`. Per-domain `create_graph` is
  runtime (domains are dynamic), not a migration step.
- **Engine `connect_args`** (`db/session.py`, the lazy engine singleton):
  `server_settings={'search_path': 'ag_catalog,"$user",public'}` and
  `statement_cache_size=0`. Setting search_path via `server_settings` applies at connection
  startup, avoiding AGE's "first cypher call" parse-hook bug. The `wired_settings` fixture
  already resets the cached engine; mirror that for the new connect args.

## Config (LLD §10)

```python
class GraphConfig(BaseModel):
    engine: Literal["cte", "age"] = "cte"   # default OFF → zero regression until enabled
    expand_depth: int = 1                   # LINKS hops
    max_entities: int = 8                   # AGE-only: entity-bridge cap
    max_neighbors: int = 12                 # AGE-only: neighbour cap
```

Layered env ⊕ app_settings ⊕ `domains.config`; per-domain override lets AGE be enabled on
one domain while others stay on CTE.

## Security

- **Cypher injection:** all user-derived strings (titles, entity names) bound as agtype
  parameters; the cypher body is a fixed dollar-quoted literal. UUIDs validated as `uuid`.
- **Domain isolation:** one graph per domain means there is no `domain_id` filter to forget —
  a domain's Cypher cannot reach another domain's nodes.
- **DDL visibility:** `create_graph` / label creation committed separately (domain create /
  rebuild), never mid-write, avoiding AGE's non-autocommit visibility pitfall.
- **Audit:** graph writes triggered by harness tools are already audited; in-service
  projection is internal and adds no new external surface.
- **Degradation:** any AGE failure degrades to the CTE path; retrieval and the API never
  surface a graph-engine error to the caller.

## Acceptance criteria (verifiable)

1. The custom image boots with both `age` and `vector`; `CREATE EXTENSION age` and a
   per-domain `create_graph` succeed.
2. Ingest with the flag on projects Article/Chunk/Entity nodes + edges into the domain graph
   in the **same** transaction; a forced rollback leaves no orphan graph nodes.
3. `engine=age` retrieval returns entity-bridged neighbours with deterministic ranking and
   attaches "via concepts" provenance.
4. **Isolation:** a Cypher query on domain A returns zero domain-B nodes (cross-domain seed
   test).
5. **Injection:** an article titled `$$ ) MATCH (x) DETACH DELETE x //` cannot alter the
   executed query (parameter-bound) — verified by test.
6. `engine=cte` (default) → retrieval is identical to pre-Phase-10 behaviour (regression
   guard).
7. `graph_rebuild(domain)` reconstructs the full graph idempotently; enabling the flag then
   running rebuild backfills a pre-existing domain.

## Tests (LLD §11)

- **Unit:** `naming` (domain_id → graph name); agtype parameter builder (injection safety —
  a malicious title cannot break out of the body); retrieval neighbour-merge/ranking logic.
- **Integration (testcontainers):** the test PG image must include AGE → switch to the custom
  image (a real, called-out cost). Project a seeded corpus → `graph_expand` returns the
  expected entity-bridged neighbours; cross-domain isolation; rebuild idempotency.
- **API (httpx):** retrieval/query endpoints behave identically with `engine=cte`; with
  `engine=age` related context carries provenance.
- **E2E:** ingest a small corpus (Phase 2) → enable the flag → `graph_rebuild` →
  query shows entity-bridged related context; flip back to `cte` → identical to baseline.

## Risks / notes

- **asyncpg + AGE friction (highest risk).** No official asyncpg driver; prepared-statement
  cache collides with AGE (`unhandled cypher(cstring) function call`). Mitigated by
  `statement_cache_size=0` + `server_settings` search_path. The single-engine decision keeps
  graph writes in the same transaction at a negligible prepared-cache perf cost.
- **Custom image maintenance.** The DB image is now repo-owned (pgvector pin + AGE branch);
  it must track Postgres minor upgrades and AGE releases. Both runtime and CI/testcontainers
  use it.
- **Delete reconciliation gap.** MVP relies on full rebuild for deletes; `detach_*` helpers
  ship but incremental delete wiring is fast-follow. Until then, deleting an article without
  a rebuild can leave stale graph nodes (read path tolerates this — it joins back to
  relational and drops unknown ids).
- **MERGE without property indexes is slow.** `schema.py` must create the `id`/`article_id`
  property indexes before bulk projection, or rebuild on a large domain degrades.
- **Scope discipline.** AGE makes many graph features cheap (paths, centrality, UI); they are
  explicitly deferred so Phase 10 stays a single, testable retrieval-quality increment.
