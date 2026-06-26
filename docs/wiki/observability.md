# Observability

## Overview
The `paw.obs` package centralises production observability: a Prometheus metric
registry (`paw_*`), an HTTP RED middleware, a liveness/readiness `/health` split
plus `/metrics`, a per-model LLM cost table, instrumented chat/embedding providers
(latency + cost + tokens), worker arq job/lock/queue metrics, and an OFF-by-default
fire-and-forget Langfuse client. An opt-in `observability` Docker Compose profile
ships Prometheus + Grafana + exporters. Observability never changes behaviour: every
metric and trace call is guarded so a failure can never alter a response, job, or op.

## Purpose
`paw.obs` exists so api and worker are measurable in production without scattering
metric calls across call sites. It is a leaf-ish utility layer: `obs.metrics`/`obs.cost`/
`obs.http` import only stdlib + `prometheus_client`/`starlette`; `obs.instrument`/
`obs.langfuse_client` may import `paw.providers`/`paw.config`/`paw.security` but never
`paw.api`/`paw.services`. The api/worker/jobs layers import `paw.obs`, never the reverse
(see [[architecture#Layered dependencies (no cycles)]]).

## Metric registry
`obs/metrics.py` declares every collector once on the default registry (`paw_` prefix)
and `render_metrics() -> (bytes, content_type)`. Counters: `paw_http_requests_total`,
`paw_job_total`, `paw_job_retries_total`, `paw_job_deadletter_total`, `paw_llm_tokens_total`,
`paw_llm_cost_usd_total`, `paw_llm_errors_total`, `paw_embeddings_total`, `paw_articles_total`,
`paw_chunks_total`, `paw_cache_hits_total`. Histograms: `paw_http_request_duration_seconds`,
`paw_job_duration_seconds`, `paw_job_lock_wait_seconds`, `paw_llm_latency_seconds`. Gauges:
`paw_http_inflight`, `paw_sse_active`, `paw_queue_depth`. The registry is process-global, so
tests assert deltas (read-before/after), never absolutes, and never call `REGISTRY.clear()`.

## Cost table
`obs/cost.py` holds `MODEL_COSTS: dict[str, tuple[float, float]]` — USD per 1K (prompt,
completion) tokens for a few seeded models — and `compute_cost(model, usage) -> float`.
Unknown model returns `0.0`; missing `prompt_tokens`/`completion_tokens` keys are treated
as 0; embedding models bill prompt-side only. The table is intentionally small (YAGNI): to
price a new model, add one entry `"<model>": (<prompt_rate>, <completion_rate>)` — no other
code changes. Token usage comes from `ChatResult.usage` (see [[providers#Chat provider]]).

## HTTP RED middleware
`obs/http.py::MetricsMiddleware` (a Starlette `BaseHTTPMiddleware`, wired in
[[architecture#create_app() wiring]]) records Rate/Errors/Duration per request: it
increments `paw_http_inflight`, times the request, and records `paw_http_requests_total`
+ `paw_http_request_duration_seconds` keyed by the **route template**
(`request.scope["route"].path`, e.g. `/api/v1/domains/{domain_id}/sources`), never the raw
path — `domain_id` is not a label, keeping cardinality bounded. An unmatched request (404)
is labelled `route="<unmatched>"`. The middleware reads the route after `call_next` (routing
has run) and records in a `finally`, so it never raises into the response.

## Health & readiness
`/health` is a trivial liveness probe (always `200 {"status":"ok"}`, no I/O) so the
compose healthcheck and existing tests are unchanged. Readiness is `/health?ready=1` (and
the `/ready` alias): it calls `obs/readiness.py::check_readiness()`, which runs `SELECT 1`
via the sessionmaker and pings Redis, returning `(ok, {"db":..., "redis":...})` and HTTP
`503` when any dependency is down. `/metrics` returns the Prometheus exposition payload from
`render_metrics()`. The `/health` handler calls `readiness` as a module attribute so tests
can monkeypatch the check.

## Instrumented providers
`obs/instrument.py` wraps the provider objects (not each call site): `InstrumentedChatProvider`
and `InstrumentedEmbeddingProvider`, built via `instrument_chat(inner, *, op, trace)` /
`instrument_embedding(...)`. `chat()` times the call, records `paw_llm_latency_seconds`,
`paw_llm_tokens_total{direction}` (in/out from usage, falling back to `total_tokens` as "in"),
`paw_llm_cost_usd_total` via `compute_cost`, and emits a Langfuse generation span; on failure it
increments `paw_llm_errors_total` and re-raises (behaviour unchanged). `structured()` routes
through the wrapped `chat` by passing the wrapper to `coerce_structured` (see
[[providers#Structured output]]). Wrapping happens per-op in the job bodies, the single LLM seam —
`loop.py` and `factory.py` stay untouched (see [[harness#The agentic loop]]).

## Langfuse tracing
`obs/langfuse_client.py` is an OFF-by-default, fire-and-forget tracing adapter. `get_langfuse(cfg)`
returns `None` when disabled or keys are empty (the SDK is only imported when enabled) and
memoises the client by `(host, public_key, secret_key)`. `trace_op(cfg, *, name, trace_id,
metadata) -> OpTrace` returns a no-op trace when disabled; `OpTrace.generation(...)`, `.span(...)`,
and `.flush()` each wrap real SDK calls in `try/except: pass`, so a Langfuse outage can never fail
an op. The adapter targets the installed `langfuse>=4` API (`start_observation(as_type=...)`,
`usage_details`/`cost_details`, `client.flush()`); the `OpTrace` seam isolates SDK drift. The op
is a trace, each LLM call a generation span, and each tool step a span (emitted via the op's
`on_step` callback, not by editing the loop).

## Langfuse settings
`services/langfuse_settings.py::LangfuseSettingsService` reads/writes the Langfuse config in the
`app_settings` JSONB singleton (see [[services#SettingsService & SetupService]]). `load() ->
LangfuseConfig` decrypts `langfuse_secret_key_enc` via `SecretBox` (see [[providers#Secrets]]);
a missing/blank secret yields a disabled config. `save(*, enabled, host, public_key, secret_key)`
encrypts the secret and commits through `SettingsService.update`. The 9a config keys are
`langfuse_enabled`, `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key_enc` (Fernet
token), plus optional `langfuse_redact_input` and `langfuse_sample_rate`. The keys round-trip
through the admin settings PUT API; no new table or migration is needed.

## Worker & job metrics
The worker starts a `prometheus_client` HTTP server in `on_startup` only when
`worker_metrics_port > 0` (default 0, so tests and plain local runs never bind), guarded so a bind
failure can never crash the worker. `set_queue_depth(redis)` reads `redis.zcard("arq:queue")` (arq's
default queue sorted set) into `paw_queue_depth` at startup and at the top of each job, so the gauge
is never always-zero. Each job body records exactly one `paw_job_total{kind,status}` +
`paw_job_duration_seconds{kind}` via `_record_job`, which also bumps `paw_job_retries_total` (when
`job_try > 1`) and `paw_job_deadletter_total` (failed on the final try, `max_tries` default 5);
`_record_job` is fully guarded so metrics never change a job's outcome. `model_lock` observes
acquisition wait into `paw_job_lock_wait_seconds{kind}` (see [[jobs#Locks]]). Cache lookups record
`paw_cache_hits_total{result}` (see [[services#QueryCacheService]]); SSE generators bracket
`paw_sse_active` with inc/`finally`-dec so a disconnect never leaks the gauge.

## Compose profile
The metrics stack is opt-in: `docker compose up` is unchanged, while `docker compose --profile
observability up` adds `prometheus`, `grafana`, `postgres_exporter`, `redis_exporter`, and
`cadvisor` (all carry `profiles: ["observability"]`). Traefik exposes Prometheus metrics on an
unpublished `:8082` entrypoint; the worker binds `WORKER_METRICS_PORT=9100` inside the compose
network. Scrape config and a minimal RED + jobs + LLM-cost Grafana dashboard are vendored under
`deploy/observability/`. `GRAFANA_ADMIN_PASSWORD` must be set when running the profile (Grafana
falls back to its `admin` default otherwise); `cadvisor` runs privileged. No observability port
is published to the host on a plain `up`.
