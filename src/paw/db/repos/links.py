from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, Link


@dataclass(frozen=True)
class LinkedArticle:
    link_type: str
    article_id: uuid.UUID
    slug: str
    title: str


class LinkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def backlinks(self, article_id: uuid.UUID) -> list[LinkedArticle]:
        res = await self._s.execute(
            select(Link.type, Article.id, Article.slug, Article.title)
            .join(Article, Article.id == Link.src_article_id)
            .where(Link.dst_article_id == article_id)
            .order_by(Link.type, Article.title)
        )
        return [
            LinkedArticle(link_type=r[0], article_id=r[1], slug=r[2], title=r[3])
            for r in res.all()
        ]

    async def outgoing(self, article_id: uuid.UUID) -> list[LinkedArticle]:
        res = await self._s.execute(
            select(Link.type, Article.id, Article.slug, Article.title)
            .join(Article, Article.id == Link.dst_article_id)
            .where(Link.src_article_id == article_id)
            .order_by(Link.type, Article.title)
        )
        return [
            LinkedArticle(link_type=r[0], article_id=r[1], slug=r[2], title=r[3])
            for r in res.all()
        ]

    async def parent_child_raw(
        self, domain_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id, Link.type).where(
                Link.domain_id == domain_id, Link.type.in_(("parent", "child"))
            )
        )
        return [(r[0], r[1], r[2]) for r in res.all()]

    async def domain_link_pairs(
        self, domain_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id).where(Link.domain_id == domain_id)
        )
        return [(r[0], r[1]) for r in res.all()]
