from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphNode, GraphRepo
from paw.graph.subgraph import SubEdge
from paw.providers.config import GraphConfig
from paw.services.provider_settings import ProviderSettingsService


@dataclass(frozen=True)
class SubgraphPayload:
    root: uuid.UUID
    depth: int
    nodes: list[GraphNode]
    edges: list[SubEdge]


class GraphService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def config_for(self, domain_id: uuid.UUID) -> GraphConfig:
        cfg = await ProviderSettingsService(self._s).get_graph()
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        overrides = dom.config.get("graph") if isinstance(dom.config, dict) else None
        if isinstance(overrides, dict):
            return GraphConfig.model_validate({**cfg.model_dump(), **overrides})
        return cfg

    async def subgraph(
        self,
        *,
        domain_id: uuid.UUID,
        root: uuid.UUID,
        depth: int | None,
        types: list[str] | None,
    ) -> SubgraphPayload:
        cfg = await self.config_for(domain_id)
        art = await ArticleRepo(self._s).get(root)
        if art is None or art.domain_id != domain_id:
            raise ProblemError(status=404, title="Root article not found in domain")
        eff_depth = cfg.default_depth if depth is None else depth
        eff_depth = max(0, min(eff_depth, cfg.max_depth))
        allow = set(cfg.link_types)
        eff_types = list(allow) if types is None else [t for t in types if t in allow]
        nodes, edges = await GraphRepo(self._s).subgraph(
            domain_id=domain_id, root_article_id=root, depth=eff_depth, types=eff_types
        )
        return SubgraphPayload(root=root, depth=eff_depth, nodes=nodes, edges=edges)
