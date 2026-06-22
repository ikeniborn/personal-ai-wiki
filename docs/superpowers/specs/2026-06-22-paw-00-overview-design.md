---
review:
  spec_hash: 9922cdfa83620575
  last_run: 2026-06-22
  phases:
    structure:    { status: passed }
    coverage:     { status: passed }
    clarity:      { status: passed }
    consistency:  { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: INFO
      section: "5. Phase map"
      section_hash: 2069ae4339304d6a
      text: "Suggested order line says phases 4/5/6 'in any order' depend on 2/3, but the table lists Phase 5 depends on 2 only (not 3) and Phase 6 on 2,3. Wording 'all depend on 2/3' is slightly loose vs the per-row Depends-on column; not a contradiction, but reconcile for precision."
      verdict: accepted
      verdict_at: 2026-06-22
    - id: F-002
      phase: clarity
      severity: INFO
      section: "7. Cross-cutting conventions"
      section_hash: 7699dcbe866760de
      text: "Conventions are stated as bindings without explicit per-item DoD; acceptable for an index/overview spec that delegates DoD to per-phase specs, but note that acceptance criteria live downstream (§9), not here."
      verdict: accepted
      verdict_at: 2026-06-22
chain:
  intent: null
---

# Personal AI Wiki — Implementation Specs: Overview & Phase Map

**Date:** 2026-06-22
**Status:** Design (approved phasing)
**Derived from:** `docs/reports/lld-personal-ai-wiki.html` (LLD), `docs/reports/hld-personal-ai-wiki.html` (HLD)

This is the master index for implementation. It does **not** restate the LLD — it
references LLD sections (`§N`) and splits the build into **9 vertical phases**. Each
phase has its own design spec in this directory, and each spec feeds the
`writing-plans` skill independently to produce an implementation plan.

---

## 1. System summary

Personal AI Wiki (`paw`) is a self-hosted, team-scale RAG wiki. Users upload sources
(md/pdf/docx/html/epub/url/images); an LLM harness extracts topics and **generates
wiki articles** with entities, links, and citations. Articles are chunked and embedded
for **hybrid retrieval** (vector + FTS + graph BFS). Users **query** (single-shot,
cached) or **chat** (threaded) against a domain, get cited answers, and can **edit**
articles with revision history. A **graph** view exposes the link structure. Wiki
quality is maintained by **lint/fix/format/reindex** jobs. An **MCP** endpoint exposes
read-only search to external clients (IDEs/agents).

## 2. Stack (LLD header)

Python 3.12 · `uv` · FastAPI (async) · Jinja2 + HTMX + Cytoscape.js (vendored) ·
PostgreSQL 16 + `pgvector` · Redis + `arq` · OpenAI-compatible LLM (base_url/key set in
admin UI) · MCP Python SDK (Streamable HTTP) · Docker Compose + Traefik.

## 3. Module layout & dependency rules (LLD §1)

Package `paw`, src-layout, one image / two processes (`api` uvicorn, `worker` arq), MCP
mounted into the app. Dependency rules (no cycles):

- `api/web/mcp → services + retrieval`
- `services → db.repos, storage, vector, graph, jobs, harness`
- `harness → providers`, and storage/vector/graph **via tools**
- `ingest/vector/graph/storage/providers → db`
- `db, config` — leaves

Each phase spec lists exactly which modules it creates or touches. The full target tree
is in LLD §1.

## 4. Phasing strategy

**Walking skeleton + vertical growth.** Phase 1 is a thin end-to-end slice (compose
boots, migrations run, auth works, a domain holds a manually-created article that
renders). Every later phase adds one real capability end-to-end (ingest → retrieval →
chat → graph/editing → maintenance → cache → MCP → ops). Rationale: early integration,
each phase is independently testable and deployable, smallest blast radius per increment.

Phases are **not** LLD sections — LLD `§1–§13` are design cross-sections, not a build
order. Each phase cuts vertically through several LLD sections.

## 5. Phase map

| # | Phase | Vertical value | Depends on | Spec |
|---|-------|----------------|------------|------|
| 1 | Skeleton | End-to-end without LLM | — | `…paw-phase-1-skeleton-design.md` |
| 2 | Ingest (LLM generation) | Source → AI article + chunks/embeddings | 1 | `…paw-phase-2-ingest-design.md` |
| 3 | Retrieval / Query (RAG) | Question → cited answer | 2 | `…paw-phase-3-retrieval-query-design.md` |
| 4 | Chat + history | Multi-turn dialogue | 3 | `…paw-phase-4-chat-design.md` |
| 5 | Graph + editing | Edit wiki + navigate links | 2 | `…paw-phase-5-graph-editing-design.md` |
| 6 | Maintenance | Corpus quality (lint/fix/format/reindex) | 2, 3 | `…paw-phase-6-maintenance-design.md` |
| 7 | Query-cache + suggest | LLM/retrieval savings, FAQ | 3, 6 | `…paw-phase-7-query-cache-design.md` |
| 8 | MCP server | External clients (IDE/agents) | 3 | `…paw-phase-8-mcp-design.md` |
| 9 | Ops + hardening | Production readiness | all | `…paw-phase-9-ops-hardening-design.md` |

Suggested order: 1 → 2 → 3 → (4, 5, 6 in any order, all depend on 2/3) → 7 → 8 → 9.
Phases 4/5/6 are parallelizable once 3 lands. Phase 9 is last (hardens everything).

## 6. Global UI decisions (visual-companion session, 2026-06-22)

These bind every phase that ships UI. Rendered with Jinja2 + HTMX; assets vendored
locally (no CDN); CSP without inline-script; markdown via `mistune` → sanitized via
`nh3` allowlist. Light/dark theme is built in (header toggle).

| Concern | Decision |
|---------|----------|
| **Global frame** | **Double sidebar (wiki-style).** Narrow global rail (icons: 🏠 domains · 📚 articles · 💬 chat · 🕸 graph · ⚙ settings) + per-domain secondary sidebar + content. |
| **Domain landing** | Renders the domain **index article**; secondary sidebar shows an **article tree** grouped by parent/child links; Ingest/Lint/Format actions in a content-header menu. |
| **Article page** | Read render + **metadata as sections below** the article (Citations/Sources · Backlinks/Related · Revisions). Editing via **Edit/Preview tabs** (not side-by-side). Optimistic lock → **409 → "reload" banner**. Rollback action. |
| **Query** | Dedicated search screen: query box with **as-you-type suggestions** (team FAQ), streamed answer (SSE), source chips, **stale badge + Refresh** button. |
| **Chat** | Separate messenger screen: secondary sidebar = **session history** (by `last_active_at`, deletable), turn stream, input. Only Query is cached, not Chat. |
| **Graph** | **Full-canvas** Cytoscape + thin top controls (root selector, depth slider, link-type filter); node click → **slide-in drawer** with article preview + open. |
| **Settings (admin)** | **Single page**, section cards + anchor TOC: Connection · Languages · Wiki-defaults · Users · API-keys. Changing embedding `dim` → warning ("ALTER + reindex"). |
| **Jobs progress** | Inline **drawer** (progress bar + live log via SSE + cancel) launched from the action; full-page `/jobs/{id}` also available. |
| **First run** | Step-by-step **setup wizard** (admin user + connection + models + embedding dim). |
| **UI i18n** | `ui_language` (RU/EN) independent of content/reasoning languages. |

## 7. Cross-cutting conventions

These hold across all phases; each phase applies the slice relevant to it.

- **Config layering (LLD §10):** `env (config.py)` ⊕ `global-defaults (app_settings, DB)`
  ⊕ `domain-override (domains.config)` ⊕ `per-user (users.chat_prefs)`. Infra/secrets in
  env; tunables in admin UI.
- **Secrets:** passwords `argon2`; API keys stored hashed (prefix + sha256, scopes,
  revoke); LLM provider key and Langfuse secret **encrypted at rest** (Fernet, key from
  env) and never placed in agent context.
- **API conventions (LLD §8):** server-side sessions in Redis (cookie `SameSite=Lax`) or
  API-key `Bearer paw_<prefix>.<secret>`; RBAC `require_role()`; CSRF double-submit
  (api-key exempt); errors RFC 9457 `problem+json`; pagination cursor/keyset; SSE for
  streamed answers and job progress.
- **Harness safety (LLD §4):** tool-allowlist per operation; write-scope by domain;
  schema-validated output before any write; sources and tool results wrapped as
  "data, not instructions"; per-op limits (`max_steps`, token-budget, `max_writes`,
  loop-detection); every tool call → `audit_log`.
- **Embedding dim:** fixed `vector(dim)` chosen at setup, applied by a managed migration;
  later changes are a managed `ALTER + HNSW rebuild + reindex`, never on the fly.
- **Testing (LLD §11):** unit (chunking, RRF, BFS, sanitize, provider parse) · integration
  (testcontainers PG+pgvector, Redis; stub-LLM) · API (httpx: auth/RBAC/CSRF/pagination/
  errors) · E2E (fixture sources → ingest → query). CI: `ruff` + `mypy` + `pytest`.
- **Observability:** full stack lands in Phase 9, but counters/timers are added at the
  point each operation is built (Phase 9 wires the exporter + dashboards, not the
  instrumentation calls).

## 8. Deferred (LLD §13 backlog / out of v1)

Backlog: rate limiting, idempotency keys, soft-delete, reranking, resumable ingest,
graph pagination perf, scheduled jobs (cron lint/reindex/GC). Out of v1: per-domain ACL,
config hot-reload, webhooks, quality eval harness. No phase implements these; listed so
specs can stub seams where cheap.

## 9. How to consume

Each phase spec is self-contained and ready for `writing-plans`. Recommended flow:
take Phase 1 spec → `writing-plans` → implement → verify acceptance → next phase.
Specs reference the LLD for full DDL/contracts/pipelines; they do not duplicate them.
