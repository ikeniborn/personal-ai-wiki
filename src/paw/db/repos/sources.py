import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Source


class SourceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        domain_id: uuid.UUID,
        storage_ref: str,
        filename: str | None,
        type: str,
        checksum: str,
    ) -> Source:
        s = Source(
            domain_id=domain_id,
            storage_ref=storage_ref,
            filename=filename,
            type=type,
            checksum=checksum,
        )
        self._s.add(s)
        await self._s.flush()
        return s

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Source]:
        res = await self._s.execute(
            select(Source).where(Source.domain_id == domain_id).order_by(Source.created_at)
        )
        return list(res.scalars().all())

    async def get(self, source_id: uuid.UUID) -> Source | None:
        return await self._s.get(Source, source_id)

    async def delete(self, source: Source) -> None:
        await self._s.delete(source)
        await self._s.flush()
