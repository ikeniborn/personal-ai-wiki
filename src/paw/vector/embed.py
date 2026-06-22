from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.chunks import ChunkRepo
from paw.ingest.chunking import ChunkSpec
from paw.providers.base import EmbeddingProvider


async def embed_and_write(
    session: AsyncSession,
    *,
    article_id: uuid.UUID,
    domain_id: uuid.UUID,
    specs: list[ChunkSpec],
    embedder: EmbeddingProvider,
    embedding_version: int = 1,
) -> list[uuid.UUID]:
    repo = ChunkRepo(session)
    vectors = await embedder.embed([s.text for s in specs]) if specs else []
    ids: list[uuid.UUID] = []
    for spec, vec in zip(specs, vectors, strict=True):
        cid = await repo.create(
            article_id=article_id,
            domain_id=domain_id,
            kind=spec.kind,
            ord=spec.ord,
            heading_path=spec.heading_path,
            text_body=spec.text,
            embedding_version=embedding_version,
        )
        await repo.set_embedding(chunk_id=cid, vector=vec, embedding_version=embedding_version)
        ids.append(cid)
    return ids
