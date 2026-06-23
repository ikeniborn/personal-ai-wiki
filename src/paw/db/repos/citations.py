from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Citation, Source


@dataclass(frozen=True)
class CitationView:
    id: uuid.UUID
    quote: str | None
    locator: str | None
    source_id: uuid.UUID | None
    source_filename: str | None


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

    async def list_for_article(self, article_id: uuid.UUID) -> list[CitationView]:
        res = await self._s.execute(
            select(
                Citation.id,
                Citation.quote,
                Citation.locator,
                Citation.source_id,
                Source.filename,
            )
            .outerjoin(Source, Source.id == Citation.source_id)
            .where(Citation.article_id == article_id)
            .order_by(Citation.created_at)
        )
        return [
            CitationView(
                id=r[0], quote=r[1], locator=r[2], source_id=r[3], source_filename=r[4]
            )
            for r in res.all()
        ]
