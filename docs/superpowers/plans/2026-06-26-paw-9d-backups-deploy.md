---
title: "Phase 9d — Backups + deploy hardening"
phase: 9
state: draft
review:
  plan_hash: 5bb187aa647b12c3
  spec_hash: 25f8d2e8b94c05a4
  last_run: 2026-06-26
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings: []
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-9-ops-hardening-design.md
---

# Phase 9d — Backups + deploy hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed `paw` stack operable and recoverable — scheduled `pg_dump` backups with a retention policy and a tested restore procedure, conservative per-service resource guidance, an opt-in `backup` compose profile, `restart: unless-stopped` on all long-lived services, a worker healthcheck (Redis heartbeat key), the missing deploy env vars documented in `.env.example`, and a single prod/ops doc covering TLS/ACME, secrets, volumes, healthchecks, restart policies, and the backup/restore runbook.

**Architecture:** This sub-plan is **infra-only** — it touches no Python application code, no `config.py`, and no migrations. It adds two shell scripts under a new `deploy/backup/` directory (`backup.sh`, `restore.sh`), a `backup` sidecar service (compose `profiles: ["backup"]`) built on `postgres:16-alpine` (which ships `pg_dump`/`pg_restore` and a POSIX shell) that runs a `while true; backup.sh; sleep …; done` loop writing custom-format dumps to a new `backups` named volume and pruning old dumps by age. It edits `docker-compose.yml` to add `restart: unless-stopped` to `traefik`/`postgres`/`redis`/`api`/`worker` (NOT `init`/`backup`), a `worker` healthcheck that checks the `paw:worker:heartbeat` Redis key, and per-service `deploy.resources` guidance. It extends `.env.example` with the deploy/backup env vars and adds a new ops doc (`docs/wiki/ops.md`) cross-linked from `REDACTED`.

**Tech Stack:** Docker Compose · Traefik v3.2 · PostgreSQL 16 (`pgvector/pgvector:pg16` for data, `postgres:16-alpine` for the backup client) · Redis 7 · POSIX shell (`sh`). No Python changes.

## Global Constraints

- **Dependency management is `uv`** — never call `pip`/`pytest` directly; go through `uv run`. This sub-plan adds no Python deps, but the CI gate still runs over the repo.
- **CI gate (all three must still pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`. This sub-plan adds no `src/` code, so ruff/mypy must remain green unchanged; any new test must pass.
- **No application-code changes.** Do **not** edit `config.py`, `src/paw/**`, or `alembic/**`. Backups are infra; the backup knobs are env-only and read by the shell scripts/compose, never by the app.
- **Compose default `up` must be unaffected.** The `backup` service lives behind `profiles: ["backup"]`; `docker compose up` (no `--profile`) must not start it.
- **Atomicity of the backup:** `pg_dump --format=custom` of a single database is internally consistent (runs in one snapshot transaction); no app quiescing is required.
- **Docs are English; conversation is Russian.** After functional changes, update `docs/wiki/` via iwiki (final task).
- **Branch workflow:** all work on a `dev-*` branch off up-to-date `master`; merge via PR. Never commit to `master`.

## Coordination with the other Phase-9 sub-plans (READ THIS)

Phase 9 is split into **9a (observability)**, **9b (hardening + loaders)**, **9c (admin-ui + i18n)**, and **9d (this plan)**. The only shared file 9d touches that another sub-plan also edits is **`docker-compose.yml`**:

- **9a** adds an `observability` profile (Prometheus/Grafana/exporters) to `docker-compose.yml` and the `volumes:` block.
- **9d** (this plan) adds a `backup` profile + service, `restart:`/`deploy.resources`/`worker` healthcheck, and a `backups` volume.

These edits target **different services and different lines**, but they both append to the `services:` and `volumes:` maps, so a **textual merge conflict in `docker-compose.yml` is likely** if 9a and 9d land independently. Mitigation: whichever merges second rebases and re-applies its service/volume additions; the additions are append-only and independent (no overlapping keys). This plan's edit steps are written to be re-appliable in isolation. Call this out in the PR description.

`.env.example` is also touched by 9a (scrape vars) and 9b (SSRF/upload caps) — same append-only, low-conflict situation. 9d adds only deploy/backup vars (`POSTGRES_PASSWORD`, `ACME_EMAIL`, `PAW_HOST`, `BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS`).

## Reused building blocks / current state (verified — do not reimplement)

- **`docker-compose.yml`** (current: 93 lines). Services: `traefik` (v3.2, ports 80/443, ACME `le` resolver via tlschallenge, HTTP→HTTPS redirect, mounts docker socket ro + `letsencrypt` volume — **no healthcheck, no restart**), `postgres` (`pgvector/pgvector:pg16`, env `POSTGRES_USER=paw`/`POSTGRES_PASSWORD=${POSTGRES_PASSWORD:"REDACTED" volume `pgdata`, healthcheck `pg_isready -U paw`), `redis` (`redis:7-alpine`, appendonly, volume `redisdata`, healthcheck `redis-cli ping`), `init` (one-shot `alembic upgrade head`), `api` (uvicorn, traefik labels Host `${PAW_HOST:-localhost}`, `/health` healthcheck), `worker` (`arq paw.worker.WorkerSettings` — **no healthcheck**). Volumes: `pgdata`, `redisdata`, `letsencrypt`. **No `profiles:`, no `restart:`, no `deploy.resources` anywhere.**
- **Worker liveness marker:** `src/paw/worker.py::heartbeat` sets Redis key `paw:worker:heartbeat` = `"1"` with `ex=120` (120s TTL) on startup and on the `heartbeat` job. This is the key the new worker healthcheck must probe. The worker image has the `redis` Python package (it imports `arq`/`redis`) but has **no `redis-cli`** binary — the healthcheck must use Python, not `redis-cli`.
- **`Dockerfile`** (single-stage `python:3.12-slim`, installs `uv`, `uv pip install --system .`, EXPOSE 8000). The app image has **no `pg_dump` client** — that is why the backup sidecar uses `postgres:16-alpine` instead of the app image.
- **`.env.example`** (current: 7 lines) defines only `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, `FERNET_KEY`. **Missing** the compose-referenced `POSTGRES_PASSWORD`, `ACME_EMAIL`, `PAW_HOST` (all used with `${VAR:-default}` defaults today).
- **DB connection facts** (for the scripts): inside the compose network the database is reachable as host `postgres`, port `5432`, user `paw`, db `paw`, password from `POSTGRES_PASSWORD` (default `paw`). The app's `DATABASE_URL` uses the `postgresql+asyncpg://` driver, but `pg_dump`/`pg_restore` are libpq tools and take `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` env or flags — do not pass the SQLAlchemy URL to them.
- **Corpus tables** (for the restore roundtrip assertion; from `alembic/versions/0001_baseline.py` + conftest truncate list): `users, api_keys, app_settings, domains, blobs, sources, articles, chunks, links, ...`. The restore verification counts rows in a representative table (e.g. `domains` and `articles`) before backup and after restore-into-scratch and asserts equality.
- **`README.md`** is currently a one-line stub (`# personal-ai-wiki`) — the ops doc cross-link is a small, safe append.
- **iwiki doc style:** see `docs/wiki/architecture.md` — H1 title, short prose sections, `[[wikilink#anchor]]` cross-refs, fenced code/compose snippets. The new `docs/wiki/ops.md` follows that style.

## File Structure

**Create:**
- `deploy/backup/backup.sh` — `pg_dump --format=custom` → `/backups/paw-<UTC-timestamp>.dump`; prune dumps older than `${BACKUP_RETENTION_DAYS:-7}` days. POSIX `sh`, `set -eu`.
- `deploy/backup/restore.sh` — `pg_restore --clean --if-exists` a named dump into a target DB. POSIX `sh`, `set -eu`, refuses to run without an explicit dump path.
- `docs/wiki/ops.md` — prod/ops runbook: required env + secret generation, TLS/ACME, named volumes, healthcheck expectations, restart policies, resource guidance, how to run the `backup` profile, and the **restore procedure**.
- `tests/integration/test_backup_restore.py` — Docker-gated integration test asserting backup→restore reproduces the corpus (row counts). Skips cleanly when Docker is unavailable.

**Modify:**
- `docker-compose.yml` — add `restart: unless-stopped` to `traefik`/`postgres`/`redis`/`api`/`worker`; add `worker` healthcheck (heartbeat-key probe); add `deploy.resources` guidance per service; add the `backup` sidecar service under `profiles: ["backup"]`; add the `backups` named volume.
- `.env.example` — add `POSTGRES_PASSWORD`, `ACME_EMAIL`, `PAW_HOST`, `BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS` with commented guidance.
- `README.md` — add a short "Deployment / Ops" pointer to `docs/wiki/ops.md`.
- `docs/wiki/*` — refreshed via iwiki (final task).

---

### Task 1: Backup + restore shell scripts

**Files:**
- Create: `deploy/backup/backup.sh`
- Create: `deploy/backup/restore.sh`

**Interfaces:**
- `backup.sh` reads env: `PGHOST` (default `postgres`), `PGPORT` (default `5432`), `PGUSER` (default `paw`), `PGDATABASE` (default `paw`), `PGPASSWORD` (required by libpq, sourced from `POSTGRES_PASSWORD` in compose), `BACKUP_DIR` (default `/backups`), `BACKUP_RETENTION_DAYS` (default `7`). Writes `${BACKUP_DIR}/paw-$(date -u +%Y%m%dT%H%M%SZ).dump` via `pg_dump --format=custom`; then deletes `paw-*.dump` files older than retention. Prints the written path and prune summary; exits non-zero if `pg_dump` fails.
- `restore.sh <dump-path>` reads the same `PG*` env plus an optional `RESTORE_DB` (default = `PGDATABASE`). Runs `pg_restore --clean --if-exists --no-owner --no-privileges --dbname <RESTORE_DB> <dump-path>`. Refuses (exit 2) if `<dump-path>` is missing/unset. Prints what it restored.

- [ ] **Step 1: Create `deploy/backup/backup.sh`**

```sh
#!/bin/sh
# Scheduled logical backup of the paw Postgres database.
# Writes a custom-format dump and prunes dumps older than the retention window.
# Connection comes from libpq env (PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD).
set -eu

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-paw}"
PGDATABASE="${PGDATABASE:-paw}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
export PGHOST PGPORT PGUSER PGDATABASE

mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="${BACKUP_DIR}/paw-${ts}.dump"

echo "[backup] dumping ${PGDATABASE}@${PGHOST}:${PGPORT} -> ${out}"
pg_dump --format=custom --file="$out"
echo "[backup] wrote ${out} ($(wc -c < "$out") bytes)"

# Prune: delete custom-format dumps older than the retention window.
echo "[backup] pruning dumps older than ${BACKUP_RETENTION_DAYS} day(s) in ${BACKUP_DIR}"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'paw-*.dump' \
  -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete

echo "[backup] done"
```

- [ ] **Step 2: Create `deploy/backup/restore.sh`**

```sh
#!/bin/sh
# Restore a paw Postgres dump produced by backup.sh.
# Usage: restore.sh <dump-path>
# Restores into RESTORE_DB (default PGDATABASE). Uses pg_restore --clean --if-exists
# so it overwrites the existing schema/data in place. Connection from libpq env.
set -eu

dump="${1:-}"
if [ -z "$dump" ]; then
  echo "usage: restore.sh <dump-path>" >&2
  exit 2
fi
if [ ! -f "$dump" ]; then
  echo "[restore] dump not found: ${dump}" >&2
  exit 2
fi

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-paw}"
PGDATABASE="${PGDATABASE:-paw}"
RESTORE_DB="${RESTORE_DB:-$PGDATABASE}"
export PGHOST PGPORT PGUSER

echo "[restore] restoring ${dump} -> ${RESTORE_DB}@${PGHOST}:${PGPORT}"
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname "$RESTORE_DB" "$dump"
echo "[restore] done"
```

- [ ] **Step 3: Make the scripts executable**

```bash
chmod +x deploy/backup/backup.sh deploy/backup/restore.sh
```
Expected: both files are mode `0755` (`git add` records the executable bit). Verify with `ls -l deploy/backup`.

- [ ] **Step 4: Syntax-check both scripts (runs locally — no Docker needed)**

```bash
sh -n deploy/backup/backup.sh && sh -n deploy/backup/restore.sh && echo "syntax-ok"
```
Expected: prints `syntax-ok`, no parse errors. If `shellcheck` is installed, also run `shellcheck deploy/backup/*.sh` (advisory; a warning about `find -mtime` day-granularity is acceptable).

- [ ] **Step 5: Commit**

```bash
git add deploy/backup/backup.sh deploy/backup/restore.sh
git commit -m "feat(ops): add pg_dump backup and pg_restore scripts"
```

---

### Task 2: `backup` sidecar service + `backups` volume in compose

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- A new `backup` service, image `postgres:16-alpine` (ships `pg_dump`/`pg_restore`/`sh`/`find`/`date`), behind `profiles: ["backup"]` so default `up` skips it. It mounts `deploy/backup` read-only at `/scripts` and the `backups` named volume at `/backups`, sets the libpq env (incl. `PGPASSWORD`), and runs a loop: `backup.sh` then `sleep ${BACKUP_INTERVAL_SECONDS}`. `depends_on: postgres healthy`. **No `restart:` policy** — the loop is itself long-lived; if it crashes we want the failure visible, not silently respawned. A new `backups:` named volume is declared.

- [ ] **Step 1: Read the current compose file end-to-end**

```bash
cat docker-compose.yml
```
Confirm the service block boundaries and the `volumes:` map at the bottom (so the insertions below land in the right place). The `worker` service is the last service before `volumes:`.

- [ ] **Step 2: Append the `backup` service after the `worker` service block**

Insert, immediately before the top-level `volumes:` key (i.e. after the `worker` block, keeping 2-space service indentation):

```yaml
  # Opt-in scheduled backups. Enable with:  docker compose --profile backup up -d backup
  # Default `docker compose up` does NOT start this service.
  backup:
    image: postgres:16-alpine
    profiles: ["backup"]
    command:
      - "sh"
      - "-c"
      - "while true; do /scripts/backup.sh; sleep \"${BACKUP_INTERVAL_SECONDS:-86400}\"; done"
    environment:
      PGHOST: postgres
      PGPORT: "5432"
      PGUSER: paw
      PGDATABASE: paw
      PGPASSWORD: ${POSTGRES_PASSWORD:-paw}
      BACKUP_DIR: /backups
      BACKUP_RETENTION_DAYS: ${BACKUP_RETENTION_DAYS:-7}
    volumes:
      - "./deploy/backup:/scripts:ro"
      - "backups:/backups"
    depends_on:
      postgres: { condition: service_healthy }
```

- [ ] **Step 3: Add the `backups` named volume**

In the top-level `volumes:` map, add `backups:` alongside the existing entries:

```yaml
volumes:
  pgdata:
  redisdata:
  letsencrypt:
  backups:
```

- [ ] **Step 4: Validate compose config (runs locally if the `docker compose` CLI is present; config parse needs no daemon)**

```bash
docker compose config >/dev/null && echo "compose-config-ok"
docker compose config --profiles
```
Expected: first command prints `compose-config-ok` (YAML + interpolation valid); `--profiles` lists `backup` (and any profiles other sub-plans added). If the `docker compose` CLI is unavailable in this environment, defer to the Docker host (Task 6) and note it.

- [ ] **Step 5: Confirm default `up` excludes `backup` (config-level check, no daemon)**

```bash
docker compose config --services | sort
docker compose --profile backup config --services | sort
```
Expected: the first list (no profile) does **not** include `backup`; the second (with `--profile backup`) **does**. This proves the profile gate without starting anything.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(ops): add opt-in backup sidecar (profile) and backups volume"
```

---

### Task 3: Restart policies, worker healthcheck, resource guidance in compose

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- `restart: unless-stopped` added to `traefik`, `postgres`, `redis`, `api`, `worker` (the long-lived services). **Not** added to `init` (one-shot; must exit and stay exited) or `backup` (see Task 2 rationale).
- A `worker` healthcheck that exits 0 iff the Redis key `paw:worker:heartbeat` exists. The worker image has `redis` (Python) but no `redis-cli`, so the probe is a one-liner Python check using `REDIS_URL`.
- `REDACTEDsources` (limits + reservations, memory + cpus) added per service as **guidance** (conservative team-scale values). Documented caveat: `deploy.resources` is only enforced under Swarm; for plain `docker compose up` the doc notes the `mem_limit`/`cpus` equivalents.

- [ ] **Step 1: Add `restart: unless-stopped` to the five long-lived services**

For each of `traefik`, `postgres`, `redis`, `api`, `worker`, add a `restart: unless-stopped` line within the service block (e.g. as the first key after `image:`/`build:`). Do **not** touch `init` or `backup`. Example for `postgres`:

```yaml
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      ...
```

- [ ] **Step 2: Add the `worker` healthcheck**

Append a healthcheck to the `worker` service (it currently has none). The probe imports `redis` synchronously, builds the client from `REDIS_URL`, and checks the heartbeat key:

```yaml
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import os,sys,redis; r=REDACTEDom_url(os.environ['REDIS_URL']); sys.exit(0 if r.exists('paw:worker:heartbeat') else 1)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```
Note: `start_period: 30s` gives `on_startup` (which calls `heartbeat`) time to set the key on first boot; the key's TTL is 120s and the `heartbeat` job refreshes it, so a 30s interval keeps it green. `REDACTEDom_url` is the sync client (already importable in the worker image).

- [ ] **Step 3: Add `deploy.resources` guidance per service**

Add a `deploy.resources` block to each long-lived service with conservative team-scale values. Example values (the doc, not this file, is where operators tune them):

```yaml
  # api
    deploy:
      resources:
        limits:   { memory: 1g,   cpus: "1.0" }
        reservations: { memory: 256m, cpus: "0.25" }
  # worker
    deploy:
      resources:
        limits:   { memory: 2g,   cpus: "2.0" }
        reservations: { memory: 512m, cpus: "0.5" }
  # postgres
    deploy:
      resources:
        limits:   { memory: 2g,   cpus: "2.0" }
        reservations: { memory: 512m, cpus: "0.5" }
  # redis
    deploy:
      resources:
        limits:   { memory: 512m, cpus: "0.5" }
        reservations: { memory: 128m, cpus: "0.1" }
  # traefik
    deploy:
      resources:
        limits:   { memory: 256m, cpus: "0.5" }
        reservations: { memory: 64m,  cpus: "0.1" }
```
Insert each block into the matching service. The doc (Task 7 §7) explains the Swarm-only enforcement caveat and the plain-compose `mem_limit`/`cpus` equivalents.

- [ ] **Step 4: Validate compose config again (config parse; no daemon)**

```bash
docker compose config >/dev/null && echo "compose-config-ok"
```
Expected: `compose-config-ok`. `deploy.resources` and `healthcheck` keys are schema-valid in Compose v2. If the CLI is unavailable, defer validation to the Docker host (Task 6).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(ops): restart policies, worker heartbeat healthcheck, resource guidance"
```

---

### Task 4: Document deploy/backup env vars in `.env.example`

**Files:**
- Modify: `.env.example`

**Interfaces:**
- Add the five compose-referenced-but-undocumented vars with commented guidance: `POSTGRES_PASSWORD`, `ACME_EMAIL`, `PAW_HOST`, `BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS`. Keep the existing four lines untouched (append-only).

- [ ] **Step 1: Read the current file**

```bash
cat .env.example
```
Confirm it ends after `FERNET_KEY=...` (7 lines).

- [ ] **Step 2: Append the deploy/backup section**

Append to `.env.example`:

```bash

# --- Deploy (docker-compose) ---
# Postgres superuser password (compose default is "paw"; set a strong value in prod).
POSTGRES_PASSWORD=paw
# Email Let's Encrypt uses for ACME registration / expiry notices.
REDACTED
# Public hostname Traefik routes to the api (must resolve to this host for ACME to issue certs).
PAW_HOST=localhost

# --- Backups (compose `backup` profile) ---
# Days to keep dumps before pruning.
BACKUP_RETENTION_DAYS=7
# Seconds between scheduled dumps (86400 = daily).
BACKUP_INTERVAL_SECONDS=86400
```

- [ ] **Step 3: Sanity-check the file parses as env (runs locally)**

```bash
set -a; . ./.env.example; set +a; \
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD PAW_HOST=$PAW_HOST BACKUP_RETENTION_DAYS=$BACKUP_RETENTION_DAYS"
```
Expected: prints the three values, no shell parse errors (confirms no stray quoting/`$` issues).

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "docs(ops): document deploy + backup env vars in .env.example"
```

---

### Task 5: Backup→restore roundtrip integration test (Docker-gated)

**Files:**
- Create: `tests/integration/test_backup_restore.py`

**Interfaces:**
- A test that, against a real Postgres container, (1) seeds known rows, (2) runs `pg_dump --format=custom` to a temp file, (3) clears the data, (4) runs `pg_restore --clean --if-exists`, and (5) asserts the seeded rows are back (row counts + contents match). This is the executable proof of **acceptance criterion #4** ("a restore from a dump reproduces the corpus").
- **Docker dependency:** this is an `integration` test — it needs the Docker daemon (testcontainers Postgres) **and** the `pg_dump`/`pg_restore` client binaries on the test runner's `PATH`. The user cannot run Docker locally (see memory: "No Docker test verification") — so this test must **skip cleanly** when either Docker or the libpq client tools are unavailable, and the real assertion runs on a Docker host / CI. `psycopg2-binary` is already a dev dependency (used by conftest), so a sync psycopg2 connection is the simplest way to seed/inspect without async machinery.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_backup_restore.py`:

```python
"""Backup -> restore roundtrip (acceptance criterion #4).

Proves `pg_dump --format=custom` + `pg_restore --clean --if-exists` reproduces the
corpus. Needs the Docker daemon (testcontainers Postgres) AND the libpq client tools
(`pg_dump`/`pg_restore`) on PATH; skips cleanly when either is missing so the unit
layer / Docker-less environments stay green. The real run happens on a Docker host / CI.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("psycopg2")
import psycopg2  # noqa: E402

if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
    pytest.skip("pg_dump/pg_restore not on PATH", allow_module_level=True)

try:
    from testcontainers.postgres import PostgresContainer
except Exception:  # pragma: no cover - import guard
    pytest.skip("testcontainers not available", allow_module_level=True)


def _libpq_env(container: "PostgresContainer") -> dict[str, str]:
    return {
        "PGHOST": container.get_container_host_ip(),
        "PGPORT": str(container.get_exposed_port(5432)),
        "PGUSER": container.username,
        "PGPASSWORD": container.password,
        "PGDATABASE": container.dbname,
    }


def _dsn(container: "PostgresContainer") -> str:
    return (
        f"host={container.get_container_host_ip()} "
        f"port={container.get_exposed_port(5432)} "
        f"user={container.username} password="REDACTED" "
        f"dbname={container.dbname}"
    )


def test_backup_restore_roundtrip() -> None:
    try:
        ctx = PostgresContainer("pgvector/pgvector:pg16")
        ctx.start()
    except Exception as exc:  # Docker daemon not reachable
        pytest.skip(f"Docker unavailable: {exc}")

    try:
        env = _libpq_env(ctx)
        dsn = _dsn(ctx)

        # 1. Seed a known corpus.
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE corpus (id int primary key, body text)")
            cur.execute(
                "INSERT INTO corpus (id, body) VALUES (1,'alpha'),(2,'beta'),(3,'gamma')"
            )
            conn.commit()

        # 2. pg_dump --format=custom to a temp file.
        with tempfile.TemporaryDirectory() as tmp:
            dump = str(Path(tmp) / "paw.dump")
            subprocess.run(
                ["pg_dump", "--format=custom", "--file", dump], check=True, env=env
            )
            assert Path(dump).stat().st_size > 0

            # 3. Destroy the data.
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM corpus")
                conn.commit()
                cur.execute("SELECT count(*) FROM corpus")
                assert cur.fetchone()[0] == 0

            # 4. Restore from the dump.
            subprocess.run(
                ["pg_restore", "--clean", "--if-exists", "--no-owner",
                 "--no-privileges", "--dbname", ctx.dbname, dump],
                check=True, env=env,
            )

        # 5. Corpus is reproduced exactly.
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM corpus")
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT body FROM corpus ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == ["alpha", "beta", "gamma"]
    finally:
        ctx.stop()
```

- [ ] **Step 2: Run the test locally (expected to SKIP without Docker)**

```bash
uv run pytest tests/integration/test_backup_restore.py -q
```
Expected **locally (no Docker)**: `1 skipped` — the module-level / in-test guards skip on missing Docker or libpq tools, and the suite stays green. **On a Docker host / CI** (Docker daemon up + `postgresql-client` installed): `1 passed` — the roundtrip reproduces the 3 seeded rows. This is acceptance criterion #4.

- [ ] **Step 3: Lint the new test file**

```bash
uv run ruff check tests/integration/test_backup_restore.py
```
Expected: no errors. (`mypy src` does not type-check `tests/`, so no mypy step here.)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_backup_restore.py
git commit -m "test(ops): backup->restore roundtrip reproduces the corpus (Docker-gated)"
```

---

### Task 6: Manual / Docker-host verification of the live backup profile

**Files:** none (verification runbook; the same commands are captured in `docs/wiki/ops.md` in Task 7).

**Interfaces:** End-to-end proof on a machine with a running Docker daemon that the scheduled sidecar produces a dump and that a restore into a **scratch** database reproduces the corpus. The user cannot run Docker locally — these steps are marked **"run on a Docker host / CI"** and must be copy-pasteable.

- [ ] **Step 1 (Docker host): bring up the stack + backup profile**

```bash
cp .env.example .env   # then fill SESSION_SECRET, FERNET_KEY, POSTGRES_PASSWORD, PAW_HOST
docker compose up -d postgres redis init api worker
docker compose --profile backup up -d backup
```
Expected: all services start; `docker compose ps` shows `backup` running; `worker` becomes `healthy` within ~1 minute (heartbeat key set).

- [ ] **Step 2 (Docker host): force one immediate backup and confirm a dump exists**

```bash
docker compose exec backup /scripts/backup.sh
docker compose exec backup sh -c 'ls -l /backups'
```
Expected: `backup.sh` prints `[backup] wrote /backups/paw-<ts>.dump`; the `ls` lists at least one `paw-*.dump` file with non-zero size.

- [ ] **Step 3 (Docker host): restore into a scratch DB and compare row counts**

```bash
# Pick the latest dump.
DUMP=$(docker compose exec -T backup sh -c "ls -1t /backups/paw-*.dump | head -1" | tr -d '\r')

# Baseline counts in the live DB.
docker compose exec -T postgres psql -U paw -d paw -tAc \
  "select 'domains',count(*) from domains union all select 'articles',count(*) from articles"

# Create a scratch DB and restore the dump into it.
docker compose exec -T postgres psql -U paw -d paw -c "DROP DATABASE IF EXISTS paw_restore;"
docker compose exec -T postgres psql -U paw -d paw -c "CREATE DATABASE paw_restore;"
docker compose exec -T -e RESTORE_DB=paw_restore backup /scripts/restore.sh "$DUMP"

# Counts in the restored DB must match the baseline.
docker compose exec -T postgres psql -U paw -d paw_restore -tAc \
  "select 'domains',count(*) from domains union all select 'articles',count(*) from articles"
```
Expected: the `domains`/`articles` counts in `paw_restore` equal the live-DB baseline — the restore reproduces the corpus (acceptance criterion #4, live). Drop the scratch DB afterward.

- [ ] **Step 4 (Docker host): confirm retention pruning**

```bash
# Simulate an old dump and run a backup with a 7-day window.
docker compose exec backup sh -c 'touch -d "10 days ago" /backups/paw-old.dump'
docker compose exec -e BACKUP_RETENTION_DAYS=7 backup /scripts/backup.sh
docker compose exec backup sh -c 'ls /backups'
```
Expected: `paw-old.dump` (10 days old) is deleted by the prune (`-mtime +7`); the fresh `paw-<ts>.dump` remains.

- [ ] **Step 5: Record the outcome**

No commit. If run on CI/host, paste the observed counts into the PR description as evidence for acceptance criterion #4. If no Docker host is available at plan-execution time, mark this task's checkboxes with a note "deferred to Docker host" and rely on Task 5's gated test running in CI.

---

### Task 7: Prod/ops doc + README cross-link

**Files:**
- Create: `docs/wiki/ops.md`
- Modify: `README.md`

**Interfaces:** A single ops runbook covering the prod checklist (required env + secret generation, TLS/ACME, named volumes, healthcheck expectations, restart policies, resource guidance) and the backup/restore procedure (how to enable the profile, where dumps live, retention, and the exact restore steps). Cross-linked from `REDACTED`. Follows the `docs/wiki/architecture.md` style with `[[wikilink]]` cross-refs.

- [ ] **Step 1: Create `docs/wiki/ops.md`**

Write the doc with these sections (English):

1. **Overview** — one image/two processes recap; deploy is Docker Compose + Traefik. Cross-ref `[[architecture#Two processes, one image]]`.
2. **Required environment** — table of env vars: secrets (`SESSION_SECRET`, `FERNET_KEY` — with the generation one-liners from `.env.example`), DB (`DATABASE_URL`, `POSTGRES_PASSWORD`), `REDIS_URL`, deploy (`ACME_EMAIL`, `PAW_HOST`), backups (`BACKUP_RETENTION_DAYS`, `BACKUP_INTERVAL_SECONDS`). State which are required vs defaulted.
3. **TLS / ACME** — Traefik `le` resolver via tlschallenge; `PAW_HOST` must resolve publicly and ports 80/443 reachable for issuance; certs persist in the `letsencrypt` volume.
4. **Named volumes (back these up)** — `pgdata` (database), `redisdata` (queue/append-only), `letsencrypt` (certs), `backups` (dumps). Note the logical `pg_dump` backup covers `pgdata`'s *data*; `redisdata` and `letsencrypt` are regenerable.
5. **Healthchecks** — `postgres` (`pg_isready`), `redis` (`redis-cli ping`), `api` (`/health`), `worker` (Redis `paw:worker:heartbeat` key, 120s TTL, refreshed by the heartbeat job). Cross-ref `[[jobs]]`.
6. **Restart policies** — `unless-stopped` on `traefik`/`postgres`/`redis`/`api`/`worker`; `init` is one-shot (no restart); `backup` runs its own loop.
7. **Resource guidance** — the `deploy.resources` table with the caveat: enforced under Swarm; for plain `docker compose up` use `mem_limit`/`cpus` (give the equivalents). Values are conservative team-scale starting points — tune by load.
8. **Backups** — enable: `docker compose --profile backup up -d backup`; the sidecar runs `backup.sh` every `BACKUP_INTERVAL_SECONDS` writing `paw-<ts>.dump` (custom format) to the `backups` volume, pruning files older than `BACKUP_RETENTION_DAYS`. Off by default.
9. **Restore procedure** — the exact steps from Task 6 (restore into a scratch DB, compare counts, then swap or restore in place with `pg_restore --clean --if-exists`). Warn that in-place restore overwrites live data.
10. **Prod checklist** — a terse checkbox list: strong `POSTGRES_PASSWORD`/`SESSION_SECRET`/`FERNET_KEY`, public DNS for `PAW_HOST`, ports 80/443 open, `backup` profile enabled, backups tested-restored, healthchecks green, restart policies on.

- [ ] **Step 2: Cross-link from `REDACTED`**

Append to `README.md`:

```markdown

## Deployment / Ops

Production deployment, TLS/ACME, healthchecks, resource guidance, and the
**backup/restore** runbook live in [`docs/wiki/ops.md`](docs/wiki/ops.md).
Quick start: `cp .env.example .env`, fill the secrets, `docker compose up`;
enable scheduled backups with `docker compose --profile backup up -d backup`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/wiki/ops.md README.md
git commit -m "docs(ops): prod checklist + backup/restore runbook"
```

---

### Task 8: Docs refresh + full CI + PR

**Files:**
- Modify: `docs/wiki/*` (regenerated via iwiki)

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -q
```
Expected **locally (no Docker)**: green, with the new backup→restore test **skipped** (and the existing integration/api/e2e layers skipped/handled as usual without Docker). **On a Docker host / CI:** green with the roundtrip test passing.

- [ ] **Step 2: Run the complete CI gate**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
```
Expected: all three pass (mirrors `.github/workflows/ci.yml`). `mypy src` is unaffected — this sub-plan added no `src/` code.

- [ ] **Step 3: Regenerate the wiki pages for the changed areas**

```
iwiki:iwiki-ingest docs/wiki/ops.md
iwiki:iwiki-ingest docker-compose.yml
```
Then run `/iwiki-lint`.
Expected: `ops.md` is indexed and cross-links resolve (`[[architecture]]`, `[[jobs]]`); no broken `[[refs]]`, no orphan/stale pages. If `architecture.md`'s deploy section needs a one-line pointer to `[[ops]]`, add it (keep it minimal).

- [ ] **Step 4: Commit docs**

```bash
git add docs/wiki
git commit -m "docs(wiki): index ops runbook (backups + deploy hardening)"
```

- [ ] **Step 5: Open the PR**

Use **@skill:git-workflow** to push the `dev-*` branch and open a PR into `master` summarizing: opt-in `backup` profile (scheduled `pg_dump` + retention + tested restore), `restart: unless-stopped` on long-lived services, a `worker` heartbeat healthcheck, per-service resource guidance, and the documented deploy env vars + ops runbook. **Flag the likely `docker-compose.yml` / `.env.example` merge conflict with sub-plans 9a/9b** so reviewers expect a rebase. After the PR is created, remove the branch's worktree if one was used.

---

## Acceptance Criteria → Coverage Map

- **Spec criterion #4 — `pg_dump` backup runs on schedule; a restore from a dump reproduces the corpus.**
  - *Schedule:* Task 2 (the `backup` sidecar loops `backup.sh` every `BACKUP_INTERVAL_SECONDS`, gated by `profiles: ["backup"]`), verified live in Task 6 Step 2.
  - *Retention:* Task 1 (`backup.sh` prunes `-mtime +BACKUP_RETENTION_DAYS`), verified live in Task 6 Step 4.
  - *Restore reproduces the corpus:* Task 5 (automated, Docker-gated row-count roundtrip — the executable proof) + Task 6 Step 3 (live restore-into-scratch with matching `domains`/`articles` counts).
  - *Documented restore procedure:* Task 7 §9.
- **Spec "Resourcing/deploy" — per-service resource guidance, compose profiles, prod checklist (healthchecks, volumes, TLS/ACME).**
  - *Resource guidance:* Task 3 Step 3 (`deploy.resources`) + Task 7 §7 (Swarm caveat + `mem_limit`/`cpus` equivalents).
  - *Compose profiles:* Task 2 (`backup` profile, default `up` unaffected — proved in Task 2 Step 5).
  - *Restart policies:* Task 3 Step 1.
  - *Worker healthcheck:* Task 3 Step 2 (heartbeat-key probe).
  - *Missing env vars documented:* Task 4 (`POSTGRES_PASSWORD`, `ACME_EMAIL`, `PAW_HOST`, backup vars).
  - *Prod checklist doc (healthchecks/volumes/TLS/ACME/secrets/restart):* Task 7 §1–§10, cross-linked from README.

## Tests → Spec Map

- **Integration (testcontainers + libpq tools):** `tests/integration/test_backup_restore.py` — backup→restore roundtrip reproduces seeded rows. ✔ spec "Integration … backup→restore roundtrip". Docker-gated; skips without Docker/`pg_dump`.
- **Manual / E2E (Docker host):** Task 6 — live `backup` profile produces a dump, restore-into-scratch matches row counts, retention prunes old dumps. ✔ spec "E2E … backup/restore".
- **Config-level (no daemon):** Task 2 Steps 4–5 / Task 3 Step 4 — `docker compose config` validates the file and proves the `backup` profile is excluded from default `up`.
- **Pure-shell (local):** Task 1 Step 4 — `sh -n` syntax check (+ optional `shellcheck`).

## Risks / Notes

- **No app-code changes / CI unaffected:** this sub-plan adds no `src/` code, so `ruff`/`mypy` stay green by construction; the only new test is Docker-gated and skips cleanly, so the local `unit` run is unchanged.
- **Backup client image vs app image:** the app image (`python:3.12-slim`) has no `pg_dump`; the sidecar deliberately uses `postgres:16-alpine` (libpq tools + `sh`/`find`/`date`). Dump produced by a pg16 client against a pg16 server — version-matched, so no `pg_restore` version-skew warnings.
- **Worker healthcheck uses Python, not `redis-cli`:** the worker image has no `redis-cli`; the probe uses the already-present sync `redis` client (`REDACTEDom_url`). `start_period: 30s` covers first-boot before `on_startup` sets the heartbeat key; the 120s key TTL is refreshed by the `heartbeat` job, so a 30s interval stays green.
- **`deploy.resources` is Swarm-enforced only:** under plain `docker compose up` these blocks are advisory (validated but not enforced). The doc gives the `mem_limit`/`cpus` plain-compose equivalents so operators can enforce limits without Swarm. Values are conservative team-scale starting points, not tuned benchmarks.
- **In-place restore is destructive:** `restore.sh` uses `--clean --if-exists`, which drops then recreates objects in the target DB — safe into a scratch DB, overwriting into the live DB. Task 6 / the doc restore into a scratch DB first; the runbook warns before any in-place restore.
- **Merge conflict with 9a/9b:** `docker-compose.yml` and `.env.example` are appended to by multiple Phase-9 sub-plans. Additions are key-disjoint (different services/vars), so conflicts are textual, not semantic — resolve by re-appending. Flagged in the PR (Task 8 Step 5).
- **Redis durability is separate from backups:** `pg_dump` covers Postgres only. Redis (`redisdata`, appendonly) holds the job queue and is regenerable; `letsencrypt` certs re-issue on demand. The doc lists all four volumes but scopes the logical backup to Postgres.

---

## Self-Review (check-plan)

**Structure** — PASS. Frontmatter (title/phase/state/chain.spec) matches the Phase-8 plan shape. Eight tasks, each with Files, Interfaces, checkbox Steps carrying explicit commands + Expected outcomes, and per-task commits. Acceptance/Tests maps and Risks close the doc.

**Coverage** — PASS. Both 9d scope items are covered: (1) Backups — `backup.sh`/`restore.sh` (Task 1), sidecar + `backup` profile + `backups` volume (Task 2), retention (Task 1/Task 6), tested restore (Task 5 automated + Task 6 live), documented procedure (Task 7). (2) Resourcing/deploy — `deploy.resources` (Task 3), profiles usage (Task 2), `restart: unless-stopped` (Task 3), worker healthcheck (Task 3), `.env.example` vars (Task 4), prod-checklist doc (Task 7). Acceptance criterion #4 has a dedicated automated test (Task 5) and a live runbook (Task 6).

**Dependencies** — PASS. Task order is sound: scripts (1) before the sidecar that mounts them (2); compose service/restart/healthcheck edits (2,3) before live verification (6); env doc (4) feeds both the sidecar and the ops doc (7); tests (5) before full CI (8); docs/iwiki/PR last (7,8). No task depends on a later one. Cross-sub-plan compose/`.env` conflicts are called out, not silently assumed away.

**Verifiability** — PASS. Every step has a runnable command + expected result. Docker-dependent steps (Task 5 in-test, Task 6) are explicitly marked "run on a Docker host / CI" with copy-pasteable commands, honoring the no-local-Docker constraint; pure-shell (`sh -n`) and `docker compose config` checks run locally. Acceptance #4 is proven by matching row counts (Task 5) and live count comparison (Task 6).

**Consistency** — PASS. Identifiers are stable throughout: scripts `deploy/backup/{backup,restore}.sh`; service/profile `backup`; volume `backups`; env `POSTGRES_PASSWORD`/`ACME_EMAIL`/`PAW_HOST`/`BACKUP_RETENTION_DAYS`/`BACKUP_INTERVAL_SECONDS`; heartbeat key `paw:worker:heartbeat`; doc `docs/wiki/ops.md`. Connection facts (host `postgres`, user/db `paw`, libpq env vs SQLAlchemy URL) are consistent across scripts, compose, and tests. No `config.py`/`src` edits, matching the infra-only constraint.
