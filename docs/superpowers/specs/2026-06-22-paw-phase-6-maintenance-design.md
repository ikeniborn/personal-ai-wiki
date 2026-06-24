---
title: "Phase 6 — Maintenance (lint / fix / format / reindex)"
phase: 6
status: design
date: 2026-06-22
depends_on: [2, 3]
chain:
  intent: null
review:
  spec_hash: 641de51a9bd13d23
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
      section: "In scope"
      section_hash: 13d2af362b217df2
      text: "Lint is specified as a harness op ('harness/ops/lint.py + prompt; read-only tools; the report_issue collect tool is now active'), but the implementation plan realises Lint as deterministic detectors with the LLM/report_issue off the critical path. The LLM-driven-lint sub-requirement maps to no task."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Accepted (plan Scope decision 1). Every Lint acceptance criterion and every listed Lint unit test (broken-ref, orphan, duplicate-entity, stale) is deterministic; pure detectors satisfy them fully and reproducibly. The report_issue collect tool (wired in Phase 4) stays available and untouched — it is simply not on Lint's critical path. Same pure-builder choice Phase 5 made for the graph."
    - id: F-002
      phase: coverage
      severity: INFO
      section: "In scope"
      section_hash: 13d2af362b217df2
      text: "Lint lists 'duplicate entities (graph + db)' but the plan detects duplicates only in the entities (db) table; there is no separate graph-side entity dedup."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Accepted (plan Scope decision 3). Entities live only in the db in this phase (the graph stores article links, not entity nodes), so 'graph + db' duplicate detection reduces to db detection. Lint reports the duplicate (acceptance criterion 1); the fix (entity merge) is deferred."
    - id: F-003
      phase: clarity
      severity: INFO
      section: "Config (LLD §10)"
      section_hash: 1b02a994bb46d266
      text: "'lint thresholds (orphan/stale definitions)' defers the precise definitions of 'stale' and 'orphan' to config without pinning a DoD in the spec body."
      verdict: accepted
      verdict_at: 2026-06-23
      resolution: "Accepted. Acceptance criterion 1 stays verifiable (plant a stale/orphan article -> Lint reports it); the plan pins concrete defaults (stale_days=180, orphan = zero in/out links) in MaintenanceConfig, tunable per-domain by design."
---

# Phase 6 — Maintenance (lint / fix / format / reindex)

**Goal / vertical value:** keep the corpus healthy. Lint scans a domain (no writes) and
reports issues; Fix applies LLM-proposed revisions per issue; Format normalizes prose
without changing facts; Reindex re-embeds chunks under a new embedding version. All run as
domain-locked jobs with live progress.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## In scope

- **Lint op (LLD §4/§12):** `harness/ops/lint.py` + prompt; read-only tools; the
  `report_issue(article, type, detail, fix)` collect tool is now active. Detects broken
  `[[refs]]`, orphan articles, stale articles, duplicate entities (graph + db) →
  `LintResult{issues[]}`. **No writes.**
- **Fix op (LLD §4/§12):** `harness/ops/fix.py` + prompt; per issue → Chat proposes a fix
  → schema validation → `upsert_article` revision (origin=`ai`) + `add_link`. Writes.
- **Format op (LLD §4):** `harness/ops/format.py` + prompt; reformat/normalize **without
  changing facts** → revision (origin=`ai`).
- **Reindex (LLD §6):** `vector/reindex.py` — chunks with `embedding_version != current`
  re-embedded in batches; search already filters `current` (Phase 3). Also the path run
  after an embedding-dim change (Phase 2 managed migration).
- **Jobs (LLD §7):** `jobs/tasks.py` += `lint_domain`, `fix_issues`, `format_articles`,
  `reindex_domain`; all under the per-domain job-lock; progress via the Phase 2 pub/sub +
  SSE infrastructure.
- **API (LLD §8):** `POST /domains/{id}/lint` · `/format` · `/reindex` → `job_id`;
  `POST /domains/{id}/fix` `{issue_ids}` → `job_id`.
- **Web UI:** domain-page actions Lint/Format/Reindex + job drawer; **Lint results** view
  (issues list) → select issues → Fix.

## Out of scope (deferred)

Query-cache stale-marking on write (Phase 7 — the write path here exposes the **seam** but
the cache table does not exist yet) · scheduled cron lint/reindex/GC (backlog) · MCP
(Phase 8).

## Data model touched

Reads/writes `articles`/`article_revisions`, `links`, `entities`, `chunks`
(`embedding_version`), `jobs`. No schema changes. Note: the "mark dependent `query_cache`
stale" step is wired in Phase 7 when `query_cache` exists; in this phase the write path
calls a no-op stale hook (seam).

## Key flows (LLD §12)

- **Lint:** lock + claim → read-only domain walk → `report_issue` collect → `LintResult`
  (no write).
- **Fix:** per issue → Chat → validate → revision (+ link). Loop over issues.
- **Format:** per article → Chat (facts unchanged) → revision.
- **Reindex:** batch re-embed chunks `!= current` → flip to `current`; search uses new
  version.

## Config (LLD §10)

`enabled_ops`, agent limits per op, batch sizes for reindex, lint thresholds (orphan/stale
definitions). Per-domain overrides.

## Security

Fix/Format are write ops → write-scope by domain, schema validation before write, audit
each tool call, domain job-lock prevents concurrent writers. Lint is read-only.

## Acceptance criteria (verifiable)

1. On a corpus with planted defects (a broken `[[ref]]`, an orphan article, a stale
   article, a duplicate entity), Lint reports each as an issue and writes nothing.
2. Fix on selected issues writes new revisions (origin=`ai`) that resolve them; a re-run of
   Lint shows them gone.
3. Format produces a revision whose facts/entities/citations are unchanged (verified by
   comparing extracted entities/citations before/after) but formatting differs.
4. Reindex re-embeds chunks to a new `embedding_version`; subsequent search returns results
   and ignores the old version.
5. All four run as domain-locked jobs with streaming progress and cooperative cancel.

## Tests (LLD §11)

- **Unit:** lint detectors (broken-ref, orphan, duplicate-entity, stale); format-invariance
  check (entities/citations stable); reindex batch planner.
- **Integration (testcontainers + stub-LLM):** fix writes resolving revision; reindex flips
  version and search filters it; domain lock during a maintenance job.
- **API (httpx):** lint→job→issues; fix with issue_ids; reindex job.
- **E2E:** plant issue → lint → fix → lint clean.

## Risks / notes

- Keep the write path's stale-hook a real seam (callable) so Phase 7 only has to implement
  it, not refactor the writers.
- Format must be conservative — prefer prompt constraints + a post-write entity/citation
  diff guard to catch fact drift.
