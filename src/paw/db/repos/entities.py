from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import ArticleEntity, Entity


class EntityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, *, domain_id: uuid.UUID, name: str, kind: str | None = None
    ) -> Entity:
        res = await self._s.execute(
            select(Entity).where(Entity.domain_id == domain_id, Entity.name == name)
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            return existing
        e = Entity(domain_id=domain_id, name=name, kind=kind)
        self._s.add(e)
        await self._s.flush()
        return e

    async def tag_article(self, *, article_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        exists = await self._s.get(ArticleEntity, (article_id, entity_id))
        if exists is None:
            self._s.add(ArticleEntity(article_id=article_id, entity_id=entity_id))
            await self._s.flush()

    async def shared_with(
        self, *, domain_id: uuid.UUID, article_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, int]]:
        mine = select(ArticleEntity.entity_id).where(ArticleEntity.article_id == article_id)
        stmt = (
            select(ArticleEntity.article_id, func.count().label("shared"))
            .join(Entity, Entity.id == ArticleEntity.entity_id)
            .where(
                Entity.domain_id == domain_id,
                ArticleEntity.entity_id.in_(mine),
                ArticleEntity.article_id != article_id,
            )
            .group_by(ArticleEntity.article_id)
            .order_by(func.count().desc())
        )
        res = await self._s.execute(stmt)
        return [(row[0], int(row[1])) for row in res.all()]
