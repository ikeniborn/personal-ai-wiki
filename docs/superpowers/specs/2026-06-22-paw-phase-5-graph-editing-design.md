---
title: "Phase 5 — Graph + editing (link-aware UI)"
phase: 5
status: design
date: 2026-06-22
depends_on: [2]
---

# Phase 5 — Graph + editing (link-aware UI)

**Goal / vertical value:** navigate the wiki by its link structure. A full-canvas graph
shows the subgraph around any article; the article page exposes real backlinks, related
links, citations, and revisions; the secondary sidebar becomes a parent/child tree. Build
on the link/entity/citation data that Phase 2 produces.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## In scope

- **Graph read (LLD §6/§9):** `graph/repo.py` subgraph query around a root, bounded depth,
  filtered by link type; `GET /graph?domain=&root=&depth=` returning nodes + typed edges.
- **Graph UI (frame C, 🕸):** **full-canvas** Cytoscape (vendored) + thin top controls
  (root selector, depth slider, link-type filter over `related/parent/child/references/
  depends_on`); node click → **slide-in drawer** (article summary + links + "open").
- **Link-aware article page (LLD §9):** populate the metadata sections that were
  placeholders in Phase 1 — **Citations/Sources** (via `citations`→`sources`),
  **Backlinks** (`links` by `dst_article_id`), **Related/parent/child** (`links` by type),
  **Revisions** (`article_revisions` + rollback). `[[refs]]` render as in-wiki links.
- **Secondary sidebar tree (LLD §9):** replace Phase 1's flat article list with a
  **parent/child tree** built from `links` (type `parent`/`child`), with a filter box.
- **Editing:** reuse Phase 1 Edit/Preview tabs + optimistic-lock 409 + rollback; ensure
  edits that change links/citations are reflected in the metadata sections and graph.

## Out of scope (deferred)

Lint/fix that *modify* the graph (Phase 6) · query-cache stale-on-edit (Phase 7) · graph
pagination perf (backlog). No new tables.

## Data model touched

Reads `links` (+ `links(dst_article_id)` backlink index), `citations`, `entities`,
`article_revisions` from Phase 2. No schema changes.

## Key flows

- **Graph browse:** pick root + depth + type filter → subgraph → click node → drawer →
  open article.
- **Article read:** render + backlinks/related/citations/revisions sections from live data;
  edit → new revision → sections + graph update on reload.

## Config (LLD §10)

`bfs_depth`/graph default depth, link-type allowlist (display + filter). Per-domain
overrides.

## Security

Read endpoints scoped to the caller's domain access; rendered article + summaries stay
sanitized (`nh3`). Rollback writes a new revision (origin preserved/audited), not a
destructive overwrite.

## Acceptance criteria (verifiable)

1. `GET /graph` returns the subgraph around a root within the depth bound, edges carry
   their type; the type filter removes filtered edges.
2. Graph UI renders the subgraph; depth slider re-queries; node click opens a drawer with
   summary + links; "open" navigates to the article.
3. Article page shows real backlinks (reverse of an outgoing link), related/parent/child
   links, citations linking to their sources, and the revision list.
4. Secondary sidebar shows a parent/child tree; the filter narrows it.
5. Rollback restores a prior revision as a new revision; optimistic-lock 409 still holds.

## Tests (LLD §11)

- **Unit:** subgraph builder (depth + type filter), backlinks query, parent/child tree
  builder from links.
- **Integration (testcontainers):** `/graph` returns expected nodes/edges on a seeded
  link set; backlinks reciprocity.
- **API (httpx):** graph endpoint, rollback, article metadata payloads.
- **E2E:** ingest a small corpus (Phase 2) → graph shows links → article shows backlinks →
  edit + rollback round-trip.

## Risks / notes

- Cytoscape is vendored (no CDN), consistent with CSP-no-inline (LLD §9).
- Tree from parent/child links can have multiple roots/cycles — render defensively
  (the BFS/tree builder must be cycle-safe like LLD §6's CYCLE guard).
