from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Job

_TERMINAL = ("succeeded", "failed", "cancelled")


class JobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, domain_id: uuid.UUID, kind: str) -> Job:
        job = Job(domain_id=domain_id, kind=kind, status="queued")
        self._s.add(job)
        await self._s.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> Job | None:
        job = await self._s.get(Job, job_id)
        if job is not None:
            await self._s.refresh(job)
        return job

    async def set_status(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        article_id: uuid.UUID | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if status == "running":
            values["started_at"] = func.now()
        if status in _TERMINAL:
            values["finished_at"] = func.now()
        if error is not None:
            values["error"] = error
        if article_id is not None:
            values["article_id"] = article_id
        await self._s.execute(update(Job).where(Job.id == job_id).values(**values))
        await self._s.flush()

    async def append_log(self, job_id: uuid.UUID, entry: dict[str, Any]) -> None:
        await self._s.execute(
            text("UPDATE jobs SET log = log || CAST(:e AS jsonb) WHERE id = :id"),
            {"e": json.dumps([entry]), "id": str(job_id)},
        )
        await self._s.flush()

    async def request_cancel(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(Job).where(Job.id == job_id).values(cancel_requested=True)
        )
        await self._s.flush()

    async def is_cancel_requested(self, job_id: uuid.UUID) -> bool:
        res = await self._s.execute(select(Job.cancel_requested).where(Job.id == job_id))
        return bool(res.scalar_one_or_none())

    async def heartbeat(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(Job).where(Job.id == job_id).values(heartbeat_at=func.now())
        )
        await self._s.flush()

    async def reconcile_stuck(self, *, older_than_seconds: int) -> int:
        res = await self._s.execute(
            text(
                "UPDATE jobs SET status='failed', error='reconciled: stale heartbeat', "
                "finished_at=now() "
                "WHERE status='running' AND "
                "(heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => :s)) "
                "RETURNING id"
            ),
            {"s": older_than_seconds},
        )
        n = len(res.all())
        await self._s.flush()
        return n
