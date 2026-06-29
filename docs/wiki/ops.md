# Ops

## Overview

`paw` ships as one Docker image running two processes (`api` + `worker`) — see
[[architecture#Two processes, one image]]. Production deployment uses Docker Compose with
Traefik as the TLS-terminating reverse proxy. `docker compose up` starts all core services;
opt-in profiles (`backup`, `observability`) are off by default.

## Required environment

Copy `.env.example` to `.env` and fill the values below before running.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SESSION_SECRET` | **yes** | — | 32+ byte random string. Generate: `openssl rand -hex 32` |
| `FERNET_KEY` | **yes** | — | 44-char Fernet key. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL` | **yes** | — | `postgresql+asyncpg://paw:<PGPASSWORD>@postgres:5432/paw` |
| `REDIS_URL` | **yes** | — | `redis://redis:6379/0` |
| `POSTGRES_PASSWORD` | no | `paw` | Postgres superuser password — set a strong value in prod |
| `ACME_EMAIL` | no | `admin@example.com` | Email for Let's Encrypt registration / expiry notices |
| `PAW_HOST` | no | `localhost` | Public hostname Traefik routes to the api |
| `BACKUP_RETENTION_DAYS` | no | `7` | Days to keep dumps before pruning |
| `BACKUP_INTERVAL_SECONDS` | no | `86400` | Seconds between scheduled dumps (86400 = daily) |

`SESSION_SECRET`, `FERNET_KEY`, `DATABASE_URL`, and `REDIS_URL` have no defaults and will
fail validation on startup if absent. All others have usable defaults for local dev.

## TLS / ACME

Traefik uses the `le` certificate resolver with TLS challenge (`tlschallenge=true`). On first
request, Traefik contacts Let's Encrypt and provisions a certificate for `PAW_HOST`. Two
conditions must be met:

- `PAW_HOST` must resolve publicly to the host IP.
- Ports 80 and 443 must be reachable from the internet (Let's Encrypt performs an inbound TLS
  handshake on port 443).

Certificates are stored in the `letsencrypt` named volume at `/letsencrypt/acme.json` and
survive container restarts. For local dev (`PAW_HOST=localhost`) ACME issuance will fail; use
a self-signed cert or skip TLS.

## Named volumes (back these up)

| Volume | Contains | Regenerable? |
|---|---|---|
| `pgdata` | All Postgres data (articles, sources, users, jobs) | No — primary store |
| `redisdata` | Redis AOF log (job queue, sessions, pub/sub state) | Mostly — queue drains on restart; sessions invalidated |
| `letsencrypt` | ACME certs and account key | Yes — Traefik re-issues on restart (rate-limited) |
| `backups` | Logical `pg_dump` archives written by the backup sidecar | Yes — derived from `pgdata` |

The `backup` sidecar covers `pgdata` data via logical dump. `redisdata` and `letsencrypt` are
regenerable; back up `pgdata` (and optionally `letsencrypt` to avoid rate-limit delays).

## Healthchecks

Each long-lived service declares a healthcheck that Compose uses for `depends_on` ordering
and for `docker ps` / `docker inspect` status reporting.

| Service | Check | Interval / retries |
|---|---|---|
| `postgres` | `pg_isready -U paw` | 5s / 10 |
| `redis` | `redis-cli ping` | 5s / 10 |
| `api` | HTTP GET `http://localhost:8000/health` → 200 | 10s / 5 |
| `worker` | `redis.exists('paw:worker:heartbeat')` (key TTL 120s) | interval: 30s / retries: 3 / start_period: 30s |

The `worker` healthcheck passes only while the `heartbeat` job is writing the
`paw:worker:heartbeat` key to Redis every cycle (`ex=120`). If the worker stops, the key
expires within 120 seconds and the check fails. See [[jobs]] for the heartbeat job details.

## Restart policies

| Service | Policy | Reason |
|---|---|---|
| `traefik` | `unless-stopped` | Persistent router |
| `postgres` | `unless-stopped` | Persistent database |
| `redis` | `unless-stopped` | Persistent queue |
| `api` | `unless-stopped` | Persistent HTTP server |
| `worker` | `unless-stopped` | Persistent job consumer |
| `init` | *(none)* | One-shot migration; exits 0 on success |
| `backup` | *(none)* | Runs its own `while true; do … sleep; done` loop — no restart so a crash stays visible in `docker ps` |

## Resource guidance

`deploy.resources` values from `docker-compose.yml` (limits / reservations):

| Service | Memory limit | CPU limit | Memory reservation | CPU reservation |
|---|---|---|---|---|
| `traefik` | 256m | 0.5 | 64m | 0.1 |
| `postgres` | 2g | 2.0 | 512m | 0.5 |
| `redis` | 512m | 0.5 | 128m | 0.1 |
| `api` | 1g | 1.0 | 256m | 0.25 |
| `worker` | 2g | 2.0 | 512m | 0.5 |

**Important:** `deploy.resources` is enforced only under Docker Swarm (`docker stack deploy`).
For plain `docker compose up`, use `mem_limit` and `cpus` at the service level instead:

```bash
# Equivalent plain-compose overrides (add to the service block):
# mem_limit: 2g
# cpus: "2.0"
```

These are conservative team-scale starting points. Monitor actual usage and tune upward for
large corpora or heavy LLM ingest load.

## Backups

The `backup` service is off by default. Enable it with:

```bash
docker compose --profile backup up -d backup
```

Once running, the sidecar executes `deploy/backup/backup.sh` every `BACKUP_INTERVAL_SECONDS`
seconds (default 86400 = daily). Each run:

1. Calls `pg_dump --format=custom` against the `postgres` service.
2. Writes `paw-<timestamp>.dump` (e.g. `paw-20260629T120000Z.dump`) to the `backups` volume
   at `/backups/`.
3. Prunes dumps older than `BACKUP_RETENTION_DAYS` days (default 7) using `find -mtime`.
   Note: `find -mtime +N` matches files whose age is **strictly greater than** N×24h, so a
   dump is pruned only once it is older than roughly `N+1` days — retention errs toward
   keeping one extra day, never fewer.

The sidecar depends on `postgres: service_healthy`, so it waits for Postgres to be ready
before each dump cycle.

To list existing dumps:

```bash
docker compose run --rm backup ls /backups
```

## Restore procedure

Dumps are custom-format (`pg_restore` required — `psql` won't work).

**Safe path — restore into a scratch database first.** Recommended: this verifies a dump
without touching live data.

```bash
docker compose exec postgres createdb -U paw paw_restore
docker compose run --rm \
  -e RESTORE_DB=paw_restore \
  backup /scripts/restore.sh /backups/paw-<timestamp>.dump
docker compose exec postgres psql -U paw paw_restore \
  -c "SELECT COUNT(*) FROM domains;"
docker compose exec postgres psql -U paw paw \
  -c "SELECT COUNT(*) FROM domains;"
```

Compare row counts between `paw_restore` and `paw`. If they match, the dump is good. Drop
the scratch database when done:

```bash
docker compose exec postgres dropdb -U paw paw_restore
```

**In-place restore — overwrites live data; use with caution.**
**Warning: this OVERWRITES all data in the live `paw` database. Stop the api and worker
first to avoid write conflicts.**

```bash
docker compose stop api worker
docker compose run --rm backup /scripts/restore.sh /backups/paw-<timestamp>.dump
docker compose start api worker
```

`restore.sh` runs `pg_restore --clean --if-exists --no-owner --no-privileges`, which drops
and recreates every object in the target database before loading the dump.

## Prod checklist

Before going live, verify each item:

- [ ] `POSTGRES_PASSWORD` is a strong random value (not `paw`)
- [ ] `SESSION_SECRET` is 32+ bytes of randomness (not the placeholder)
- [ ] `FERNET_KEY` is a valid Fernet key generated by the one-liner above
- [ ] `PAW_HOST` is set to the public DNS name that resolves to this host
- [ ] Ports 80 and 443 are reachable from the internet (for ACME issuance)
- [ ] `backup` profile is enabled and running (`docker compose --profile backup up -d backup`)
- [ ] At least one backup has been taken and successfully restored into a scratch database
- [ ] All five core services show `healthy` in `docker compose ps`
- [ ] `restart: unless-stopped` is active on `traefik`, `postgres`, `redis`, `api`, `worker`
