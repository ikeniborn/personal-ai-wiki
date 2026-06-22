---
title: "Phase 9 — Ops + hardening"
phase: 9
status: design
date: 2026-06-22
depends_on: [1, 2, 3, 4, 5, 6, 7, 8]
---

# Phase 9 — Ops + hardening

**Goal / vertical value:** make the system production-ready — observability, backups, full
security hardening, the remaining loaders + bulk upload, admin UI polish, and UI i18n.
Hardens everything built in Phases 1–8.

See `…paw-00-overview-design.md`. References point into LLD (`§5/§9/§11`).

## In scope

- **Observability — metrics (LLD §11):** `prometheus-client` `/metrics` on api + worker.
  api = RED by **route-template** (rate/errors/latency), in-flight, active SSE; worker =
  arq job by type/status, duration histogram, queue depth, retries, dead-letter, job-lock
  wait. Domain counters: articles/chunks per ingest, embeddings generated, **tokens in/out
  + cost** per op, LLM latency/errors, cache hit-rate. `/health` (liveness/readiness)
  **separate** from `/metrics`. Wire the instrumentation calls added incrementally in
  earlier phases.
- **Observability — collection (LLD §11):** exporters `postgres_exporter`,
  `redis_exporter`, Traefik built-in Prometheus, `cAdvisor`; **Prometheus + Grafana** as an
  **opt-in** compose profile `observability` (vendored dashboards) or external scrape.
  Label cardinality bounded (route-template, limited `domain_id`).
- **Observability — Langfuse (LLD §11):** external (not deployed); app holds the client
  only. `host` + public/secret keys in admin `app_settings` (secret Fernet-encrypted),
  **off by default**. Integration point = harness loop: op = trace, each LLM call =
  generation-span (model, tokens, latency, cost), tool-call = span; `langfuse.openai`
  wrapper; `trace_id = job_id/request_id`; metadata `domain_id` + `prompt_version`;
  non-blocking batch flush (fire-and-forget); optional input redaction/sampling.
- **Backups (LLD §11):** scheduled `pg_dump` (cron/sidecar) + retention + documented
  restore procedure.
- **Security hardening (LLD §11):** full uploads (magic-byte sniff, whitelist, max-size,
  **anti-zip-bomb**); **SSRF** for the url loader (allowlist + block private/link-local,
  https-only, size-cap); finalize CSP (no inline-script) and secrets handling.
- **Remaining loaders (LLD §5):** `ingest/loaders/{epub,url,image}.py` — epub `ebooklib`→md;
  url `httpx` (SSRF-guarded) → readability; image/scanned-pdf via `VisionProvider.describe`
  (OCR/description) → text. **Bulk upload:** zip/folder → unpack (anti-zip-bomb) → batch
  source registration → ingest; large files streamed to storage.
- **Admin UI polish + i18n (LLD §9/§10):** api-keys/users management UI (issue/scope/revoke,
  roles); **UI i18n** switch (`ui_language` RU/EN), independent of content/reasoning
  languages.
- **Resourcing/deploy (LLD §11):** per-service resource guidance, compose profiles, prod
  checklist (healthchecks, volumes, TLS/ACME).

## Out of scope (deferred — backlog/v2)

Rate limiting, idempotency keys, soft-delete, reranking, resumable ingest, graph
pagination perf, scheduled cron jobs; per-domain ACL, config hot-reload, webhooks, quality
eval harness (LLD §13).

## Data model touched

No new core tables. `app_settings` gains Langfuse config keys. Uses existing tables for
metrics/cost (e.g. `chat_messages.meta`, job records).

## Key flows (LLD §12)

- **Metrics:** request/op → counters/histograms → `/metrics` scrape (Prometheus) →
  Grafana (opt-in).
- **Langfuse:** op trace + per-LLM generation-span, flushed in background; a Langfuse
  outage never breaks the operation.
- **Bulk ingest:** zip → safe unpack → batch register → per-source ingest jobs.
- **URL ingest:** SSRF-guarded fetch → readability → ingest.

## Config (LLD §10)

Env: scrape/limits/allowlists (SSRF allowlist, upload caps). `app_settings`: Langfuse
`host`/keys + enable flag, `ui_language` default. Compose profile `observability` toggles
the metrics stack.

## Security (LLD §11)

This phase completes the security baseline: upload sniffing + anti-zip-bomb, SSRF blocks,
CSP finalization, encrypted Langfuse secret never in agent context, api-key lifecycle.
Langfuse traces may contain untrusted content → optional redaction/sampling before export.

## Acceptance criteria (verifiable)

1. `/metrics` exposes api RED + worker arq + domain/token/cost counters; `/health` is
   separate and reports readiness.
2. `docker compose --profile observability up` brings Prometheus + Grafana + exporters with
   working dashboards; without the profile the app runs unaffected.
3. Langfuse off by default; when enabled, an op produces a trace with per-LLM generation
   spans; killing Langfuse does not fail the op.
4. `pg_dump` backup runs on schedule; a restore from a dump reproduces the corpus.
5. URL loader rejects private/link-local/non-https targets and oversize bodies; a zip bomb
   is rejected; non-allowlisted upload types are refused.
6. epub, url, and image (OCR) sources ingest into articles; bulk zip registers + ingests
   multiple sources.
7. UI language switch toggles RU/EN independently of content/reasoning languages; api-keys
   and users are manageable in the admin UI.

## Tests (LLD §11)

- **Unit:** SSRF allowlist/block decisions; anti-zip-bomb guard; magic-byte sniff; metric
  label cardinality; Langfuse no-op when disabled.
- **Integration (testcontainers + stubs):** `/metrics` content; backup→restore roundtrip;
  epub/url/image loaders (stub Vision/httpx); bulk-zip ingest.
- **API (httpx):** i18n switch; api-keys/users management; `/health` vs `/metrics`.
- **E2E:** observability profile up + scrape; ingest via url/epub/image; backup/restore.

## Risks / notes

- Instrumentation calls should already exist from earlier phases; this phase wires the
  exporter/dashboards, not a late retrofit — verify no hot-path counter was missed.
- SSRF + anti-zip-bomb are the highest-risk items; test adversarial inputs explicitly.
