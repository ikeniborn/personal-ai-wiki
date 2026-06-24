from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.chunks import ChunkRepo
from paw.providers.base import EmbeddingProvider

OnBatch = Callable[[int, int], Awaitable[None]]


def plan_batches(total: int, batch_size: int) -> list[int]:
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size!r}")
    if total <= 0:
        return []
    full, remainder = divmod(total, batch_size)
    sizes = [batch_size] * full
    if remainder:
        sizes.append(remainder)
    return sizes


async def reindex_domain_chunks(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    target_version: int,
    embedder: EmbeddingProvider,
    batch_size: int,
    on_batch: OnBatch | None = None,
) -> int:
    repo = ChunkRepo(session)
    total = await repo.count_stale(domain_id=domain_id, target_version=target_version)
    done = 0
    for _ in plan_batches(total, batch_size):
        batch = await repo.fetch_stale_batch(
            domain_id=domain_id, target_version=target_version, limit=batch_size
        )
        if not batch:
            break
        vectors = await embedder.embed([txt for _, txt in batch])
        for (cid, _txt), vec in zip(batch, vectors, strict=True):
            await repo.set_embedding(
                chunk_id=cid, vector=vec, embedding_version=target_version
            )
        done += len(batch)
        if on_batch is not None:
            await on_batch(done, total)
    return done
