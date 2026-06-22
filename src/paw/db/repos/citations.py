from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Citation


class CitationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        article_id: uuid.UUID,
        source_id: uuid.UUID | None,
        quote: str | None,
        locator: str | None,
    ) -> Citation:
        c = Citation(article_id=article_id, source_id=source_id, quote=quote, locator=locator)
        self._s.add(c)
        await self._s.flush()
        return c
