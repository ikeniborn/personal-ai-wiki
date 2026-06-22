from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Link
from paw.db.repos.entities import EntityRepo


class GraphRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._entities = EntityRepo(session)

    async def link(
        self,
        *,
        domain_id: uuid.UUID,
        src_article_id: uuid.UUID,
        dst_article_id: uuid.UUID,
        type: str,
    ) -> bool:
        if src_article_id == dst_article_id:
            raise ValueError("cannot link an article to itself")
        res = await self._s.execute(
            select(Link.id).where(
                Link.src_article_id == src_article_id,
                Link.dst_article_id == dst_article_id,
                Link.type == type,
            )
        )
        if res.scalar_one_or_none() is not None:
            return False
        self._s.add(
            Link(
                domain_id=domain_id,
                src_article_id=src_article_id,
                dst_article_id=dst_article_id,
                type=type,
            )
        )
        await self._s.flush()
        return True

    async def cooccurrence_targets(
        self, *, domain_id: uuid.UUID, article_id: uuid.UUID, threshold: int
    ) -> list[uuid.UUID]:
        shared = await self._entities.shared_with(domain_id=domain_id, article_id=article_id)
        return [aid for aid, count in shared if count >= threshold]
