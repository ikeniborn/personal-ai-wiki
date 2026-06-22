---
title: "Phase 1 — Skeleton (walking skeleton)"
phase: 1
status: design
date: 2026-06-22
depends_on: []
review:
  spec_hash: f634c88ea5571423
  last_run: 2026-06-22
  phases:
    structure:    { status: passed }
    coverage:     { status: passed }
    clarity:      { status: passed }
    consistency:  { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: WARNING
      section: "In scope"
      section_hash: b54df4287e24afaa
      text: "`api_keys` table is created in the Phase-1 alembic baseline, but its only consumer (MCP) is explicitly Out of scope (Phase 8). This requirement maps to no Phase-1 context task."
      verdict: accepted
      verdict_at: 2026-06-22
    - id: F-002
      phase: coverage
      severity: INFO
      section: "In scope"
      section_hash: b54df4287e24afaa
      text: "`audit_log` table and traefik `SSE pass-through` are forward-provisioned for later phases (chat=Phase 4). They fit `core tables`/deploy infra but no Phase-1 task maps them directly."
      verdict: accepted
      verdict_at: 2026-06-22
    - id: F-003
      phase: clarity
      severity: WARNING
      section: "In scope"
      section_hash: b54df4287e24afaa
      text: "arq worker `noop heartbeat task` has no acceptance criterion or test. Acceptance #1 only asserts the worker process starts; the heartbeat task lacks an explicit DoD."
      verdict: accepted
      verdict_at: 2026-06-22
    - id: F-004
      phase: clarity
      severity: INFO
      section: "In scope"
      section_hash: b54df4287e24afaa
      text: "`GET·PUT /settings` persistence has no acceptance criterion or test; Connection fields are `saved but unused`, and no test asserts the settings round-trip."
      verdict: accepted
      verdict_at: 2026-06-22
chain:
  intent: null
---

# Phase 1 — Skeleton (walking skeleton)

**Goal / vertical value:** `docker compose up` boots the whole stack; an admin completes
a first-run wizard, logs in, creates a domain, uploads a markdown source, manually
authors an article, and sees it rendered. **No LLM anywhere.** This proves the deploy
pipeline, schema, auth, storage, and the UI frame end-to-end.

See `…paw-00-overview-design.md` for stack, module rules, global UI, and cross-cutting
conventions. References below point into `docs/reports/lld-personal-ai-wiki.html`.

## In scope

- **Scaffold:** src-layout package `paw`, `uv`/`pyproject.toml`, `ruff` + `mypy` +
  `pytest` config, `main.py` FastAPI factory (mount `/api/v1`, `/` web), `worker.py` arq
  `WorkerSettings` skeleton (no domain tasks yet; a noop heartbeat task only).
- **Config (LLD §10 env layer):** `config.py` pydantic-settings — `DATABASE_URL`,
  `REDIS_URL`, `SESSION_SECRET`, `FERNET_KEY`, body-size/upload limits.
- **DB (LLD §2 subset):** `db/{models,session}.py`, `db/repos/`. Tables this phase:
  `users`, `api_keys`, `app_settings`, `domains`, `blobs`, `sources`, `articles`,
  `article_revisions`, `audit_log`. Vector/chunk/entity/graph/chat/cache/jobs tables are
  introduced by the phases that own them.
- **Migrations:** `alembic/` baseline — extensions (`vector`, `pgcrypto`, `citext`),
  enums (`user_role`, `source_status`, `rev_origin`), the core tables above. Run by a
  one-shot **init container** before api/worker. (Embedding-dim-dependent DDL is deferred
  to Phase 2's managed migration once dim is known.)
- **Storage (LLD §3):** `storage/{base,postgres}.py` — `StorageBackend` Protocol;
  `PostgresStorage` (small → `blobs.bytea` ref `blob:uuid`; large → Large Object ref
  `lo:oid`); streamed `open()` for large bodies.
- **Auth & security baseline (LLD §8/§11):** server-side Redis sessions (cookie
  `SameSite=Lax`), `argon2` passwords, `security/csrf.py` (double-submit),
  `security/sanitize.py` (`nh3` allowlist), `security/secrets.py` (Fernet helper),
  `api/auth.py` + `api/deps.py` (`require_role()` RBAC), `api/errors.py` (RFC 9457
  problem+json), `api/pagination.py` (cursor/keyset). Basic upload guard: magic-byte +
  extension allowlist for `md`/`txt` + max-size (full hardening → Phase 9).
- **Services:** `services/{domains,articles,sources,settings}.py` — domain CRUD; manual
  article create/update producing `article_revisions` (origin=`user`) with optimistic
  lock on `articles.current_rev`; md/txt source upload + delete.
- **API (LLD §8 subset):** `/auth/login`·`/logout`; `GET·POST /domains`;
  `POST /domains/{id}/sources` (multipart, md/txt) + delete; `GET /domains/{id}/articles`
  (listing + cursor); `GET·PUT /articles/{id}` (+ `/revisions`, `POST …/rollback`);
  `GET·PUT /settings` and `/users` (admin); first-run setup endpoints.
- **Web UI (global frame C):** `api/web/{routes,templates,static}`. Vendored htmx +
  theme CSS, CSP without inline-script, `mistune`→`nh3` render. Screens: setup wizard;
  `/login`; `/` dashboard (domains list/create); `/domains/{id}` (renders index article +
  article-tree secondary sidebar — flat list acceptable this phase); `/articles/{id}`
  (render + metadata section placeholders; Edit/Preview tabs; 409 reload banner; rollback);
  `/settings` (single page: Connection + Users sections; Connection fields may be saved
  but are unused this phase).
- **Deploy (LLD §11):** compose services `traefik` (TLS/ACME, HTTP→HTTPS, SSE pass-through),
  `api`, `worker`, `postgres` (pgvector image), `redis` (AOF), `init` (alembic);
  healthchecks; volumes pgdata/redisdata/letsencrypt; **first-run setup wizard**.

## Out of scope (deferred)

LLM/providers/harness, ingest, chunking, embeddings (Phase 2) · retrieval/query (Phase 3)
· chat (Phase 4) · graph viz, revisions diff niceties (Phase 5) · jobs progress UI beyond
the noop task (Phase 2) · query-cache (Phase 7) · MCP (Phase 8) · observability, backups,
full upload/SSRF hardening, bulk-upload, extra loaders, UI i18n switch (Phase 9).

## Data model touched

LLD §2 core subset only (listed above). Cascades that apply now: `domain → sources,
articles`; `article → revisions`. No `chunks`/`entities` yet.

## Key flows

- **First run:** init container migrates → api starts → setup wizard creates the admin
  user + seeds `app_settings` singleton.
- **Manual article:** create/update → write `articles` + `article_revisions`
  (origin=`user`), bump `current_rev`; concurrent PUT with stale rev → **409**.

## Config (LLD §10)

Env layer only this phase. `app_settings` singleton seeded empty by the wizard;
Connection/model/dim fields exist in the schema and form but are wired in Phase 2/9.

## Acceptance criteria (verifiable)

1. `docker compose up` brings all services healthy; init container applies the baseline
   migration; api and worker start.
2. First-run wizard creates admin; second run skips the wizard.
3. Login establishes a Redis session; logout clears it; protected routes 401/redirect.
4. RBAC: a `viewer` cannot create a domain (403); an `admin` can.
5. CSRF: a form POST without the token is rejected.
6. Create domain → upload a `.md` source (persisted via `PostgresStorage`, roundtrips) →
   manually create an article → it renders sanitized (script tags stripped).
7. Concurrent article PUT with a stale `current_rev` returns 409 with a reload banner.
8. Listings paginate by cursor.

## Tests (LLD §11)

- **Unit:** sanitize allowlist; argon2 hash/verify; cursor pagination; `PostgresStorage`
  put/get/open/delete roundtrip incl. Large Object (testcontainer PG).
- **API (httpx):** login/logout, RBAC, CSRF reject, domain CRUD, source upload/delete,
  article create/update + 409, pagination.
- **E2E:** migrate → create domain → upload md → create article → fetch rendered HTML.
- **CI:** `ruff` + `mypy` + `pytest` green.

## Risks / notes

- Embedding-dim-dependent DDL intentionally deferred; baseline must not create `chunks`
  with a fixed-dim vector. Phase 2 owns the managed dim migration.
- Keep the article-tree sidebar a flat list now; parent/child grouping arrives with links
  in Phase 5.
