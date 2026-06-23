from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Chunk, ChunkEntity


@dataclass(frozen=True)
class PassageRow:
    chunk_id: uuid.UUID
    article_id: uuid.UUID
    heading_path: str | None
    text: str
    slug: str
    title: str


@dataclass(frozen=True)
class SummaryRow:
    article_id: uuid.UUID
    text: str
    slug: str
    title: str


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

    async def fetch_passages(self, chunk_ids: list[uuid.UUID]) -> list[PassageRow]:
        if not chunk_ids:
            return []
        res = await self._s.execute(
            text(
                "SELECT c.id, c.article_id, c.heading_path, c.text, a.slug, a.title "
                "FROM chunks c JOIN articles a ON a.id = c.article_id "
                "WHERE c.id = ANY(:ids)"
            ),
            {"ids": [str(c) for c in chunk_ids]},
        )
        by_id = {
            uuid.UUID(str(r[0])): PassageRow(
                chunk_id=uuid.UUID(str(r[0])),
                article_id=uuid.UUID(str(r[1])),
                heading_path=r[2],
                text=r[3],
                slug=r[4],
                title=r[5],
            )
            for r in res.all()
        }
        # preserve caller's (fused-score) order
        return [by_id[c] for c in chunk_ids if c in by_id]

    async def fetch_summaries(self, article_ids: list[uuid.UUID]) -> list[SummaryRow]:
        if not article_ids:
            return []
        res = await self._s.execute(
            text(
                "SELECT c.article_id, c.text, a.slug, a.title "
                "FROM chunks c JOIN articles a ON a.id = c.article_id "
                "WHERE c.article_id = ANY(:aids) AND c.kind = 'summary'"
            ),
            {"aids": [str(a) for a in article_ids]},
        )
        return [
            SummaryRow(
                article_id=uuid.UUID(str(r[0])), text=r[1], slug=r[2], title=r[3]
            )
            for r in res.all()
        ]

    async def tagged_with(
        self, *, chunk_ids: list[uuid.UUID], entity_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        if not chunk_ids or not entity_ids:
            return set()
        res = await self._s.execute(
            text(
                "SELECT DISTINCT chunk_id FROM chunk_entities "
                "WHERE chunk_id = ANY(:cids) AND entity_id = ANY(:eids)"
            ),
            {"cids": [str(c) for c in chunk_ids], "eids": [str(e) for e in entity_ids]},
        )
        return {uuid.UUID(str(r[0])) for r in res.all()}

    async def count_for_article(self, article_id: uuid.UUID) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(Chunk).where(Chunk.article_id == article_id)
        )
        return int(res.scalar_one())
