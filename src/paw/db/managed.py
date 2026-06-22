from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_HNSW_INDEX = "ix_chunks_embedding_hnsw"


async def ensure_embedding_column(session: AsyncSession, dim: int) -> None:
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    # dim is validated above; safe to interpolate (DDL type modifiers cannot bind).
    await session.execute(
        text(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector({dim})")
    )
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_HNSW_INDEX} "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def embedding_dim(session: AsyncSession) -> int | None:
    row = await session.execute(
        text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = 'chunks' AND a.attname = 'embedding' AND NOT a.attisdropped"
        )
    )
    val = row.scalar_one_or_none()
    # pgvector stores the dimension directly in atttypmod (no -4 VARLENA offset).
    return int(val) if val is not None and val > 0 else None
