# Jobs & Worker

## Overview
Long-running LLM work runs out-of-band in the `arq` worker. `worker.py::WorkerSettings` registers the job functions; `jobs/tasks.py` implements them with a two-session pattern (one session for job status/progress, one as the single data-commit boundary). Redis locks serialize per-domain and per-model work, `progress.py` streams steps over pub/sub, and `queue.py` enqueues jobs. Stuck jobs are reconciled on startup. See [[architecture#Two processes, one image]], [[services#The commit-boundary rule]], [[harness#Ops]].

## Worker jobs
`WorkerSettings.functions` (`worker.py`) registers the arq tasks; `on_startup` runs `heartbeat` then `reconcile_jobs`, and `redis_settings` is a lazy descriptor reading `get_settings().redis_url`.

- `ingest_domain` — load a source/topic, run `run_ingest`, write the article ([[harness#Ops]]).
- `lint_domain` — run `run_lint`, log the issue list (read-only, no `data_s.commit`).
- `fix_issues` — re-lint, then `run_fix_issue` for each selected issue id.
- `format_articles` — `run_format_article` over every article in the domain.
- `reindex_domain` — re-embed chunks to the target embedding version in batches.
- `gc_housekeeping` — prune chat sessions past each user's retention; Phase 7 adds a query-cache TTL sweep that loops over domains and calls `QueryCacheRepo.delete_expired(cutoff, domain_id=...)` with each domain's resolved `ttl_seconds` (global ⊕ per-domain), so per-domain TTL overrides are honored (see [[db#Repo pattern]]). Return value `"gc:{pruned}"` is unchanged.
- `heartbeat` — write `paw:worker:heartbeat` to Redis (`ex=120`) as a liveness marker.
- `reconcile_jobs` — `JobRepo.reconcile_stuck` to fail jobs with stale heartbeats.

## Two-session pattern
Each mutating job opens two sessions from the same maker: `async with maker() as job_s, maker() as data_s`. `job_s` (wrapped by `JobRepo`) owns status, heartbeats, the log and progress — it commits frequently so the UI sees live updates. `data_s` is the **single data-commit boundary**: all article/chunk/graph writes batch there and commit exactly once on success. See [[services#The commit-boundary rule]].

- On success: `await data_s.commit()` → `jobs.set_status("succeeded")` → `job_s.commit()`.
- On cancel/error: `await data_s.rollback()`, then the status flip is committed on `job_s` alone — partial data work never lands.
- `_safe_publish` swallows Redis errors so a progress hiccup never changes job status.

## Locks
`jobs/locks.py` provides two Redis-backed async context-manager locks via `SET NX EX`.

- `domain_lock(redis, domain_id, ttl=3600)` — yields a `bool`; only one mutating job per domain runs at a time. If not acquired the job sets status `failed` with `error="domain busy"` and returns early. Released (`DELETE`) on exit only if it was acquired.
- `model_lock(redis, model, ttl=600, timeout=120)` — serializes concurrent LLM calls against a given chat model; it polls (`poll=0.05`) until acquired or raises `TimeoutError`. Wraps each `run_*` LLM section.

## Progress
`jobs/progress.py` carries job steps to the browser over Redis pub/sub as Server-Sent Events. `publish(redis, job_id, event)` posts JSON to channel `job:<id>`; `sse_events(...)` first replays the persisted `job.log`, returns immediately if the job is already terminal, else subscribes and forwards live frames.

- `_TERMINAL = {"succeeded","failed","cancelled"}` ends the stream.
- Idle connections get a `: keep-alive` SSE comment after `idle_timeout` so proxies don't drop them.
- Tasks call it indirectly through `_safe_publish` (best-effort).

## Queue
`jobs/queue.py` enqueues jobs onto a process-global `arq` pool (`get_arq_pool`, a lazy singleton mirroring the engine/redis singletons). Thin `enqueue_*` helpers map to the worker functions: `enqueue_ingest`, `enqueue_lint`, `enqueue_fix`, `enqueue_format`, `enqueue_reindex`, `enqueue_gc_housekeeping`. Each stringifies UUID args and calls `pool.enqueue_job("<name>", …)`.

## Cancellation & reconcile
Cancellation is cooperative: the API marks a job cancel-requested, and tasks poll `jobs.is_cancel_requested(jid)` at each step (`on_step` / per-issue / `on_batch`), raising `IngestCancelled` / `MaintenanceCancelled` to roll `data_s` back and set status `cancelled`. Liveness is enforced by heartbeats plus `JobRepo.reconcile_stuck(older_than_seconds=120)`, run on worker startup to fail jobs whose heartbeat went stale (e.g. a crashed worker). See [[db#Repo pattern]].
