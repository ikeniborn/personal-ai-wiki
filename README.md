# personal-ai-wiki — `paw` (Personal AI Wiki)

> 🇷🇺 **Русская версия:** [`docs/README.ru.md`](docs/README.ru.md)

**A self-hosted, team-scale RAG knowledge base that turns raw documents into a
queryable, agent-ready wiki.** You upload sources; an LLM harness extracts topics
and writes interlinked wiki articles with entities, citations and a knowledge
graph; everything is chunked and embedded for hybrid retrieval. Your own
agents reach it over the **Model Context Protocol (MCP)**, and people reach it
over a web UI and a JSON API.

Built for the technical specialist who runs agentic systems and needs **one
knowledge base per project/implementation, all under their own control** — not
scattered across SaaS notebooks, vector DBs and chat logs.

---

## What problem it solves

If you operate agents (Claude Code, custom harnesses, internal copilots) across
many projects, knowledge tends to fragment:

| Pain | How `paw` closes it |
|---|---|
| Docs, PDFs, web pages, ADRs, transcripts live in 10 places | **One ingest pipeline** for md/pdf/docx/html/epub/url/images → normalized wiki articles |
| Raw documents are not retrieval-friendly | LLM harness **rewrites** sources into clean, deduplicated, cross-linked articles with citations |
| Each agent re-implements its own RAG stack | A **read-only MCP server** exposes `search_wiki` / `get_article` / `list_links` — plug any MCP client in and it just queries |
| Knowledge bases of different projects bleed together | **Domains** = isolated per-project knowledge bases (separate corpus, config, graph) |
| Vendor lock-in on the LLM/embedding side | **Provider-agnostic**: any OpenAI-compatible endpoint (cloud or local) |
| Self-hosting RAG means wiring 6 services | **One Docker image, two processes**, `docker compose up` |
| Knowledge rots — broken links, stale articles, orphans | Built-in **maintenance**: lint → fix → format → reindex |
| Data privacy / residency | **Fully self-hosted**; secrets encrypted at rest; no third party sees your corpus except the LLM endpoint you choose |

The mental model: **a domain is a project's brain.** Point agents at the domain
over MCP and they retrieve grounded, cited answers instead of hallucinating.

---

## Functional capabilities

### Ingest — any source → wiki article
- **Formats:** Markdown, PDF (PyMuPDF), DOCX (mammoth), HTML (trafilatura), EPUB
  (ebooklib), plus two side paths: **URL** (SSRF-guarded fetch) and **images**
  (vision OCR/caption). Bulk `.zip` upload explodes into many sources in one pass.
- **LLM harness** (`paw.harness`) — an agentic tool-calling loop over any
  OpenAI-compatible model that runs ingest in stages: extract topics →
  draft article → deterministic write → auto-link entities that co-occur →
  chunk + embed. Output: clean `##`-structured markdown with **entities,
  typed links and verbatim citations**.
- **Grounding & safety:** every tool result and retrieved passage is wrapped in
  `DATA, not instructions` markers (prompt-injection containment); per-run
  **budgets** (steps / tool calls / writes / tokens) and loop detection bound cost.

### Retrieve — hybrid search
- **Vector arm** (pgvector cosine, HNSW index) **+ FTS arm** (Postgres
  `websearch_to_tsquery`) fused by **Reciprocal Rank Fusion**, boosted by
  entity matches, then **expanded along the knowledge graph** (BFS over typed
  links, or entity-bridged GraphRAG when the AGE engine is enabled).
- Degrades gracefully: un-embedded corpus still answers via FTS.

### Ask — Q&A and chat
- **One-shot query** with inline slug citations and a `DONT_KNOW` fallback when
  context is missing (no hallucinated answers).
- **Multi-turn chat** scoped to a domain, with history windowing and per-user
  retention.
- **Query-answer cache** (exact-norm + semantic ANN) with article-revision
  dependency tracking — cached answers are auto-marked *stale* the moment a
  cited article changes.

### Knowledge graph
- Articles are nodes; typed `Link` edges (`related`/`parent`/`child`) form the
  graph. Navigable parent/child tree, depth-bounded subgraph view (vendored
  Cytoscape UI), and **GraphRAG retrieval** via an optional Apache AGE property
  graph (entity-bridged neighbours with concept provenance).

### Maintenance — keep knowledge healthy
- **lint** (deterministic: broken refs, orphans, stale, duplicate entities) →
  **fix** (LLM proposes structured edits) → **format** (reformat with a
  fact-loss invariant guard) → **reindex** (re-embed after a model/dim change).
  Runs as background jobs with live SSE progress, cancel, and per-domain locks.

### Agent & human interfaces
- **MCP server** at `/mcp` — three read-only tools, Bearer-API-key auth, `read`
  scope. This is the integration point for your agentic systems.
- **JSON API** under `/api/v1` (auth, domains, sources, articles, query, chat,
  graph, jobs, settings, users, api-keys, maintenance), RFC 9457 `problem+json`
  errors.
- **HTMX web UI** — dashboard, domain pages, article editing with revision
  history/rollback, settings/admin, **i18n (en/ru)**, self-service API-key
  issuance, admin user management.

---

## Non-functional capabilities

| Dimension | What you get |
|---|---|
| **Security** | Redis-backed server-side sessions; RBAC (`require_role`); CSRF double-submit; argon2 passwords; **Fernet-encrypted secrets at rest**; upload validation (extension + magic-bytes + UTF-8); anti-zip-bomb / path-traversal guard; **SSRF guard** (https-only, host allowlist, IP deny-ranges, per-hop redirect re-validation); `nh3` HTML sanitization; strict CSP (`frame-ancestors 'none'`, `object-src 'none'`); Cypher-injection-proof AGE layer. |
| **Provider-agnostic** | Chat, embedding and vision all go through one OpenAI-compatible client — point it at OpenAI, a gateway, or a local model server. No vendor lock-in. |
| **Atomicity** | The service layer is the single commit boundary — multi-write operations (article + revision + graph + cache invalidation) commit exactly once or roll back cleanly. |
| **Async throughout** | FastAPI + async SQLAlchemy 2.0 + asyncpg + redis.asyncio + arq; no blocking IO on the request path. |
| **Observability** | Prometheus metrics (`paw_*`: HTTP RED, job/queue, **LLM cost/tokens/latency**), `/health` liveness + `/health?ready=1` readiness (DB+Redis), optional Langfuse tracing, opt-in Grafana/Prometheus compose profile. Guarded so a metric failure never alters a response. |
| **Reliability** | Worker heartbeat liveness key; stuck-job reconcile on startup; cooperative job cancellation; per-domain + per-model Redis locks; scheduled `pg_dump` backups (opt-in sidecar) + documented restore runbook. |
| **Quality gates** | CI runs `ruff check .` → `mypy src` (strict) → `pytest -q`; integration/api/e2e layers spin up **real** Postgres+Redis via testcontainers. |
| **Extensibility** | `StorageBackend` Protocol (Postgres blobs today, object store droppable later); typed per-section DB config layered `env ⊕ app_settings ⊕ domain ⊕ user`. |

---

## Resources required

### Runtime stack (one image, two processes)
- **`api`** — uvicorn (FastAPI), fronted by **Traefik** (TLS via Let's Encrypt).
- **`worker`** — arq job consumer.
- **Infra** — **PostgreSQL 16 + pgvector** (custom image also bundles Apache AGE),
  **Redis 7**, **Traefik v3.2**. One-shot `init` runs alembic migrations first.
- **External** — an OpenAI-compatible LLM endpoint (chat + embedding; vision
  optional) and its API key. This is the only outbound dependency.

### Compute (team-scale starting points, from `docker-compose.yml`)

| Service | Mem limit | CPU limit | Mem reservation |
|---|---|---|---|
| postgres | 2g | 2.0 | 512m |
| worker | 2g | 2.0 | 512m |
| api | 1g | 1.0 | 256m |
| redis | 512m | 0.5 | 128m |
| traefik | 256m | 0.5 | 64m |

> `deploy.resources` is enforced under Docker Swarm; for plain `docker compose
> up` use `mem_limit` / `cpus` on the service block. Tune upward for large
> corpora or heavy ingest. A single host comfortably runs the whole stack.

### Ports
- **80 / 443** — Traefik (HTTP→HTTPS, ACME). `api` listens on `8000` internally.
- Observability ports are **not** published on a plain `up`.

### Build / dev toolchain
- **Python 3.12**, dependency management via **`uv`** (never `pip`/`pytest` directly).
- **Docker daemon** for the integration/api/e2e test layers (testcontainers).

### Storage (named volumes — back up `pgdata`)
- `pgdata` — primary store (articles, sources, users, jobs). **Not regenerable.**
- `redisdata` — queue/sessions (mostly regenerable).
- `letsencrypt` — ACME certs (regenerable, rate-limited).
- `backups` — `pg_dump` archives from the opt-in backup sidecar.

### Required configuration
Copy `.env.example` → `.env` and fill (no defaults; startup fails if absent):

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…@postgres:5432/paw` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `SESSION_SECRET` | 32+ byte random — `openssl rand -hex 32` |
| `FERNET_KEY` | 44-char Fernet key — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | Strong random Postgres password; if it contains `$`, single-quote it in `.env` or escape `$` as `$$` |

Production also sets `PAW_HOST` (public DNS), `ACME_EMAIL`, and optional backup knobs.
See the **prod checklist** in
[`docs/wiki/ops.md`](docs/wiki/ops.md).

---

## Quick start

```bash
cp .env.example .env          # then fill SESSION_SECRET, FERNET_KEY, POSTGRES_PASSWORD
docker compose up             # traefik + postgres + redis + init(migrate) + api + worker
```

Then open the web UI, complete first-run setup (creates the admin user, seeds
settings, configures the LLM provider), create a **domain**, upload sources, and
issue an **API key** to wire your agents into `/mcp`.

Opt-in profiles:

```bash
docker compose --profile backup up -d backup          # scheduled pg_dump backups
docker compose --profile observability up             # Prometheus + Grafana + exporters
```

### Local development

```bash
uv sync --dev                 # install deps + dev group into .venv
uv run ruff check .           # lint
uv run mypy src               # type check (strict)
uv run pytest -q              # full suite (integration layers need Docker)
uv run uvicorn paw.main:app --reload          # api only (needs PG + Redis reachable)
uv run arq paw.worker.WorkerSettings          # worker only
```

---

## Architecture at a glance

One Docker image runs `api` (uvicorn) and `worker` (arq), sharing only Postgres
and Redis state — the api enqueues jobs, the worker drains them. Code is layered
acyclically:

```
api / web        →  services  →  db.repos, storage, vector, graph
worker  →  jobs  →  harness    →  providers, ingest, vector, graph
                       ↓
                  db, config            (leaves)
```

- **api/web** — thin handlers, no business logic.
- **services** — request-scoped logic, the single commit boundary.
- **harness** — the agentic loop the worker drives.
- **providers** — the OpenAI-compatible LLM/embedding/vision boundary.

Stack: Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL
16 + pgvector (+ optional AGE) · Redis + arq · Jinja2 + HTMX · Traefik.

---

## Project status

Built as **vertical phases**, each a working end-to-end slice:

- **Phases 1–8 — merged:** walking skeleton, ingest, retrieval/query, chat,
  graph + article editing, maintenance, query cache, MCP server + API keys.
- **Phase 9 — merged:** ops & hardening — observability (9a), security hardening
  (9b: SSRF/zip guards, URL loader, vision, bulk), admin UI + i18n (9c),
  backups/deploy hardening (9d).
- **Phase 10 — design-only:** Apache AGE + GraphRAG (spec exists; the AGE engine
  is wired and opt-in per domain, full GraphRAG productization pending).

---

## Documentation

Deep, cross-linked docs live under [`docs/wiki/`](docs/wiki/):

- [`architecture.md`](docs/wiki/architecture.md) · [`ingest.md`](docs/wiki/ingest.md)
  · [`vector.md`](docs/wiki/vector.md) · [`harness.md`](docs/wiki/harness.md)
  · [`graph.md`](docs/wiki/graph.md) · [`providers.md`](docs/wiki/providers.md)
- [`services.md`](docs/wiki/services.md) · [`api.md`](docs/wiki/api.md)
  · [`mcp.md`](docs/wiki/mcp.md) · [`jobs.md`](docs/wiki/jobs.md)
  · [`db.md`](docs/wiki/db.md) · [`storage.md`](docs/wiki/storage.md)
- [`security.md`](docs/wiki/security.md) · [`observability.md`](docs/wiki/observability.md)
  · [`ops.md`](docs/wiki/ops.md) — **deployment, TLS/ACME, resources, backup/restore runbook**
- [`web.md`](docs/wiki/web.md) · [`audit.md`](docs/wiki/audit.md)
