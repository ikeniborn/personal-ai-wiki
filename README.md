# personal-ai-wiki

## Deployment / Ops

Production deployment, TLS/ACME, healthchecks, resource guidance, and the
**backup/restore** runbook live in [`docs/wiki/ops.md`](docs/wiki/ops.md).
Quick start: `cp .env.example .env`, fill the secrets, `docker compose up`;
enable scheduled backups with `docker compose --profile backup up -d backup`.
