# Knowledge Graph

## Overview

The `graph` package models articles as nodes joined by typed `Link` edges. Pure builders — `subgraph.py` (depth-bounded undirected BFS), `traverse.py` (SQL recursive outgoing BFS) and `tree.py` (parent/child forest) — operate on edges fetched by `graph/repo.py::GraphRepo` and `db/repos/links.py::LinkRepo` (links + backlinks). `services/graph.py::GraphService` applies per-domain config and `GET /api/v1/graph` returns the JSON nodes/edges payload. Used for navigation and [[vector#Hybrid search]] expansion.

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
