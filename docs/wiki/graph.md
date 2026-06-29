# Knowledge Graph

## Overview

The `graph` package models articles as nodes joined by typed `Link` edges. Pure builders — `subgraph.py` (depth-bounded undirected BFS), `traverse.py` (SQL recursive outgoing BFS) and `tree.py` (parent/child forest) — operate on edges fetched by `graph/repo.py::GraphRepo` and `db/repos/links.py::LinkRepo` (links + backlinks). `services/graph.py::GraphService` applies per-domain config and `GET /api/v1/graph` returns the JSON nodes/edges payload. Used for navigation and [[vector#Hybrid search]] expansion. The optional `graph/age/` subpackage adds an Apache AGE property graph (Article + Entity + Chunk nodes) used for entity-aware GraphRAG retrieval behind a per-domain `GraphConfig.engine` flag (`cte` default, `age` opt-in).

## Links

A `Link` (see [[db#Models and tables]]) is a typed, directed edge `src_article_id → dst_article_id` within one `domain_id`. `LinkRepo` reads them; `GraphRepo.link` writes them idempotently. Typical types: `related`, `parent`, `child`. Entities co-occurring across articles drive automatic linking.

- `GraphRepo.link(...)` rejects self-links, skips duplicates `(src, dst, type)`, else `add` + `flush`; returns whether a row was created.
- `LinkRepo.backlinks(article_id)` and `LinkRepo.outgoing(article_id)` return `LinkedArticle` rows (incoming / outgoing) ordered by `(type, title)`.
- `GraphRepo.cooccurrence_targets(...)` uses `EntityRepo.shared_with` to find articles sharing ≥ `threshold` entities — candidate `related` edges. See [[db#Models and tables]].

## Subgraph

`subgraph.py::build_subgraph` is a pure, undirected, cycle-safe, depth-bounded BFS from a `root` over a list of `SubEdge(src, dst, type)`. It returns the reachable `node_ids` (always including the root) and the induced edges (both endpoints reachable).

- `types` filters edges *before* traversal: `None` = all types, empty set = none (root only).
- Builds an undirected adjacency map, expands the frontier `max(0, depth)` times, stops early when a level adds nothing.
- `GraphRepo.subgraph(...)` loads `_domain_edges`, runs the builder, then `_briefs` hydrates `GraphNode(id, slug, title, summary)` rows. Surfaced via [[services#GraphService]] and [[api#Graph router]].

## Traverse

`traverse.py::bfs_expand` is the outgoing-only, depth-bounded graph expansion run in SQL — a `WITH RECURSIVE` BFS over `links`, cycle-safe via Postgres `CYCLE ... SET` (PG14+). Unlike `build_subgraph` it follows only `src → dst` direction and returns just the reachable article ids.

- Seeds with `unnest(:seed)` at depth 0, joins `links` while `depth < :max_depth`, `SELECT DISTINCT article_id`.
- Empty seed short-circuits to `[]`; `:seed` is bound as a Postgres `ARRAY(UUID)`.
- Called by `harness/retrieve.py` to expand from the passages returned by [[vector#Hybrid search]], pulling in graph-neighbour articles before the [[harness#Retrieve]] step.

## Tree

`tree.py` builds a navigable parent/child forest of `TreeNode(id, slug, title, children)` from typed links — the hierarchy view. It is a pure builder fed by `LinkRepo.parent_child_raw` and surfaced through `services/articles.py`.

- `normalize_parent_child(typed_edges)` folds `child` links to `(src, dst)` and `parent` links to `(dst, src)`, deduping into `(parent_id, child_id)` pairs.
- `build_tree(nodes, parent_child)` attaches children sorted by title, skips self-loops, and treats any node that is never a child as a root.
- It is cycle-safe: nodes trapped in a cycle are never visited as a root, so they are surfaced afterward to avoid being dropped.

## AGE graph engine

The `graph/age/` subpackage projects the relational truth (articles, chunks, entities, links) into an Apache AGE property graph so retrieval can reach related material through shared concepts, not only hand-authored links. Relational tables stay the source of truth; the AGE graph is a derived projection. It is gated by `GraphConfig.engine` (`cte` default → zero regression; `age` opt-in per domain — see [[providers#Config models]]). One AGE graph per domain gives hard isolation: a domain's Cypher cannot reach another domain's nodes.

- `naming.py::graph_name(domain_id)` → deterministic `g_<32 hex>`; `assert_graph_name` validates against `^g_[0-9a-f]{32}$` — the injection guard before any graph name is interpolated into SQL.
- `cypher.py` is the only Cypher executor: `run_cypher`/`exec_cypher` run `SELECT * FROM cypher('<graph>', $cy$<body>$cy$, CAST(:p AS agtype)) AS (...)`. The body is always a fixed literal; every user value goes through the agtype `parameters` argument (`agtype_params` → `json.dumps`), never interpolated — see [[security#Cypher injection]]. `_escape_body` backslash-escapes `[:LABEL]` edge syntax so SQLAlchemy's `text()` does not mistake it for a bind.
- `schema.py::ensure_graph` idempotently creates the graph, the `Article`/`Entity`/`Chunk` vlabels, the `LINKS`/`MENTIONS`/`IN_ARTICLE`/`CHUNK_MENTIONS` elabels, and btree property indexes on each `id` (+ `Chunk.article_id`). It is DDL-like, so it runs in its own commit at domain creation and in rebuild — never mid-write. `drop_graph` tears it down.
- `projection.py::project_article` mirrors one article's relational rows into the graph on the caller's `AsyncSession` (it never commits — the service owns the single commit, see [[services#The commit-boundary rule]]). It runs in-transaction during ingest/edit/rollback, so a rollback leaves no orphan nodes (AGE shares the transaction). `detach_article` and `merge_link` support rebuild and future delete wiring.
- Requires the custom Postgres image (`pgvector` + AGE) and asyncpg `connect_args` (`search_path=ag_catalog,...` + `statement_cache_size=0`) — see [[db#Async sessions and singletons]].

## GraphRAG retrieval

`graph/age/query.py::graph_expand` is the entity-aware expansion that replaces `bfs_expand` when `engine == "age"`. It unions two neighbour sources in the domain graph and returns ranked `Neighbor(article_id, shared, via)` rows with concept provenance, so a seed chunk reaches related articles even when no `links` edge exists.

- **Entity-bridge** (GraphRAG core): `Chunk -CHUNK_MENTIONS-> Entity <-CHUNK_MENTIONS- Chunk -IN_ARTICLE-> Article`, ranked by count of shared entities; `via` carries up to 5 shared concept names.
- **Link-expand**: `Article -LINKS-> Article` to `expand_depth` (a validated int inlined into the body), preserving the old behaviour.
- `_merge_neighbors` dedups by `article_id`, orders by `shared DESC, article_id`, caps at `max_neighbors`; a link-only neighbour gets `shared=0`. Bounds come from `GraphConfig`.
- `harness/retrieve.py` takes a `graph_cfg` resolved by the caller services (`services/query.py`, `services/chat.py` via `GraphService.config_for`) — never imported into `harness` to avoid a layering cycle. On `engine == "age"` it calls `graph_expand`; ANY error logs and falls back to `bfs_expand`, so retrieval never hard-fails because of the graph. The `[related]` blocks then render `(via concepts: X, Y)` provenance. See [[harness#Retrieve]].

## Graph rebuild job

`jobs/tasks.py::graph_rebuild` is the arq job that fully rebuilds a domain's AGE graph — used to backfill an existing domain when the flag is first enabled, or to repair drift (MVP covers deletes only via full rebuild). It mirrors the Phase-6 reindex job (per-domain lock, two sessions, SSE progress, cancel/error rollback). See [[jobs#Worker jobs]].

- `_rebuild_domain_graph(data_s, domain_id, *, on_batch)` is the core: `drop_graph` + `ensure_graph` + `project_article` for every article in batches. The caller owns the commit; it is idempotent (a double rebuild leaves exactly one node per article).
- Triggered admin-only (no per-domain enable flag) via `MaintenanceService.start_graph_rebuild` → `POST /domains/{id}/rebuild-graph` and the "Rebuild graph" button in `domain.html`, reusing the job-progress drawer.
