from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.storage.postgres import PostgresStorage


async def upsert_article(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    slug: str,
    title: str,
    markdown: str,
    summary: str,
    author_id: uuid.UUID | None,
) -> tuple[Article, bool]:
    repo = ArticleRepo(session)
    store = PostgresStorage(session)
    ref = await store.put(markdown.encode(), content_type="text/markdown")
    res = await session.execute(
        select(Article).where(Article.domain_id == domain_id, Article.slug == slug)
    )
    existing = res.scalar_one_or_none()
    if existing is None:
        art = await repo.create(
            domain_id=domain_id,
            slug=slug,
            title=title,
            storage_ref=ref,
            summary=summary or None,
        )
        await repo.add_revision(
            article_id=art.id, rev_no=1, storage_ref=ref, author_id=author_id, origin="ai"
        )
        return art, True
    new_rev = existing.current_rev + 1
    existing.title = title
    existing.storage_ref = ref
    existing.summary = summary or existing.summary
    existing.current_rev = new_rev
    await repo.add_revision(
        article_id=existing.id,
        rev_no=new_rev,
        storage_ref=ref,
        author_id=author_id,
        origin="ai",
    )
    return existing, False
