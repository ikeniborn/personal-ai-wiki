from __future__ import annotations

import math
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Chunk, ChunkEntity


class ChunkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        article_id: uuid.UUID,
        domain_id: uuid.UUID,
        kind: str,
        ord: int,
        heading_path: str | None,
        text_body: str,
        embedding_version: int = 1,
    ) -> uuid.UUID:
        row = await self._s.execute(
            text(
                "INSERT INTO chunks "
                "(article_id, domain_id, kind, ord, heading_path, text, tsv, embedding_version) "
                "VALUES (:aid, :did, :kind, :ord, :hp, :txt, "
                "to_tsvector('english', :txt), :ev) RETURNING id"
            ),
            {
                "aid": str(article_id),
                "did": str(domain_id),
                "kind": kind,
                "ord": ord,
                "hp": heading_path,
                "txt": text_body,
                "ev": embedding_version,
            },
        )
        cid = row.scalar_one()
        await self._s.flush()
        return uuid.UUID(str(cid))

    async def set_embedding(
        self, *, chunk_id: uuid.UUID, vector: list[float], embedding_version: int = 1
    ) -> None:
        parts = []
        for x in vector:
            f = float(x)
            if not math.isfinite(f):
                raise ValueError(f"embedding contains non-finite value: {f!r}")
            parts.append(repr(f))
        literal = "[" + ",".join(parts) + "]"
        await self._s.execute(
            text(
                "UPDATE chunks SET embedding = CAST(:v AS vector), embedding_version = :ev "
                "WHERE id = :id"
            ),
            {"v": literal, "ev": embedding_version, "id": str(chunk_id)},
        )
        await self._s.flush()

    async def tag_entity(self, *, chunk_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        exists = await self._s.get(ChunkEntity, (chunk_id, entity_id))
        if exists is None:
            self._s.add(ChunkEntity(chunk_id=chunk_id, entity_id=entity_id))
            await self._s.flush()

    async def count_for_article(self, article_id: uuid.UUID) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(Chunk).where(Chunk.article_id == article_id)
        )
        return int(res.scalar_one())
