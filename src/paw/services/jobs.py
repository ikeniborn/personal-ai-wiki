from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.audit import actions
from paw.audit.log import record
from paw.config import get_settings
from paw.db.models import Job
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.init import build_structure_plan
from paw.jobs.queue import enqueue_ingest
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = JobRepo(session)

    async def start_ingest(
        self,
        *,
        domain_id: uuid.UUID,
        source_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> Job:
        job = await self._repo.create(domain_id=domain_id, kind="ingest")
        await record(
            self._s,
            user_id=actor_id,
            action=actions.INGEST_START,
            target_type="source",
            target_id=source_id,
        )
        await self._s.commit()
        await enqueue_ingest(None, job_id=job.id, domain_id=domain_id, source_id=source_id)
        return job

    async def init_domain(
        self, *, domain_id: uuid.UUID, brief: str, actor_id: uuid.UUID | None = None
    ) -> list[tuple[str, uuid.UUID]]:
        from paw.providers.factory import build_chat_provider
        from paw.services.provider_settings import ProviderSettingsService

        box = SecretBox(get_settings().fernet_key)
        psvc = ProviderSettingsService(self._s, box=box)
        pc = await psvc.get_provider()
        wiki = await psvc.get_wiki() if pc else WikiConfig()
        if pc is None:
            raise ProblemError(
                status=422,
                title="Provider not configured",
                detail="Configure an LLM provider before initialising a domain.",
            )
        from paw.db.repos.domains import DomainRepo

        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        chat = build_chat_provider(pc, box)
        topics = await build_structure_plan(domain_name=dom.name, brief=brief, chat=chat, cfg=wiki)
        out: list[tuple[str, uuid.UUID]] = []
        for topic in topics:
            job = await self._repo.create(domain_id=domain_id, kind="ingest")
            await record(
                self._s,
                user_id=actor_id,
                action=actions.INGEST_START,
                target_type="topic",
                meta={"topic": topic},
            )
            await self._s.commit()
            await enqueue_ingest(None, job_id=job.id, domain_id=domain_id, topic=topic)
            out.append((topic, job.id))
        return out

    async def cancel(self, job_id: uuid.UUID) -> None:
        job = await self._repo.get(job_id)
        if job is None:
            raise ProblemError(status=404, title="Job not found")
        await self._repo.request_cancel(job_id)
        await self._s.commit()
