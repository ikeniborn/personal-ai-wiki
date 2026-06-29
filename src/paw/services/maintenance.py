from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import Job
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.jobs.queue import (
    enqueue_fix,
    enqueue_format,
    enqueue_graph_rebuild,
    enqueue_lint,
    enqueue_reindex,
)
from paw.providers.config import MaintenanceConfig
from paw.services.provider_settings import ProviderSettingsService


class MaintenanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = JobRepo(session)

    async def _resolved_config(self, domain_id: uuid.UUID) -> MaintenanceConfig:
        cfg = await ProviderSettingsService(self._s).get_maintenance()
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        overrides = dom.config.get("maintenance") if isinstance(dom.config, dict) else None
        if isinstance(overrides, dict):
            return MaintenanceConfig.model_validate({**cfg.model_dump(), **overrides})
        return cfg

    async def _require_enabled(self, domain_id: uuid.UUID, op: str) -> None:
        cfg = await self._resolved_config(domain_id)
        if op not in cfg.enabled_ops:
            raise ProblemError(
                status=422,
                title="Operation disabled",
                detail=f"{op} is not enabled for this domain",
            )

    async def start_lint(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "lint")
        job = await self._repo.create(domain_id=domain_id, kind="lint")
        await self._s.commit()
        await enqueue_lint(None, job_id=job.id, domain_id=domain_id)
        return job

    async def start_fix(self, *, domain_id: uuid.UUID, issue_ids: list[str]) -> Job:
        await self._require_enabled(domain_id, "fix")
        job = await self._repo.create(domain_id=domain_id, kind="fix")
        await self._s.commit()
        await enqueue_fix(None, job_id=job.id, domain_id=domain_id, issue_ids=issue_ids)
        return job

    async def start_format(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "format")
        job = await self._repo.create(domain_id=domain_id, kind="format")
        await self._s.commit()
        await enqueue_format(None, job_id=job.id, domain_id=domain_id)
        return job

    async def start_reindex(self, *, domain_id: uuid.UUID) -> Job:
        await self._require_enabled(domain_id, "reindex")
        job = await self._repo.create(domain_id=domain_id, kind="reindex")
        await self._s.commit()
        await enqueue_reindex(None, job_id=job.id, domain_id=domain_id)
        return job

    async def start_graph_rebuild(self, *, domain_id: uuid.UUID) -> Job:
        job = await self._repo.create(domain_id=domain_id, kind="graph_rebuild")
        await self._s.commit()
        await enqueue_graph_rebuild(None, job_id=job.id, domain_id=domain_id)
        return job
