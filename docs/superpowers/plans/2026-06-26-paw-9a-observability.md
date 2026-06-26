---
title: "Phase 9a — Observability Implementation Plan"
phase: 9
sub_plan: 9a
state: draft
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-9-ops-hardening-design.md
review:
  plan_hash: 92b62077e9a31ddb
  spec_hash: 25f8d2e8b94c05a4
  last_run: 2026-06-26
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: WARNING
      section: "Task 1: Dependencies + metric registry + cost table"
      section_hash: 5b4f14795dfeab8e
      fragment: 'QUEUE_DEPTH = Gauge("paw_queue_depth", "arq queue depth")'
      text: "The spec requires a worker `queue depth` metric (In scope: 'worker = arq job by type/status, duration histogram, queue depth, retries, dead-letter, job-lock wait'). The `paw_queue_depth` gauge is declared in Task 1 but no step (Task 5 included) ever sets it, so it always reads 0 — the requirement is named but not fulfilled."
      fix: "Add a step (most naturally in Task 5) that populates `paw_queue_depth`, e.g. read the arq queue length via `redis.zcard`/`llen` on the arq queue key in `on_startup`/a periodic hook or per-job, or explicitly defer it in Risks and remove the gauge to avoid an always-zero metric."
      verdict: fixed
      verdict_at: 2026-06-26
      resolution: "Task 5 now adds set_queue_depth(redis) via redis.zcard on the arq queue (called in on_startup + per job body), Interfaces document it, and test_worker_metrics asserts the gauge reflects the live queue length. Risks note added."
    - id: F-002
      phase: coverage
      severity: WARNING
      section: "Task 3: Langfuse client + settings service (OFF by default, no-op safe)"
      section_hash: 00758489f2f1b984
      fragment: "OpTrace.span(*, name, metadata) -> None"
      text: "The spec's Langfuse integration says 'tool-call = span' (In scope: 'op = trace, each LLM call = generation-span ..., tool-call = span'). `OpTrace.span()` exists and a no-op test calls it, but no implementation step ever emits a real tool-call span — Task 4's `instrument_chat` only calls `trace.generation(...)`, and the plan keeps `loop.py` (the tool-call site) untouched. Tool-call spans are neither wired nor explicitly deferred."
      fix: "Either wire tool-call spans (e.g. via the harness `on_step` hook the plan already references, calling `trace.span(name=\"tool:<name>\", ...)`), or add an explicit deferral note in Risks/Notes stating tool-call spans are out of 9a scope (generation spans only) so the spec gap is acknowledged."
      verdict: fixed
      verdict_at: 2026-06-26
      resolution: "Task 4 Step 5 now wires tool-call spans via the op's on_step callback (trace.span(name=f'tool:{...}')) without editing loop.py, with an optional ToolContext.trace path for finer granularity, plus a non-fatal test and a Risks note."
    - id: F-003
      phase: verifiability
      severity: INFO
      section: "Task 6: Cache-hit + active-SSE instrumentation (close the remaining domain counters)"
      section_hash: bb2feee92bedaa64
      fragment: "metrics.SSE_ACTIVE.inc() ... finally: metrics.SSE_ACTIVE.dec()"
      text: "Task 6 adds the `paw_sse_active` gauge (Step 2) but the only test in the task (Step 4) asserts the cache hit/miss metric; the SSE-active inc/dec has no verifying assertion. The gauge ships without a check."
      fix: "Add a small assertion that entering an SSE stream increments `paw_sse_active` and exiting decrements it (delta-based), or note that the SSE gauge is covered only by manual/dashboard inspection."
      verdict: fixed
      verdict_at: 2026-06-26
      resolution: "Task 6 Step 4 now asserts the paw_sse_active gauge returns to its starting value after a stream completes (balanced inc/dec, no leak on disconnect), with a dedicated `-k 'sse and obs'` run."
---

# Phase 9a — Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `paw` observable in production — Prometheus metrics on both api and worker (`/metrics`), a real liveness/readiness `/health` split, instrumented LLM latency + cost (which do not exist yet), an opt-in `observability` compose profile (Prometheus + Grafana + exporters), and an OFF-by-default Langfuse client whose outage can never fail an op. This is sub-plan **9a** of Phase 9 ("Ops + hardening"); it owns observability only. Sub-plans 9b (hardening + loaders), 9c (admin-ui + i18n), 9d (backups + deploy) are out of scope here.

**Architecture:** A new `paw.obs` package centralises everything. `obs/metrics.py` declares every Prometheus collector (`paw_*` prefix) and a single `render_metrics()`. `obs/http.py` is a Starlette `BaseHTTPMiddleware` that records RED metrics keyed by the **route template** (bounded cardinality), plus an in-flight gauge. `obs/cost.py` holds a tiny per-model rate table and `compute_cost()`. `obs/instrument.py` provides `InstrumentedChatProvider` / `InstrumentedEmbeddingProvider` wrappers (decorators around the `ChatProvider` / `EmbeddingProvider` Protocols) that time every LLM call, compute cost, increment token/cost/latency/error metrics, and emit a Langfuse generation span — this is the single seam that captures the many scattered `.chat()/.structured()/.embed()` call sites without editing each one. `obs/langfuse_client.py` is a lazy singleton reading Langfuse config from `app_settings`; every flush is fire-and-forget so a Langfuse failure never propagates; when disabled it is a complete no-op. The api gets `@app.get("/metrics")` and a readiness-checking `/health`; the worker starts a tiny `prometheus_client` HTTP server in `on_startup` (gated behind `worker_metrics_port`, default 0 = disabled) and increments arq job/duration/retry/dead-letter/lock-wait/queue-depth metrics inside the job bodies. The metrics stack (Prometheus, Grafana, `postgres_exporter`, `redis_exporter`, `cAdvisor`, Traefik built-in metrics) is added under `profiles: [observability]` so plain `docker compose up` is unchanged; scrape config + a minimal dashboard JSON are vendored under `deploy/observability/`.

**Tech Stack:** Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · arq · Redis · `prometheus-client` · `langfuse` (client only) · Prometheus + Grafana (opt-in compose) · `pytest` + `testcontainers`.

## Global Constraints

- **Dependency management is `uv`** — never call `pip`/`pytest` directly; go through `uv run`. Add deps with `uv add`.
- **CI gate (all three must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- **Service is the single commit boundary.** Repos and storage must never `commit()`; a service batches writes and commits once. (9a adds no new persisted writes except the existing `SettingsService.update`, which already commits.)
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457 `application/problem+json`). `IntegrityError` auto-maps to 409.
- **Async everywhere** (`asyncpg`, `redis.asyncio`); `pytest` runs `asyncio_mode = auto` so tests are plain `async def`.
- **Layering (no cycles):** `api`/`web`/`mcp` → `services` → `db.repos`, `storage` → `db`, `config`. The new `paw.obs` package is a **leaf-ish utility layer**: `obs.metrics`, `obs.cost`, `obs.http` import only stdlib + `prometheus_client`/`starlette` (no `paw.services`/`paw.db`). `obs.readiness` imports `paw.api.deps` + `paw.db.session` (it is an infra probe, allowed). `obs.langfuse_client` and `obs.instrument` may import `paw.providers.base`, `paw.security.secrets`, and `paw.config`, but must NOT import `paw.api` or `paw.services` (the Langfuse config dataclass is passed IN to them, not fetched by them). The api/worker/job layers import `paw.obs`, never the reverse.
- **Observability must never change behaviour.** A metrics error, a missing route, or a Langfuse outage must never alter an HTTP response, a job status, or an op result. Every Langfuse call and every metrics-server start is wrapped so failures are swallowed.
- **Label cardinality is bounded.** HTTP metrics use `request.scope["route"].path` (the template, e.g. `/api/v1/domains/{domain_id}/sources`), never the raw path; an unmatched request (404) is labelled `route="<unmatched>"`. `domain_id` is NOT a metric label (high cardinality); domain-level detail belongs in Langfuse metadata, not Prometheus labels.
- **Tests need Docker** for `integration`/`api`/`e2e` layers (real Postgres + Redis via testcontainers). Only `unit` runs without Docker. New unit tests must run Docker-free.
- **Branch workflow:** all work on a `dev-*` branch off up-to-date `master` (ask first whether to create a `wk-<branch>` worktree); merge via PR. Never commit to `master`.
- **Docs are English; conversation is Russian.** After functional changes, update `docs/wiki/` via iwiki (final task).

## Reused building blocks (already in the codebase — do not reimplement)

- `paw.providers.base`: `ChatProvider` / `EmbeddingProvider` Protocols; `ChatResult(content, tool_calls, finish_reason, usage: dict[str, int])`; `Message`, `ToolSpec`, `ToolCall`. **`ChatResult.usage` already carries `prompt_tokens`/`completion_tokens`/`total_tokens`** when the OpenAI response includes usage (`openai_compat.py::chat` lines 89-94) — that is the token source for cost.
- `paw.providers.openai_compat.OpenAICompatProvider`: concrete `chat()/stream()/embed()/structured()`. `self.chat_model` / `self.embedding_model` give the model name for cost lookup.
- `paw.providers.factory.build_chat_provider(pc, box)` / `build_embedding_provider(pc, box)` — the two construction sites whose output we wrap.
- `paw.harness.loop.run_loop(provider, ctx, *, system, task, tools, on_step)` — the harness loop; `ctx.budget.add_tokens(...)` already accumulates tokens (`loop.py` line 46). The op-level trace wraps a `run_loop`/`run_ingest` call.
- `paw.harness.prompts.PROMPT_VERSION` (`= "v1"`, in `src/paw/harness/prompts/__init__.py`) — Langfuse trace metadata `prompt_version`.
- `paw.harness.ops.ingest.run_ingest(...) -> IngestResult(article_id, chunk_count, entity_count, citation_count, link_count)` — domain counters source (articles/chunks per ingest).
- `paw.jobs.tasks.*` (`ingest_domain`, `lint_domain`, `fix_issues`, `format_articles`, `reindex_domain`, `gc_housekeeping`) — arq job bodies; each sets status running/succeeded/failed/cancelled and **returns** the status string. Instrument the job counter/duration here.
- `paw.jobs.locks.domain_lock` / `model_lock` — `model_lock` polls until acquired (lines 21-34); the wait before `yield` is the job-lock-wait histogram seam.
- `paw.worker.WorkerSettings` — `functions`, `on_startup` (calls `heartbeat` + `reconcile_jobs`). arq surfaces the retry attempt via `ctx["job_try"]`.
- `paw.services.settings.SettingsService(session)`: `.get() -> dict`, `.update(dict) -> dict` (commits) — backed by the `app_settings` JSONB singleton (`db/repos/settings.py`). Langfuse keys are read/written through this.
- `paw.security.secrets.SecretBox(key)`: `.encrypt(plaintext) -> str` / `.decrypt(token) -> str` (Fernet). Used for `langfuse_secret_key_enc`.
- `paw.config.get_settings()` (`lru_cache`) — env layer; tests reset it via the `wired_settings` fixture (`get_settings.cache_clear()`).
- `paw.api.deps.get_redis()` — process-global `redis.asyncio.Redis`; readiness pings it.
- `paw.db.session.get_sessionmaker()` — process-global async sessionmaker; readiness runs `SELECT 1`.
- Test infra: `tests/conftest.py` (`wired_settings`, `db_session`, `redis_client`, `_migrate`, `_clean_db`); `tests/stubs.py::StubChatProvider` (`.text(...)`, `.tool(...)`, scriptable; `usage` is empty by default — tests that assert cost build a `ChatResult` with explicit `usage`), `StubEmbeddingProvider`. `tests/unit/test_health.py` (ASGI httpx pattern, no Docker).

## File Structure

**Create:**
- `src/paw/obs/__init__.py` — package marker (empty).
- `src/paw/obs/metrics.py` — all Prometheus collectors (`paw_*`) + `render_metrics() -> tuple[bytes, str]`.
- `src/paw/obs/cost.py` — `MODEL_COSTS` table + `compute_cost(model, usage) -> float`.
- `src/paw/obs/http.py` — `MetricsMiddleware` (RED + in-flight by route template).
- `src/paw/obs/langfuse_client.py` — `LangfuseConfig`, `get_langfuse(cfg)`, `trace_op(...)`, `OpTrace`, no-op when disabled.
- `src/paw/obs/instrument.py` — `InstrumentedChatProvider`, `InstrumentedEmbeddingProvider`, `instrument_chat(...)`, `instrument_embedding(...)`.
- `src/paw/obs/readiness.py` — `async def check_readiness() -> tuple[bool, dict[str, str]]` (DB + Redis).
- `src/paw/services/langfuse_settings.py` — `LangfuseSettingsService` reading/writing the four `app_settings` keys (decrypts secret via `SecretBox`).
- `deploy/observability/prometheus.yml` — scrape config (api, worker, postgres_exporter, redis_exporter, cadvisor, traefik).
- `deploy/observability/grafana/provisioning/datasources/prometheus.yml` — Grafana datasource.
- `deploy/observability/grafana/provisioning/dashboards/paw.yml` — dashboard provider.
- `deploy/observability/grafana/dashboards/paw-overview.json` — minimal RED + jobs + LLM-cost dashboard.
- Tests: `tests/unit/test_obs_cost.py`, `tests/unit/test_obs_http_metrics.py`, `tests/unit/test_obs_langfuse_noop.py`, `tests/unit/test_obs_metrics_render.py`, `tests/unit/test_health_readiness.py` (Docker-free with monkeypatched checks), `tests/integration/test_metrics_endpoint.py` (live `/metrics` content), `tests/integration/test_obs_instrument.py` (instrumented provider records cost + non-fatal Langfuse), `tests/integration/test_worker_metrics.py` (job counters increment), `tests/integration/test_obs_cache_metric.py`.

**Modify:**
- `pyproject.toml` — add `prometheus-client>=0.21`, `langfuse>=2.50`; add mypy override for `langfuse.*`.
- `src/paw/config.py` — add `worker_metrics_port: int = 0`.
- `src/paw/main.py` — add `/metrics` route; readiness `/health` (+`/ready`); mount `MetricsMiddleware`.
- `src/paw/worker.py` — start metrics http server in `on_startup` (gated).
- `src/paw/jobs/tasks.py` — record job counter/duration/retries/dead-letter; wrap providers per-op + emit a Langfuse trace; record domain counters (articles/chunks) after `run_ingest`.
- `src/paw/jobs/locks.py` — record `model_lock` wait time into `paw_job_lock_wait_seconds`.
- `src/paw/services/query_cache.py` — cache hit/miss counter.
- `src/paw/api/routers/chat.py` + `src/paw/api/routers/query.py` — `paw_sse_active` gauge around the SSE generators.
- `docker-compose.yml` — Traefik prometheus metrics + a profiled `observability` block; named volumes.
- `.env.example` — document `WORKER_METRICS_PORT`, `GRAFANA_ADMIN_PASSWORD`.
- `docs/wiki/*` — refreshed via iwiki (final task).

---

### Task 1: Dependencies + metric registry + cost table

**Files:**
- Modify: `pyproject.toml`, `src/paw/config.py`
- Create: `src/paw/obs/__init__.py`, `src/paw/obs/metrics.py`, `src/paw/obs/cost.py`
- Test: `tests/unit/test_obs_cost.py`, `tests/unit/test_obs_metrics_render.py`

**Interfaces:**
- Produces in `obs/cost.py`:
  - `MODEL_COSTS: dict[str, tuple[float, float]]` — USD per **1K** (prompt, completion) tokens; a few seeded models only. YAGNI — do not enumerate every model.
  - `compute_cost(model: str, usage: dict[str, int]) -> float` — `prompt_tokens`/`completion_tokens` from `usage`; unknown model → `0.0`; missing keys → treated as 0.
- Produces in `obs/metrics.py` (default registry; one module-level instance each):
  - Counters: `paw_http_requests_total{method,route,status}`, `paw_job_total{kind,status}`, `paw_job_retries_total{kind}`, `paw_job_deadletter_total{kind}`, `paw_llm_tokens_total{op,direction}`, `paw_llm_cost_usd_total{op,model}`, `paw_llm_errors_total{op}`, `paw_embeddings_total`, `paw_articles_total`, `paw_chunks_total`, `paw_cache_hits_total{result}`.
  - Histograms: `paw_http_request_duration_seconds{method,route}`, `paw_job_duration_seconds{kind}`, `paw_job_lock_wait_seconds{kind}`, `paw_llm_latency_seconds{op}`.
  - Gauges: `paw_http_inflight`, `paw_sse_active`, `paw_queue_depth`.
  - `render_metrics() -> tuple[bytes, str]` returning `(generate_latest(), CONTENT_TYPE_LATEST)`.
- Produces in `config.py`: `worker_metrics_port: int = 0` (0 = disabled).

- [ ] **Step 1: Add dependencies**

```bash
uv add "prometheus-client>=0.21" "langfuse>=2.50"
```
Expected: `pyproject.toml` `[project].dependencies` gains both; `uv.lock` updates; `.venv` resolves.

- [ ] **Step 2: Silence mypy on the untyped langfuse import**

Append to `pyproject.toml` after the existing `[[tool.mypy.overrides]]` block:
```toml
[[tool.mypy.overrides]]
module = ["langfuse.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Add `worker_metrics_port` to config**

In `src/paw/config.py`, inside `class Settings`, after `session_ttl_seconds`:
```python
    worker_metrics_port: int = 0  # >0 starts a prometheus http server in the worker
```

- [ ] **Step 4: Write the failing cost test**

Create `tests/unit/test_obs_cost.py`:
```python
from paw.obs.cost import MODEL_COSTS, compute_cost


def test_known_model_cost():
    model = next(iter(MODEL_COSTS))
    p_rate, c_rate = MODEL_COSTS[model]
    usage = {"prompt_tokens": 1000, "completion_tokens": 2000}
    expected = p_rate * 1 + c_rate * 2  # per-1K rates
    assert compute_cost(model, usage) == expected


def test_unknown_model_is_free():
    assert compute_cost("no-such-model", {"prompt_tokens": 5}) == 0.0


def test_missing_usage_keys_are_zero():
    model = next(iter(MODEL_COSTS))
    assert compute_cost(model, {}) == 0.0
```
Run `uv run pytest tests/unit/test_obs_cost.py -q` → fails (no module).

- [ ] **Step 5: Implement `obs/cost.py`**

```python
from __future__ import annotations

# USD per 1K tokens: (prompt, completion). Embedding models bill prompt-side only.
# Seeded, not exhaustive — unknown models cost 0.0 (YAGNI; admins extend as needed).
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.00250, 0.01000),
    "text-embedding-3-small": (0.00002, 0.0),
    "text-embedding-3-large": (0.00013, 0.0),
}


def compute_cost(model: str, usage: dict[str, int]) -> float:
    rates = MODEL_COSTS.get(model)
    if rates is None:
        return 0.0
    prompt_rate, completion_rate = rates
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return prompt_rate * (prompt / 1000) + completion_rate * (completion / 1000)
```
Run `uv run pytest tests/unit/test_obs_cost.py -q` → passes.

- [ ] **Step 6: Implement `obs/metrics.py`**

Create `src/paw/obs/__init__.py` (empty) and `src/paw/obs/metrics.py`:
```python
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# --- api RED ---
HTTP_REQUESTS = Counter(
    "paw_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "paw_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)
HTTP_INFLIGHT = Gauge("paw_http_inflight", "In-flight HTTP requests")
SSE_ACTIVE = Gauge("paw_sse_active", "Active SSE streams")

# --- worker / arq ---
JOB_TOTAL = Counter("paw_job_total", "arq jobs by kind+status", ["kind", "status"])
JOB_DURATION = Histogram("paw_job_duration_seconds", "arq job duration", ["kind"])
JOB_RETRIES = Counter("paw_job_retries_total", "arq job retries", ["kind"])
JOB_DEADLETTER = Counter("paw_job_deadletter_total", "arq jobs that exhausted retries", ["kind"])
JOB_LOCK_WAIT = Histogram("paw_job_lock_wait_seconds", "model-lock acquisition wait", ["kind"])
QUEUE_DEPTH = Gauge("paw_queue_depth", "arq queue depth")

# --- domain / LLM ---
LLM_TOKENS = REDACTED "LLM tokens", ["op", "direction"])
LLM_COST = Counter("paw_llm_cost_usd_total", "LLM cost in USD", ["op", "model"])
LLM_LATENCY = Histogram("paw_llm_latency_seconds", "LLM call latency", ["op"])
LLM_ERRORS = Counter("paw_llm_errors_total", "LLM call errors", ["op"])
EMBEDDINGS = Counter("paw_embeddings_total", "embeddings generated")
ARTICLES = Counter("paw_articles_total", "articles written by ingest")
CHUNKS = Counter("paw_chunks_total", "chunks written by ingest")
CACHE_HITS = Counter("paw_cache_hits_total", "query cache lookups", ["result"])  # hit|miss


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
```

- [ ] **Step 7: Write + pass the render test**

Create `tests/unit/test_obs_metrics_render.py`:
```python
from paw.obs import metrics


def test_render_metrics_exposes_names():
    metrics.HTTP_REQUESTS.labels(method="GET", route="/health", status="200").inc()
    metrics.LLM_COST.labels(op="ingest", model="gpt-4o-mini").inc(0.01)
    payload, content_type = metrics.render_metrics()
    text = payload.decode()
    assert "text/plain" in content_type
    assert "paw_http_requests_total" in text
    assert "paw_llm_cost_usd_total" in text
```
Run `uv run pytest tests/unit/test_obs_cost.py tests/unit/test_obs_metrics_render.py -q` → both pass.

**Commit:** `feat(obs): add prometheus metric registry + model cost table`

---

### Task 2: HTTP RED middleware + `/metrics` + readiness `/health`

**Files:**
- Create: `src/paw/obs/http.py`, `src/paw/obs/readiness.py`
- Modify: `src/paw/main.py`
- Test: `tests/unit/test_obs_http_metrics.py`, `tests/unit/test_health_readiness.py`

**Interfaces:**
- `obs/http.py`: `class MetricsMiddleware(BaseHTTPMiddleware)` — increments `HTTP_INFLIGHT`, times the request, records `HTTP_REQUESTS{method,route,status}` + `HTTP_DURATION{method,route}` using the route template; `route="<unmatched>"` when no route matched. Never raises.
- `obs/readiness.py`: `async def check_readiness() -> tuple[bool, dict[str, str]]` — pings Redis (`get_redis().ping()`) and runs `SELECT 1` via `get_sessionmaker()`; returns `(ok, {"db": "ok"|"error: …", "redis": ...})`.
- `main.py`: `@app.get("/metrics")` returns `Response(payload, media_type=content_type)`; `/health` returns `{"status": "ok"}` for liveness and, with `?ready=1` (and the `/ready` alias), includes component status + `503` when not ready.

- [ ] **Step 1: Decide the `/health` contract (liveness vs readiness)**

`/health` stays a **liveness** probe by default (trivial, always 200, no I/O) so the existing compose healthcheck and `tests/unit/test_health.py` keep passing. Readiness is exposed at `/health?ready=1` (and the convenience alias `/ready`), which runs DB+Redis checks and returns **503** when any dependency is down. This keeps liveness and readiness **separate** (spec acceptance #1) without breaking the unchanged liveness contract or the compose `/health` healthcheck.

- [ ] **Step 2: Write the failing readiness test (Docker-free)**

Create `tests/unit/test_health_readiness.py`:
```python
import paw.obs.readiness as readiness
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_liveness_is_trivial():
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_ok(monkeypatch):
    async def fake_check():
        return True, {"db": "ok", "redis": "ok"}

    monkeypatch.setattr(readiness, "check_readiness", fake_check)
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health", params={"ready": "1"})
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


async def test_readiness_degraded_returns_503(monkeypatch):
    async def fake_check():
        return False, {"db": "ok", "redis": "error: down"}

    monkeypatch.setattr(readiness, "check_readiness", fake_check)
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health", params={"ready": "1"})
    assert resp.status_code == 503
    assert resp.json()["ready"] is False
```
Note: the route must call `readiness.check_readiness` as a module attribute so monkeypatch takes effect. Run → fails.

- [ ] **Step 3: Write the failing HTTP-metrics test (Docker-free)**

Create `tests/unit/test_obs_http_metrics.py`:
```python
from httpx import ASGITransport, AsyncClient

from paw.main import create_app
from paw.obs import metrics


def _sample(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


async def test_known_route_uses_template_label():
    app = create_app()
    before = _sample(metrics.HTTP_REQUESTS, method="GET", route="/health", status="200")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/health")
    after = _sample(metrics.HTTP_REQUESTS, method="GET", route="/health", status="200")
    assert after == before + 1


async def test_unmatched_route_is_bucketed():
    app = create_app()
    before = _sample(metrics.HTTP_REQUESTS, method="GET", route="<unmatched>", status="404")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/api/v1/domains/does-not-exist-zzz/nope")
    after = _sample(metrics.HTTP_REQUESTS, method="GET", route="<unmatched>", status="404")
    assert after >= before + 1
```
Run → fails (no middleware, no template label yet).

- [ ] **Step 4: Implement `obs/readiness.py`**

```python
from __future__ import annotations

from sqlalchemy import text

from paw.api.deps import get_redis
from paw.db.session import get_sessionmaker


async def check_readiness() -> tuple[bool, dict[str, str]]:
    components: dict[str, str] = {}
    ok = True
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        components["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        components["db"] = f"error: {type(exc).__name__}"
        ok = False
    try:
        await get_redis().ping()
        components["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        components["redis"] = f"error: {type(exc).__name__}"
        ok = False
    return ok, components
```

- [ ] **Step 5: Implement `obs/http.py`**

```python
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from paw.obs import metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        metrics.HTTP_INFLIGHT.inc()
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "<unmatched>"
            method = request.method
            metrics.HTTP_INFLIGHT.dec()
            metrics.HTTP_DURATION.labels(method=method, route=template).observe(
                time.perf_counter() - start
            )
            metrics.HTTP_REQUESTS.labels(
                method=method, route=template, status=str(status)
            ).inc()
```
Note: `request.scope["route"]` is populated by Starlette routing only when a route matched; on 404 it is absent → `<unmatched>` (bounded cardinality — satisfies the cardinality guard). The middleware reads it *after* `call_next`, so routing has already run.

- [ ] **Step 6: Wire `/metrics`, readiness `/health`, and the middleware into `create_app()`**

In `src/paw/main.py`:
- Add imports:
  ```python
  from fastapi.responses import JSONResponse, Response
  from paw.obs import readiness as readiness_mod
  from paw.obs.http import MetricsMiddleware
  from paw.obs.metrics import render_metrics
  ```
  (`Response` is already imported; add `JSONResponse`.)
- Replace the existing `/health` handler with:
  ```python
  @app.get("/health")
  async def health(ready: int = 0) -> Response:
      if not ready:
          return JSONResponse({"status": "ok"})
      ok, components = await readiness_mod.check_readiness()
      return JSONResponse(
          {"ready": ok, "components": components},
          status_code=200 if ok else 503,
      )

  @app.get("/ready")
  async def ready() -> Response:
      return await health(ready=1)

  @app.get("/metrics")
  async def metrics_endpoint() -> Response:
      payload, content_type = render_metrics()
      return Response(payload, media_type=content_type)
  ```
- Register the middleware near the other middleware wiring:
  ```python
  app.add_middleware(MetricsMiddleware)
  ```

- [ ] **Step 7: Run the unit tests**

```bash
uv run pytest tests/unit/test_health.py tests/unit/test_health_readiness.py tests/unit/test_obs_http_metrics.py -q
```
Expected: existing `test_health_ok` still passes (liveness unchanged); readiness 200/503 cases pass; route-template + `<unmatched>` cases pass.

**Commit:** `feat(obs): http RED middleware, /metrics endpoint, readiness /health split`

---

### Task 3: Langfuse client + settings service (OFF by default, no-op safe)

**Files:**
- Create: `src/paw/obs/langfuse_client.py`, `src/paw/services/langfuse_settings.py`
- Test: `tests/unit/test_obs_langfuse_noop.py`

**Interfaces:**
- `obs/langfuse_client.py`:
  - `@dataclass(frozen=True) class LangfuseConfig: enabled: bool; host: str; public_key: str; secret_key: str; redact_input: bool = False; sample_rate: float = 1.0`
  - `get_langfuse(cfg: LangfuseConfig) -> Langfuse | None` — returns `None` when `not cfg.enabled` or keys empty; otherwise constructs (and memoises by `(host, public_key, secret_key)`) a `Langfuse(...)`. Construction failure → logs once, returns `None` (never raises).
  - `trace_op(cfg, *, name: str, trace_id: str, metadata: dict[str, object]) -> OpTrace` — returns an `OpTrace`. When disabled, returns a **no-op** `OpTrace` whose methods do nothing.
  - `OpTrace.generation(*, model, op, usage, latency_s, cost_usd, input=None, output=None) -> None`, `OpTrace.span(*, name, metadata) -> None`, `OpTrace.flush() -> None` — all fire-and-forget, each wrapped in `try/except Exception: pass`. `flush()` calls `langfuse.flush()` best-effort and must not block the op or raise.
- `services/langfuse_settings.py`:
  - `class LangfuseSettingsService(session, *, box: SecretBox | None = None)` with `async def load() -> LangfuseConfig` (reads the four `app_settings` keys via `SettingsService`, decrypts `langfuse_secret_key_enc`; missing/blank → disabled config) and `async def save(*, enabled, host, public_key, secret_key) -> None` (encrypts the secret, merges into `app_settings`, commits via `SettingsService.update`).
  - `app_settings` keys (the 9a contract; 9b/9c must not repurpose these): `langfuse_enabled` (bool, default False), `langfuse_host` (str), `langfuse_public_key` (str), `langfuse_secret_key_enc` (str, Fernet token). Optional: `langfuse_redact_input` (bool), `langfuse_sample_rate` (float).

- [ ] **Step 1: Write the no-op + non-fatal tests (Docker-free)**

Create `tests/unit/test_obs_langfuse_noop.py`:
```python
from paw.obs.langfuse_client import LangfuseConfig, get_langfuse, trace_op


def _disabled() -> LangfuseConfig:
    return LangfuseConfig(enabled=False, host="", public_key="", secret_key="")


def test_disabled_returns_no_client():
    assert get_langfuse(_disabled()) is None


def test_disabled_trace_is_total_noop():
    trace = trace_op(_disabled(), name="ingest", trace_id="job-1", metadata={})
    # None of these may raise or require a network call.
    trace.generation(
        model="gpt-4o-mini", op="ingest", usage={"total_tokens": 5},
        latency_s=0.1, cost_usd=0.0,
    )
    trace.span(name="tool:search_wiki", metadata={})
    trace.flush()


def test_enabled_but_unreachable_never_raises():
    # Even "enabled" with a dead host must not raise from the helpers.
    cfg = LangfuseConfig(
        enabled=True, host="http://127.0.0.1:1", public_key="pk", secret_key="sk"
    )
    trace = trace_op(cfg, name="ingest", trace_id="job-2", metadata={"domain_id": "d"})
    trace.generation(
        model="gpt-4o", op="ingest", usage={"prompt_tokens": 1, "completion_tokens": 1},
        latency_s=0.2, cost_usd=0.01,
    )
    trace.flush()  # fire-and-forget; a dead endpoint must be swallowed
```
Run → fails (no module).

- [ ] **Step 2: Implement `obs/langfuse_client.py`**

Implement the dataclass + helpers. Key rules:
- Module-level cache `dict[tuple[str, str, str], "Langfuse"]` keyed by `(host, public_key, secret_key)` so repeated `get_langfuse` calls reuse one client.
- Lazy import `from langfuse import Langfuse` **inside** `get_langfuse` so the dep is only touched when enabled.
- `OpTrace` holds either a real langfuse trace object (or `None` for the no-op). Every method body is `if self._trace is None: return` then a `try: … except Exception: pass`.
- `generation(...)` maps to the langfuse generation API (e.g. `self._trace.generation(name=op, model=model, usage=…, metadata={"latency_s":…, "cost_usd":…})`) — **confirm the exact surface against the installed `langfuse>=2.50`** at implementation (`uv run python -c "import langfuse; print(langfuse.__version__)"`). The `OpTrace` adapter isolates any SDK drift so only this file changes if the API differs.
- `flush()` calls `client.flush()` guarded; never await network in the request/job hot path beyond this best-effort call.
- Honour `cfg.sample_rate` (skip creating a real trace when `random() >= sample_rate`) and `cfg.redact_input` (drop `input`/`output` payloads when set).

- [ ] **Step 3: Implement `services/langfuse_settings.py`**

```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.obs.langfuse_client import LangfuseConfig
from paw.security.secrets import SecretBox
from paw.services.settings import SettingsService


class LangfuseSettingsService:
    def __init__(self, session: AsyncSession, *, box: SecretBox | None = None) -> None:
        self._svc = SettingsService(session)
        self._box = box or SecretBox(get_settings().fernet_key)

    async def load(self) -> LangfuseConfig:
        s = await self._svc.get()
        enc = s.get("langfuse_secret_key_enc") or ""
        secret = self._box.decrypt(enc) if enc else ""
        return LangfuseConfig(
            enabled=bool(s.get("langfuse_enabled", False)),
            host=str(s.get("langfuse_host", "")),
            public_key=str(s.get("langfuse_public_key", "")),
            secret_key=secret,
            redact_input=bool(s.get("langfuse_redact_input", False)),
            sample_rate=float(s.get("langfuse_sample_rate", 1.0)),
        )

    async def save(self, *, enabled: bool, host: str, public_key: str, secret_key: str) -> None:
        current = await self._svc.get()
        current.update(
            {
                "langfuse_enabled": enabled,
                "langfuse_host": host,
                "langfuse_public_key": public_key,
                "langfuse_secret_key_enc": self._box.encrypt(secret_key) if secret_key else "",
            }
        )
        await self._svc.update(current)
```

- [ ] **Step 4: Run the unit tests + mypy**

```bash
uv run pytest tests/unit/test_obs_langfuse_noop.py -q && uv run mypy src
```
Expected: all pass; mypy clean (langfuse import is overridden).

**Commit:** `feat(obs): langfuse client (off by default, fire-and-forget) + settings service`

---

### Task 4: Instrumented providers (latency + cost + tokens + Langfuse generation span)

**Files:**
- Create: `src/paw/obs/instrument.py`
- Modify: `src/paw/jobs/tasks.py` (wrap providers per-op + per-op trace + domain counters + tool-call spans via `on_step`); optionally `src/paw/harness/tools.py` (+ `ToolContext.trace`) for finer tool-span granularity
- Test: `tests/integration/test_obs_instrument.py`

**Rationale (design decision):** there is **no single LLM call site** — `.chat()`/`.structured()` are invoked from `harness/ops/*`, `services/chat.py`, `services/query.py`, `providers/structured.py`; `.embed()` from `vector/*` and `ingest/chunking.py`. Editing each is invasive and easy to miss. Instead we wrap the **provider object** once where it is built per-op (the callers of `_build_providers` in `jobs/tasks.py`), so every downstream call is timed and costed uniformly. This is the cheapest correct seam and keeps `loop.py` untouched.

**Interfaces:**
- `obs/instrument.py`:
  - `class InstrumentedChatProvider` wrapping a `ChatProvider`; constructed `(inner, *, op: str, model: str, trace: OpTrace)`. `chat(...)` times the call; on success records `LLM_LATENCY{op}`, `LLM_TOKENS{op,"in"|"out"}`, `LLM_COST{op,model}` (via `compute_cost`), and `trace.generation(...)`; on exception increments `LLM_ERRORS{op}` and re-raises. Also exposes `structured(...)` (routed so its model round-trips go through the instrumented `chat`) and `stream(...)` (delegated). `__getattr__` forwards `chat_model` etc.
  - `class InstrumentedEmbeddingProvider` wrapping an `EmbeddingProvider`; `embed(...)` times the call, increments `EMBEDDINGS` by `len(texts)`, records `LLM_LATENCY{op}` and (embedding) `LLM_COST` if the model is in the table.
  - `def instrument_chat(inner, *, op, trace) -> InstrumentedChatProvider` and `instrument_embedding(inner, *, op, trace) -> InstrumentedEmbeddingProvider` — read `getattr(inner, "chat_model"/"embedding_model", "")` for the model label.
- `direction` label: `"in"` ← `usage["prompt_tokens"]`, `"out"` ← `usage["completion_tokens"]`; if the breakdown is absent, fall back to counting `total_tokens` as `"in"` only.

- [ ] **Step 1: Confirm the wrap site, do NOT change `factory.py`**

Keep `providers/factory.py` returning raw providers (it has no op context). Wrap in each `jobs/tasks.py` job body, which knows the op name (`"ingest"`, `"fix"`, `"format"`, `"reindex"`, `"lint"`). (`stream`-based chat in `services/chat.py`/`query.py` is request-path, not job-path; instrumenting those is out of 9a scope — noted in Risks.)

- [ ] **Step 2: Write the failing integration test**

Create `tests/integration/test_obs_instrument.py`:
```python
import pytest

from paw.obs import metrics
from paw.obs.instrument import instrument_chat
from paw.obs.langfuse_client import LangfuseConfig, trace_op
from paw.providers.base import ChatResult
from tests.stubs import StubChatProvider


def _sample(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _disabled_trace():
    return trace_op(
        LangfuseConfig(enabled=False, host="", public_key="", secret_key=""),
        name="ingest", trace_id="t", metadata={},
    )


async def test_chat_records_cost_tokens_latency():
    inner = StubChatProvider(
        script=[
            ChatResult(
                content="done",
                usage={"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
            )
        ]
    )
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    cost_before = _sample(metrics.LLM_COST, op="ingest", model="gpt-4o-mini")
    tokens_before = _sample(metrics.LLM_TOKENS, op="ingest", direction="in")
    await prov.chat([])
    assert _sample(metrics.LLM_COST, op="ingest", model="gpt-4o-mini") > cost_before
    assert _sample(metrics.LLM_TOKENS, op="ingest", direction="in") == tokens_before + 1000


async def test_chat_error_increments_error_counter():
    class Boom(StubChatProvider):
        async def chat(self, *a, **k):  # type: ignore[override]
            raise RuntimeError("provider down")

    inner = Boom()
    inner.chat_model = "gpt-4o"  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    before = _sample(metrics.LLM_ERRORS, op="ingest")
    with pytest.raises(RuntimeError):
        await prov.chat([])
    assert _sample(metrics.LLM_ERRORS, op="ingest") == before + 1


async def test_enabled_langfuse_outage_does_not_fail_call():
    inner = StubChatProvider(script=[ChatResult(content="ok", usage={"total_tokens": 1})])
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    # Enabled but pointing at a dead host: the generation span must be swallowed.
    trace = trace_op(
        LangfuseConfig(enabled=True, host="http://127.0.0.1:1", public_key="pk", secret_key="sk"),
        name="ingest", trace_id="job-z", metadata={"domain_id": "d"},
    )
    prov = instrument_chat(inner, op="ingest", trace=trace)
    result = await prov.chat([])   # must NOT raise despite dead Langfuse
    trace.flush()
    assert result.content == "ok"
```
(These exercise the cross-module instrument↔langfuse↔metrics seam and need no containers; placed in `integration/` for grouping but Docker-free. Move to `tests/unit/` if strict separation is preferred.) Run → fails.

- [ ] **Step 3: Implement `obs/instrument.py`**

Sketch for chat:
```python
from __future__ import annotations

import time
from typing import Any

from paw.obs import metrics
from paw.obs.cost import compute_cost
from paw.obs.langfuse_client import OpTrace
from paw.providers.base import ChatProvider, ChatResult, EmbeddingProvider


class InstrumentedChatProvider:
    def __init__(self, inner: ChatProvider, *, op: str, model: str, trace: OpTrace) -> None:
        self._inner = inner
        self._op = op
        self._model = model
        self._trace = trace

    async def chat(self, *args: Any, **kwargs: Any) -> ChatResult:
        start = time.perf_counter()
        try:
            result = await self._inner.chat(*args, **kwargs)
        except Exception:
            metrics.LLM_ERRORS.labels(op=self._op).inc()
            raise
        latency = time.perf_counter() - start
        usage = result.usage or {}
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        if not prompt and not completion:
            prompt = usage.get("total_tokens", 0)  # fall back: count all as "in"
        cost = compute_cost(self._model, usage)
        metrics.LLM_LATENCY.labels(op=self._op).observe(latency)
        metrics.LLM_TOKENS.labels(op=self._op, direction="in").inc(prompt)
        metrics.LLM_TOKENS.labels(op=self._op, direction="out").inc(completion)
        metrics.LLM_COST.labels(op=self._op, model=self._model).inc(cost)
        self._trace.generation(
            model=self._model, op=self._op, usage=usage, latency_s=latency, cost_usd=cost
        )
        return result

    async def structured(self, *args: Any, **kwargs: Any) -> Any:
        from paw.providers.structured import coerce_structured
        # Pass `self` so each model round-trip flows through the instrumented chat above.
        return await coerce_structured(self, *args, **kwargs)

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.stream(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # expose chat_model, supports_tools, etc.
        return getattr(self._inner, name)
```
**Important:** `OpenAICompatProvider.structured` calls `coerce_structured(self, …)` with `use_tools=self.supports_tools`. For the instrumented `structured` to route through the instrumented `chat`, call `coerce_structured(self, …)` (passing the wrapper). Confirm `coerce_structured`'s signature in `src/paw/providers/structured.py` at implementation and match its keyword args (`model`, `retries`, `use_tools`). If signatures don't line up cleanly, fall back to delegating `structured` to the inner provider and accept that structured-call cost is captured at the `coerce_structured`→`chat` boundary only when that inner `chat` is the wrapped one (it is not in that fallback) — prefer the route-through approach and assert cost on the ingest path in Task 5.

- [ ] **Step 4: Wire instrumented providers into the job bodies**

In `src/paw/jobs/tasks.py`, in each job body, after `chat, embedder, wiki, dim = await _build_providers(...)`, load the Langfuse config and wrap:
```python
from paw.harness.prompts import PROMPT_VERSION
from paw.obs import metrics
from paw.obs.instrument import instrument_chat, instrument_embedding
from paw.obs.langfuse_client import trace_op
from paw.services.langfuse_settings import LangfuseSettingsService

lf_cfg = await LangfuseSettingsService(data_s, box=box).load()
trace = trace_op(
    lf_cfg, name="ingest", trace_id=job_id,
    metadata={"domain_id": domain_id, "prompt_version": PROMPT_VERSION},
)
chat = instrument_chat(chat, op="ingest", trace=trace)
embedder = instrument_embedding(embedder, op="ingest", trace=trace)
```
After a successful `run_ingest` returns `result`, record domain counters:
```python
metrics.ARTICLES.inc()
metrics.CHUNKS.inc(result.chunk_count)
```
and call `trace.flush()` once at the end of the job (fire-and-forget). Use `op="fix"|"format"|"reindex"|"lint"` for the other jobs; only ingest writes ARTICLES/CHUNKS. Keep the change surgical — insert the wrap right after `_build_providers`, do not restructure the lock/commit logic.

- [ ] **Step 5: Wire tool-call spans (spec: "tool-call = span")**

The harness already has a per-tool-call seam: `run_loop(..., on_step=…)` invokes `on_step(step_n, tool_name)` after every tool dispatch (`loop.py` lines 74-75), and `run_ingest(..., on_step=…)` threads a progress callback. Emit a span there **without editing `loop.py`** by composing the trace into the op's `on_step`:
- In the ingest job body (Task 4 Step 4), the op already passes an `on_step`/progress callback into `run_ingest`. Wrap that callback so it also calls `trace.span(name=f"tool:{tool_name}", metadata={"step": step_n})`. Since `run_ingest`'s `on_step` signature is `(msg: str) -> Awaitable[None]` (it forwards the step label), pass the trace down and emit `trace.span(name=f"tool:{msg}", metadata={})` for each progress step — `trace.span` is fire-and-forget and a no-op when Langfuse is disabled, so this is free when off.
- If a finer tool-name granularity is wanted, thread the `OpTrace` onto `ToolContext` (add an optional `trace: OpTrace | None = None` field) and call `ctx.trace.span(name=f"tool:{name}")` inside `harness/tools.py::run_tool` (which runs on every tool call) — a two-line addition guarded by `if ctx.trace is not None`. Prefer this if the `on_step` label is too coarse; both satisfy the spec.
- Extend `tests/integration/test_obs_instrument.py` with a test that, given an **enabled** Langfuse `OpTrace` (pointed at a dead host), calling `trace.span(name="tool:search_wiki", metadata={})` does not raise — proving tool-call spans are wired and non-fatal. (A true end-to-end "span exported" assertion needs a live Langfuse and is left to manual/E2E; the no-op + non-fatal behaviour is what's unit-checked.)

- [ ] **Step 6: Run instrument tests + the existing ingest job tests**

```bash
uv run pytest tests/integration/test_obs_instrument.py -q
uv run pytest tests/integration -k ingest -q   # no regression in job bodies
```
Expected: cost/tokens/latency recorded; error counter increments; enabled-but-dead Langfuse does not raise; existing ingest job tests still pass (and the instrumented `structured` path on ingest records cost).

**Commit:** `feat(obs): instrumented chat/embedding providers; wire LLM latency+cost+langfuse into jobs`

---

### Task 5: Worker metrics server + arq job/lock/queue metrics

**Files:**
- Modify: `src/paw/worker.py`, `src/paw/jobs/tasks.py`, `src/paw/jobs/locks.py`
- Test: `tests/integration/test_worker_metrics.py`

**Interfaces:**
- `worker.py`: `WorkerSettings.on_startup` starts `prometheus_client.start_http_server(port)` when `get_settings().worker_metrics_port > 0` (guarded in try/except so a bind failure never crashes the worker; default 0 means tests never bind a port). It also seeds `QUEUE_DEPTH` once at startup; the per-job helper refreshes it (so the gauge is never left always-zero).
- `jobs/tasks.py`: each job body records exactly one `JOB_TOTAL{kind,status}` (status = the returned `"succeeded"|"failed"|"cancelled"`) + one `JOB_DURATION{kind}` per invocation; reads `ctx.get("job_try", 1)` and increments `JOB_RETRIES{kind}` when `job_try > 1`; increments `JOB_DEADLETTER{kind}` when the body returns `"failed"` on the final attempt (`job_try >= max_tries`).
- `jobs/locks.py`: `model_lock` accepts an optional `kind: str = "unknown"`; the time spent in the acquisition loop is observed into `JOB_LOCK_WAIT{kind}`.
- `QUEUE_DEPTH`: populated via `async def set_queue_depth(redis) -> None` (in `obs/metrics.py` or `worker.py`) reading the arq queue length — `await redis.zcard(arq.constants.default_queue_name)` (the arq queue is a Redis sorted set; default key `"arq:queue"`) — and calling `metrics.QUEUE_DEPTH.set(n)`. Guarded in try/except so a Redis hiccup never fails startup or a job.

- [ ] **Step 1: Add a job-metrics helper to `jobs/tasks.py`**

The job bodies **catch their own exceptions and return a status string** (they don't raise), so the helper records from the returned value. Add near the top:
```python
import time

from paw.obs import metrics


def _record_job(kind: str, ctx: dict[str, Any], status: str, started: float) -> str:
    try_n = int(ctx.get("job_try", 1) or 1)
    if try_n > 1:
        metrics.JOB_RETRIES.labels(kind=kind).inc()
    if status == "failed":
        max_tries = int(ctx.get("max_tries", 1) or 1)
        if try_n >= max_tries:
            metrics.JOB_DEADLETTER.labels(kind=kind).inc()
    metrics.JOB_DURATION.labels(kind=kind).observe(time.perf_counter() - started)
    metrics.JOB_TOTAL.labels(kind=kind, status=status).inc()
    return status
```

- [ ] **Step 2: Apply the helper to each job body**

In `ingest_domain`, `lint_domain`, `fix_issues`, `format_articles`, `reindex_domain` (and optionally `gc_housekeeping` as `kind="gc"`): capture `started = time.perf_counter()` at the top, and replace each `return "<status>"` with `return _record_job("<kind>", ctx, "<status>", started)` (where `ctx` is the arq `ctx` dict the function already receives, `<kind>` is the job name). This records exactly one counter + one duration per invocation regardless of the success/failed/cancelled branch. Keep it mechanical — do not change the surrounding lock/commit/publish logic.

- [ ] **Step 3: Instrument `model_lock` wait in `jobs/locks.py`**

```python
@asynccontextmanager
async def model_lock(
    redis: Any, model: str, *, kind: str = "unknown",
    ttl: int = 600, poll: float = 0.05, timeout: float = 120.0,
) -> AsyncIterator[None]:
    from paw.obs import metrics
    key = f"lock:model:{model}"
    deadline = time.monotonic() + timeout
    wait_start = time.monotonic()
    while not await redis.set(key, "1", nx=True, ex=ttl):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"model lock timeout: {model}")
        await asyncio.sleep(poll)
    metrics.JOB_LOCK_WAIT.labels(kind=kind).observe(time.monotonic() - wait_start)
    try:
        yield
    finally:
        await redis.delete(key)
```
Pass `kind=<job name>` from each `model_lock(...)` call site in `jobs/tasks.py` (e.g. `async with model_lock(redis, getattr(chat, "chat_model", "default"), kind="ingest"):`). Lazy-import `paw.obs.metrics` inside the function to keep `jobs.locks` import-light.

- [ ] **Step 4: Start the worker metrics server + seed queue depth in `on_startup`**

In `src/paw/worker.py`:
```python
from paw.config import get_settings
from paw.obs import metrics


async def set_queue_depth(redis: Any) -> None:
    try:
        import arq.constants
        n = await redis.zcard(arq.constants.default_queue_name)  # arq queue = sorted set
        metrics.QUEUE_DEPTH.set(n)
    except Exception:  # noqa: BLE001
        pass  # gauge update must never fail startup or a job


class WorkerSettings:
    functions = [...]  # unchanged
    redis_settings = _LazyRedisSettings()

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        port = get_settings().worker_metrics_port
        if port > 0:
            try:
                from prometheus_client import start_http_server
                start_http_server(port)
            except Exception:  # noqa: BLE001
                pass  # metrics must never crash the worker
        await heartbeat(ctx)
        await reconcile_jobs(ctx)
        await set_queue_depth(ctx["redis"])
```
Confirm the arq queue-name constant name against the installed `arq>=0.26` (`uv run python -c "import arq.constants as c; print([x for x in dir(c) if 'queue' in x.lower()])"`); the default queue Redis key is `"arq:queue"`. Also call `await set_queue_depth(ctx["redis"])` at the top of each job body (right after acquiring `redis`) so the gauge tracks depth as jobs drain — keep it one guarded line.

- [ ] **Step 5: Write + run the worker-metrics integration test**

Create `tests/integration/test_worker_metrics.py` invoking one job body directly (e.g. `ingest_domain` with a seeded domain + stub providers, reusing the seeding pattern from the existing `tests/integration/test_jobs*.py` ingest test) and assert `paw_job_total{kind="ingest",status="succeeded"}` increased and `paw_job_duration_seconds_count{kind="ingest"}` increased (read deltas before/after). Add a second test that enqueues N jobs into a live test Redis, calls `set_queue_depth(redis)`, and asserts `metrics.QUEUE_DEPTH._value.get() == N` (then drains and re-asserts 0) — this proves the gauge is populated, not always-zero.
```bash
uv run pytest tests/integration/test_worker_metrics.py -q
```
Expected: the job-completion counters increment for the exercised job; `paw_queue_depth` reflects the live arq queue length.

**Commit:** `feat(obs): worker /metrics server + arq job/duration/retry/deadletter/lock-wait metrics`

---

### Task 6: Cache-hit + active-SSE instrumentation (close the remaining domain counters)

**Files:**
- Modify: `src/paw/services/query_cache.py` (cache hit/miss), `src/paw/api/routers/chat.py` + `src/paw/api/routers/query.py` (SSE-active gauge)
- Test: `tests/integration/test_obs_cache_metric.py` (or extend an existing query-cache test)

**Interfaces:**
- `paw_cache_hits_total{result="hit"|"miss"}` incremented at the single query-cache lookup decision point.
- `paw_sse_active` inc on entering an SSE stream, dec on exit (chat + query routers) — guard with try/finally so a disconnect always decrements.

- [ ] **Step 1: Locate the cache lookup decision** in `services/query_cache.py` (the method that returns a cached answer or `None`). Increment `metrics.CACHE_HITS.labels(result="hit").inc()` on hit and `...("miss").inc()` on miss. Two lines at the existing branch; do not restructure caching.

- [ ] **Step 2: Wrap the SSE generators.** In `api/routers/chat.py::_sse` (line ~65) and the analogous streamer in `api/routers/query.py` (around line ~116), bracket the `async for` with:
```python
from paw.obs import metrics
metrics.SSE_ACTIVE.inc()
try:
    async for tok in ...:
        yield ...
finally:
    metrics.SSE_ACTIVE.dec()
```
- [ ] **Step 3: Embeddings counter** is already incremented by `InstrumentedEmbeddingProvider.embed` (Task 4) on the job path. A request-path embed (query embedding via `vector/embed_cache.py`) is NOT instrumented in 9a (request-path scope kept minimal) — noted in Risks, not done here.

- [ ] **Step 4: Test the cache metric AND the SSE gauge**

Add `tests/integration/test_obs_cache_metric.py` (or extend a query-cache test) asserting a cold query records `result="miss"` and a repeated identical query records `result="hit"` (read deltas before/after).

Also assert the `paw_sse_active` gauge. The simplest unit-level check, since the inc/dec live in the router's SSE generator, is to extract the gauge wrapping into a tiny async generator helper (or test the route via httpx with an `Accept: text/event-stream` request against a seeded chat session): read `metrics.SSE_ACTIVE._value.get()` before, drive the stream to completion, and assert the gauge returns to its starting value (net inc/dec balanced, so a disconnect never leaks). If wiring a full streaming request is too heavy for this lane, assert the balance by directly exercising the wrapped generator helper. The gauge must not ship without at least the balanced inc/dec assertion.
```bash
uv run pytest -k "cache and obs" -q
uv run pytest -k "sse and obs" -q
```

**Commit:** `feat(obs): cache hit/miss + active-SSE gauge instrumentation`

---

### Task 7: Opt-in `observability` compose profile + vendored Prometheus/Grafana config

**Files:**
- Create: `deploy/observability/prometheus.yml`, `deploy/observability/grafana/provisioning/datasources/prometheus.yml`, `deploy/observability/grafana/provisioning/dashboards/paw.yml`, `deploy/observability/grafana/dashboards/paw-overview.json`
- Modify: `docker-compose.yml` (Traefik prometheus metrics + profiled stack + worker metrics env), `.env.example`
- Verify: `docker compose config` with/without the profile (Step 5)

**Decision — single compose file with `profiles:`** (not a second file): add the observability services to `docker-compose.yml` under `profiles: [observability]`. Per Docker Compose semantics, a service with a non-empty `profiles:` list is **not started** unless that profile is activated, so plain `docker compose up` is unaffected (spec acceptance #2). One source of truth, no `-f` juggling.

- [ ] **Step 1: Enable Traefik Prometheus metrics (always-on, unpublished)**

In `docker-compose.yml` `traefik.command`, add:
```yaml
      - "--metrics.prometheus=true"
      - "--metrics.prometheus.entrypoint=metrics"
      - "--entrypoints.metrics.address=:8082"
```
Traefik then exposes `/metrics` on `:8082`, **not published to the host** — Prometheus scrapes it over the compose network. No new host attack surface on plain `up`.

- [ ] **Step 2: Set the worker metrics port + add the profiled stack**

- On the existing `worker` service env, add `WORKER_METRICS_PORT: 9100` (the port stays unpublished; the config default is 0 so non-compose runs stay disabled). The worker `on_startup` (Task 5) reads `worker_metrics_port` and binds 9100 inside the compose network.
- Append services, each with `profiles: ["observability"]`:
  - `prometheus` (`prom/prometheus`), mounts `./deploy/observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro`, volume `promdata:/prometheus`.
  - `grafana` (`grafana/grafana`), env `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}`, mounts the provisioning + dashboards dirs read-only, volume `grafanadata:/var/lib/grafana`.
  - `postgres_exporter` (`prometheuscommunity/postgres-exporter`), env `DATA_SOURCE_NAME=postgresql://REDACTED:${POSTGRES_PASSWORD:-paw}@postgres:5432/paw?sslmode=disable`.
  - `redis_exporter` (`oliver006/redis_exporter`), env `REDIS_ADDR=redis://redis:6379`.
  - `cadvisor` (`gcr.io/cadvisor/cadvisor`), `privileged: true`, standard read-only host mounts (`/:/rootfs:ro`, `/var/run:/var/run:ro`, `/sys:/sys:ro`, `/var/lib/docker/:/var/lib/docker:ro`).
- Add named volumes `promdata`, `grafanadata` to the `volumes:` block. Do **not** add `profiles:` to traefik/postgres/redis/api/worker/init — they must keep starting on plain `up`.

- [ ] **Step 3: Vendor `deploy/observability/prometheus.yml`**

```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: paw-api
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]
  - job_name: paw-worker
    static_configs:
      - targets: ["worker:9100"]   # WORKER_METRICS_PORT
  - job_name: traefik
    static_configs:
      - targets: ["traefik:8082"]
  - job_name: postgres
    static_configs:
      - targets: ["postgres_exporter:9187"]
  - job_name: redis
    static_configs:
      - targets: ["redis_exporter:9121"]
  - job_name: cadvisor
    static_configs:
      - targets: ["cadvisor:8080"]
```

- [ ] **Step 4: Vendor Grafana provisioning + a minimal dashboard**

- `datasources/prometheus.yml`: one Prometheus datasource at `http://prometheus:9090`, `isDefault: true`.
- `dashboards/paw.yml`: a file-provider pointing at `/var/lib/grafana/dashboards` (mount `./deploy/observability/grafana/dashboards` there).
- `paw-overview.json`: minimal dashboard with panels for: HTTP request rate `sum(rate(paw_http_requests_total[5m]))`; error rate (`status=~"5.."`); p95 latency `histogram_quantile(0.95, sum(rate(paw_http_request_duration_seconds_bucket[5m])) by (le, route))`; job throughput by status `sum(rate(paw_job_total[5m])) by (kind, status)`; LLM cost `sum(increase(paw_llm_cost_usd_total[1h])) by (model)`; cache hit-rate `sum(rate(paw_cache_hits_total{result="hit"}[5m])) / sum(rate(paw_cache_hits_total[5m]))`. Keep it small (a handful of panels — YAGNI).

- [ ] **Step 5: Verify both modes with `docker compose config`**

```bash
# Plain mode must NOT include the observability services:
docker compose config --services | sort
# expect: api init postgres redis traefik worker  (NO prometheus/grafana/exporters)

# Profile mode must include them:
docker compose --profile observability config --services | sort
# expect: + cadvisor grafana postgres_exporter prometheus redis_exporter
```
Both `docker compose config -q` and the profile variant must parse without error. (Run where Docker is available; otherwise these two commands are the documented manual acceptance check for spec criterion #2.)

- [ ] **Step 6: Document env in `.env.example`**

Add `WORKER_METRICS_PORT=9100` (with a note that 0 disables it outside compose), `GRAFANA_ADMIN_PASSWORD=` (required when running the profile), and a comment that `--profile observability` is opt-in and `cadvisor` runs privileged.

**Commit:** `feat(obs): opt-in observability compose profile + vendored prometheus/grafana config`

---

### Task 8: Langfuse settings via the settings API (+ optional minimal admin field)

**Files:**
- Verify/extend: `src/paw/api/routers/settings.py` (existing settings PUT path) + an API test.
- Optional: the admin settings template + its handler (locate under `src/paw/api/web/`).

**Scope guard:** the **primary** path to configure Langfuse is the existing settings PUT API, which already persists arbitrary `app_settings` keys — the four `langfuse_*` keys flow through with no new code. A template form field is **secondary/optional**. Do **not** build i18n or rework the settings template (that is 9c). Only add a minimal `enabled`/`host`/`public_key`/`secret_key` field group if it is a few lines on the existing form.

- [ ] **Step 1: Prove the round-trip end-to-end.** Add an API test (under `tests/api/`): authenticate as admin, persist Langfuse config (preferably via a thin endpoint that calls `LangfuseSettingsService.save(...)`; if no such endpoint exists, store `langfuse_secret_key_enc` directly through the existing settings PUT after encrypting), then assert `LangfuseSettingsService(session).load()` returns `enabled=True`, the right host/public_key, and the **decrypted** secret. This proves the 9a config contract without any UI.

- [ ] **Step 2 (optional, only if cheap):** add the four fields to the admin settings form + handler, encrypting the secret via `LangfuseSettingsService.save`. Skip if it would require touching the template's i18n/structure (deferred to 9c).

```bash
uv run pytest tests/api -k settings -q
```
Expected: the Langfuse config round-trips through the settings API and decrypts correctly.

**Commit:** `feat(obs): persist langfuse app_settings via settings API (+ optional admin field)`

---

### Task 9: Docs refresh + full CI gate

**Files:**
- Modify: `docs/wiki/*` (via iwiki)
- Verify: full CI

- [ ] **Step 1: Run the full CI gate**
```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```
All three must pass (unit lane runs Docker-free; integration/api/e2e need Docker — run where available).

- [ ] **Step 2: Regenerate wiki docs for the new `paw.obs` package + observability ops**
```
iwiki:iwiki-ingest src/paw/obs
iwiki:iwiki-ingest src/paw/services/langfuse_settings.py
/iwiki-lint
```
Document: the `/metrics` vs `/health` (+`/ready`) split, the `observability` compose profile (opt-in), the Langfuse config keys + OFF-by-default fire-and-forget behaviour, and how to extend `MODEL_COSTS`. Expected: `/iwiki-lint` reports no broken `[[refs]]`, no orphan/stale pages.

**Commit:** `docs(wiki): document Phase 9a observability (metrics, health, langfuse, compose profile)`

---

## Acceptance Criteria → Coverage Map

| Spec acceptance (Phase 9, 9a-owned) | Tasks | Verifying test / check |
| --- | --- | --- |
| #1 `/metrics` exposes api RED + worker arq + domain/token/cost counters | 1,2,4,5,6 | `test_obs_metrics_render`, `test_obs_http_metrics`, `test_obs_instrument`, `test_worker_metrics`, `test_metrics_endpoint` (live content) |
| #1 `/health` separate + reports readiness | 2 | `test_health_readiness` (liveness 200, readiness 200/503), `test_health` (unchanged liveness) |
| #2 `docker compose --profile observability up` brings the stack; plain `up` unaffected | 7 | `docker compose config --services` with/without the profile (Step 5) |
| #3 Langfuse OFF by default; enabled → op trace with per-LLM generation spans; killing Langfuse does not fail the op | 3,4 | `test_obs_langfuse_noop` (no-op + dead-host non-fatal), `test_obs_instrument::test_enabled_langfuse_outage_does_not_fail_call` |

(Phase-9 acceptance #4 backups, #5 SSRF/zip-bomb/upload, #6 loaders, #7 i18n/admin-keys are owned by sub-plans 9d/9b/9c — out of scope here.)

## Tests → Spec Map

- **Unit (Docker-free):** metric label cardinality + render (`test_obs_metrics_render`, `test_obs_http_metrics` route-template + `<unmatched>`); Langfuse no-op when disabled (`test_obs_langfuse_noop`); cost table (`test_obs_cost`); readiness split (`test_health_readiness`).
- **Integration (testcontainers + stubs):** `/metrics` content (`test_metrics_endpoint`); instrumented provider cost/tokens/latency/error + non-fatal Langfuse + non-fatal tool-call span (`test_obs_instrument`); worker job counters + `paw_queue_depth` reflects live arq queue length (`test_worker_metrics`); cache hit/miss + balanced `paw_sse_active` inc/dec (`test_obs_cache_metric`).
- **API:** settings API persists `langfuse_*` keys and `LangfuseSettingsService.load()` round-trips with a decrypted secret (Task 8).
- **E2E / manual (where Docker available):** `docker compose --profile observability up` + Prometheus scrape of `api:8000/metrics`, `worker:9100/metrics`.

## Risks / Notes

- **Single LLM seam is the provider wrapper, not `loop.py`.** Request-path streaming chat (`services/chat.py`, `services/query.py` via `.chat()/.stream()`) and the request-path query embedding (`vector/embed_cache.py`) are **not** instrumented in 9a (job-path only). If full request-path LLM cost is later required, wrap those providers too — noted, not done, to keep 9a surgical.
- **`structured()` cost capture** depends on routing `coerce_structured(self, …)` through the instrumented wrapper (Task 4 Step 3). Verify against `providers/structured.py`'s actual signature at implementation; the ingest path uses `chat.structured`, so Task 5's ingest test is the guard that structured-call cost is actually recorded. If route-through proves awkward, fall back to instrumenting at the `coerce_structured` boundary.
- **Tool-call spans are emitted via the op's `on_step` callback, not by editing `loop.py`** (Task 4 Step 5). The span carries the step label / tool name only (lightweight); the rich per-LLM detail (model/tokens/latency/cost) lives on the generation span. A live "span exported to Langfuse" assertion needs a real Langfuse server and is an E2E/manual check; the unit/integration lane verifies only the no-op-when-disabled + non-fatal-when-unreachable behaviour. If finer per-tool granularity is needed, thread `OpTrace` onto `ToolContext` and span inside `run_tool` (called out in Task 4).
- **`paw_queue_depth` is set from the live arq queue** (`redis.zcard` on the arq queue sorted set) at worker startup and at the top of each job, so it is never an always-zero gauge. Confirm the arq queue-name constant against the installed `arq>=0.26` at implementation; the default Redis key is `"arq:queue"`.
- **`prometheus_client` default registry is process-global**; unit tests assert **deltas** (read-before/read-after), never absolute values, to stay order-independent. Do not call `REGISTRY.clear()` between tests.
- **`request.scope["route"]`** is only set after Starlette routing; on 404 it is absent → `<unmatched>`. `MetricsMiddleware` reads it *after* `call_next`, so routing has run — keep it that way (don't move the read before `call_next`).
- **Worker metrics server binds a port**; gated behind `worker_metrics_port` (default 0) so tests and plain local runs never bind. Compose sets `9100` under the profile (unpublished).
- **cAdvisor needs `privileged: true` + host mounts**; it only runs under the `observability` profile, so plain `up` is unaffected. Document it is opt-in and host-specific.
- **Langfuse SDK surface** (`Langfuse(...).trace(...).generation(...)`, `.flush()`) must be confirmed against the installed `langfuse>=2.50` at implementation; the `OpTrace` adapter isolates any API drift so only `obs/langfuse_client.py` changes if the surface differs.
- **Dead-letter detection** relies on arq's `ctx["job_try"]` / `ctx["max_tries"]`; confirm these keys exist in the installed arq (`>=0.26`). If `max_tries` is absent from `ctx`, read it from `WorkerSettings.max_tries` (arq default is 5 unless overridden) and pass it into `_record_job` — adjust the helper accordingly.
- **No new migration:** Langfuse config lives in the existing `app_settings` JSONB singleton; no schema change, matching the spec ("No new core tables").

## Self-Review (pre-implementation)

- **Structure:** 9 tasks, each with Files / Interfaces / checkbox Steps / a runnable verify command / a commit. Matches the repo's existing plan format (Phase 8). PASS.
- **Coverage:** every 9a-owned spec bullet (metrics api RED + worker arq by type/status + duration + **queue depth** + retries + dead-letter + lock-wait; domain/token/cost; `/health` split; wire latency+cost which did NOT pre-exist; opt-in compose profile + exporters + vendored dashboards; Langfuse off-by-default fire-and-forget, op=trace, per-LLM=**generation span**, **tool-call=span**) maps to ≥1 task and ≥1 test (see Coverage Map). The "wire instrumentation supposedly added earlier" reality gap is handled explicitly: Task 4 ADDS latency timing + cost (only token counts pre-existed via `Budget.add_tokens`). `paw_queue_depth` is actively populated (not always-zero) and tool-call spans are wired via `on_step` — the two check-plan WARNINGs (F-001/F-002) and the SSE-gauge INFO (F-003) are all resolved (`verdict: fixed`). PASS.
- **Dependencies:** Task order is buildable — registry+cost (1) → http/health (2) → langfuse (3) → instrument depends on 1+3 (4) → worker/jobs depend on 1+4 (5) → cache/SSE (6) → compose (7) → settings (8) → docs/CI (9). No forward references. PASS.
- **Verifiability:** each task ends in a concrete `uv run pytest …` / `docker compose config` check with an expected outcome; acceptance #1/#2/#3 each have a named asserting test. PASS.
- **Consistency:** metric names, label sets, and `app_settings` keys are identical everywhere they appear (registry ↔ instrument ↔ compose dashboard queries ↔ langfuse settings). Layering rule stated and respected (`obs.metrics/cost/http` import no `paw.services`; `obs.instrument/langfuse_client` import providers/security/config only; `obs.readiness` is an allowed infra probe). PASS.
- **Open risks flagged honestly:** request-path LLM instrumentation, `structured()` cost routing, arq `max_tries` key, and the Langfuse SDK surface are called out in Risks rather than silently assumed. PASS.
