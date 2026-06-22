import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article, ArticleRevision


class ArticleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        domain_id: uuid.UUID,
        slug: str,
        title: str,
        storage_ref: str,
        summary: str | None = None,
    ) -> Article:
        a = Article(
            domain_id=domain_id,
            slug=slug,
            title=title,
            storage_ref=storage_ref,
            summary=summary,
            current_rev=1,
        )
        self._s.add(a)
        await self._s.flush()
        return a

    async def get(self, article_id: uuid.UUID) -> Article | None:
        return await self._s.get(Article, article_id)

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Article]:
        res = await self._s.execute(
            select(Article).where(Article.domain_id == domain_id).order_by(Article.slug)
        )
        return list(res.scalars().all())

    async def add_revision(
        self,
        *,
        article_id: uuid.UUID,
        rev_no: int,
        storage_ref: str,
        author_id: uuid.UUID | None,
        origin: str,
    ) -> ArticleRevision:
        r = ArticleRevision(
            article_id=article_id,
            rev_no=rev_no,
            storage_ref=storage_ref,
            author_id=author_id,
            origin=origin,
        )
        self._s.add(r)
        await self._s.flush()
        return r

    async def list_revisions(self, article_id: uuid.UUID) -> list[ArticleRevision]:
        res = await self._s.execute(
            select(ArticleRevision)
            .where(ArticleRevision.article_id == article_id)
            .order_by(ArticleRevision.rev_no.desc())
        )
        return list(res.scalars().all())
