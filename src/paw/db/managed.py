from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_HNSW_INDEX = "ix_chunks_embedding_hnsw"
_QC_HNSW_INDEX = "ix_query_cache_embedding_hnsw"


async def ensure_embedding_column(session: AsyncSession, dim: int) -> None:
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
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


async def rebuild_embedding_column(session: AsyncSession, dim: int) -> None:
    """Change the chunks.embedding vector dimension: drop the HNSW index and
    column, re-add at the new dim, recreate the index. DESTRUCTIVE — existing
    embeddings are dropped and chunks must be re-embedded (this is the
    ALTER + HNSW rebuild + reindex the settings UI warns about)."""
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    await session.execute(text(f"DROP INDEX IF EXISTS {_HNSW_INDEX}"))
    await session.execute(text("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding"))
    await session.execute(text(f"ALTER TABLE chunks ADD COLUMN embedding vector({dim})"))
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


async def ensure_query_cache_embedding_column(session: AsyncSession, dim: int) -> None:
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    await session.execute(
        text(f"ALTER TABLE query_cache ADD COLUMN IF NOT EXISTS query_embedding vector({dim})")
    )
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def rebuild_query_cache_embedding_column(session: AsyncSession, dim: int) -> None:
    """Change query_cache.query_embedding dim. DESTRUCTIVE: truncates the cache
    (a dim change invalidates every stored answer embedding — they must be
    recomputed on the next miss). Mirrors rebuild_embedding_column for chunks."""
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"embedding dim must be a positive int, got {dim!r}")
    await session.execute(text("TRUNCATE query_cache CASCADE"))
    await session.execute(text(f"DROP INDEX IF EXISTS {_QC_HNSW_INDEX}"))
    await session.execute(text("ALTER TABLE query_cache DROP COLUMN IF EXISTS query_embedding"))
    await session.execute(text(f"ALTER TABLE query_cache ADD COLUMN query_embedding vector({dim})"))
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_QC_HNSW_INDEX} "
            "ON query_cache USING hnsw (query_embedding vector_cosine_ops)"
        )
    )
    await session.flush()


async def query_cache_embedding_dim(session: AsyncSession) -> int | None:
    row = await session.execute(
        text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = 'query_cache' AND a.attname = 'query_embedding' "
            "AND NOT a.attisdropped"
        )
    )
    val = row.scalar_one_or_none()
    return int(val) if val is not None and val > 0 else None
