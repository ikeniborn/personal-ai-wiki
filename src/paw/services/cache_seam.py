from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def mark_domain_cache_stale(session: AsyncSession, domain_id: uuid.UUID) -> None:
    """Phase 7 seam: invalidate cached query answers for a domain after a write.

    No-op until the ``query_cache`` table exists (Phase 7). Fix/Format call this on
    every article write so Phase 7 implements the body without touching the writers.
    """
    return None
