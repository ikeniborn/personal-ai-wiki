from __future__ import annotations

import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

# Outgoing-only, cycle-safe (CYCLE ... SET, PG14+), depth-bounded BFS over links.
_BFS = text(
    "WITH RECURSIVE bfs(article_id, depth) AS ("
    "  SELECT unnest(:seed), 0 "
    "  UNION "
    "  SELECT l.dst_article_id, b.depth + 1 "
    "    FROM bfs b JOIN links l ON l.src_article_id = b.article_id "
    "   WHERE b.depth < :max_depth"
    ") CYCLE article_id SET cyc USING path "
    "SELECT DISTINCT article_id FROM bfs"
).bindparams(bindparam("seed", type_=ARRAY(PGUUID(as_uuid=True))))


async def bfs_expand(
    session: AsyncSession, *, seed_article_ids: list[uuid.UUID], max_depth: int
) -> list[uuid.UUID]:
    if not seed_article_ids:
        return []
    res = await session.execute(_BFS, {"seed": seed_article_ids, "max_depth": max_depth})
    return [uuid.UUID(str(r[0])) for r in res.all()]
