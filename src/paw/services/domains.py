import re

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Domain
from paw.db.repos.domains import DomainRepo


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "domain"


class DomainService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = DomainRepo(session)

    async def create(self, name: str) -> Domain:
        slug = _slugify(name)
        d = await self._repo.create(
            name=name, source_prefix=f"src/{slug}", wiki_prefix=f"wiki/{slug}"
        )
        await self._s.commit()  # domain row is its own commit boundary
        # Graph bootstrap is DDL-like: run it in a *separate* commit, only when AGE is on.
        from paw.providers.config import GraphConfig
        from paw.services.provider_settings import ProviderSettingsService

        gcfg: GraphConfig = await ProviderSettingsService(self._s).get_graph()
        if gcfg.engine == "age":
            from paw.graph.age.schema import ensure_graph

            await ensure_graph(self._s, d.id)
            await self._s.commit()
        return d

    async def list(self) -> list[Domain]:
        return await self._repo.list()
