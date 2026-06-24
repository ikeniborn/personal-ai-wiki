import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Domain


class DomainRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, name: str, source_prefix: str, wiki_prefix: str) -> Domain:
        d = Domain(name=name, source_prefix=source_prefix, wiki_prefix=wiki_prefix)
        self._s.add(d)
        await self._s.flush()
        return d

    async def get(self, domain_id: uuid.UUID) -> Domain | None:
        return await self._s.get(Domain, domain_id)

    async def get_by_name(self, name: str) -> Domain | None:
        res = await self._s.execute(select(Domain).where(Domain.name == name))
        return res.scalar_one_or_none()

    async def list(self) -> list[Domain]:
        res = await self._s.execute(select(Domain).order_by(Domain.created_at))
        return list(res.scalars().all())
