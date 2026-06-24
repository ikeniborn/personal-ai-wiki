from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.query_cache import QueryCacheRepo


async def mark_cache_stale(
    session: AsyncSession, *, domain_id: uuid.UUID, article_ids: list[uuid.UUID]
) -> None:
    """Mark query_cache entries that depend on any of ``article_ids`` as stale.

    Runs in the SAME transaction as the article write (no eventual path).
    Article writers (ingest/fix/format) call this after upserting an article so a
    later read serves the cached answer with a "may be outdated" flag + Refresh.
    """
    if not article_ids:
        return None
    await QueryCacheRepo(session).mark_stale_for_articles(
        domain_id=domain_id, article_ids=article_ids
    )
    return None
