from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, Link
from paw.db.repos.entities import EntityRepo
from paw.graph.subgraph import SubEdge, build_subgraph


@dataclass(frozen=True)
class GraphNode:
    id: uuid.UUID
    slug: str
    title: str
    summary: str | None


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

    async def _domain_edges(self, domain_id: uuid.UUID) -> list[SubEdge]:
        res = await self._s.execute(
            select(Link.src_article_id, Link.dst_article_id, Link.type).where(
                Link.domain_id == domain_id
            )
        )
        return [SubEdge(src=r[0], dst=r[1], type=r[2]) for r in res.all()]

    async def _briefs(self, ids: set[uuid.UUID]) -> list[GraphNode]:
        if not ids:
            return []
        res = await self._s.execute(
            select(Article.id, Article.slug, Article.title, Article.summary)
            .where(Article.id.in_(ids))
            .order_by(Article.title)
        )
        return [GraphNode(id=r[0], slug=r[1], title=r[2], summary=r[3]) for r in res.all()]

    async def subgraph(
        self,
        *,
        domain_id: uuid.UUID,
        root_article_id: uuid.UUID,
        depth: int,
        types: list[str] | None,
    ) -> tuple[list[GraphNode], list[SubEdge]]:
        edges = await self._domain_edges(domain_id)
        sg = build_subgraph(
            edges, root_article_id, depth, set(types) if types is not None else None
        )
        nodes = await self._briefs(sg.node_ids)
        return nodes, sg.edges
