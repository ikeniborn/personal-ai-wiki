---
title: "Phase 5 — Graph + editing (link-aware UI)"
phase: 5
status: design
date: 2026-06-22
depends_on: [2]
review:
  spec_hash: 22bb325bfbd2b06a
  last_run: 2026-06-23
  phases:
    structure:    { status: passed }
    coverage:     { status: passed }
    clarity:      { status: passed }
    consistency:  { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: INFO
      section: "Data model touched"
      section_hash: c8b5b6c357398d00
      text: "`entities` is listed as a table read (and the Goal mentions building on link/entity/citation data), but no In-scope feature, key flow, or acceptance criterion consumes or displays entities. The article-page section enumerates Citations/Backlinks/Related/Revisions only — the `entities` read is declared without a feature using it."
      verdict: open
      verdict_at: null
    - id: F-002
      phase: coverage
      severity: INFO
      section: "In scope"
      section_hash: f142e058afff033a
      text: "In-scope requires `[[refs]]` render as in-wiki links (line 28), but no acceptance criterion verifies it. AC#3 covers backlinks/related/citations/revisions and is silent on `[[refs]]` rendering — requirement without a verifying criterion."
      verdict: open
      verdict_at: null
    - id: F-003
      phase: clarity
      severity: INFO
      section: "In scope"
      section_hash: f142e058afff033a
      text: "Terminology: the graph link-type filter allowlist uses `references` (line 23), while the article page and AC#3 use the separate `citations` table (lines 27-28, 41, 69). Spec reads `links` for edges and `citations` separately, so link-type `references` is distinct from `citations` — but this is never stated, leaving the references/citations relationship ambiguous."
      verdict: open
      verdict_at: null
    - id: F-004
      phase: clarity
      severity: INFO
      section: "Config (LLD §10)"
      section_hash: 382a414977938b9a
      text: "Config names `bfs_depth`/graph default depth and the depth slider, and AC#1 references 'the depth bound', but no default value or maximum-depth ceiling is specified anywhere. The depth bound is delegated to config without a stated default or cap — requirement without explicit DoD/value."
      verdict: open
      verdict_at: null
chain:
  intent: null
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
