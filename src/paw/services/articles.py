import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.storage.postgres import PostgresStorage


@dataclass
class ArticleBody:
    article: Article
    markdown: str


class ArticleService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = ArticleRepo(session)
        self._store = PostgresStorage(session)

    async def create(
        self, *, domain_id: uuid.UUID, slug: str, title: str, markdown: str, author_id: uuid.UUID
    ) -> Article:
        ref = await self._store.put(markdown.encode(), content_type="text/markdown")
        art = await self._repo.create(domain_id=domain_id, slug=slug, title=title, storage_ref=ref)
        await self._repo.add_revision(
            article_id=art.id, rev_no=1, storage_ref=ref, author_id=author_id, origin="user"
        )
        await self._s.commit()
        return art

    async def get_body(self, article_id: uuid.UUID) -> ArticleBody:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        markdown = (await self._store.get(art.storage_ref)).decode()
        return ArticleBody(article=art, markdown=markdown)

    async def update(
        self,
        *,
        article_id: uuid.UUID,
        expected_rev: int,
        title: str,
        markdown: str,
        author_id: uuid.UUID,
    ) -> Article:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        if art.current_rev != expected_rev:
            raise ProblemError(
                status=409, title="Conflict", detail=f"stale revision (current={art.current_rev})"
            )
        new_rev = art.current_rev + 1
        ref = await self._store.put(markdown.encode(), content_type="text/markdown")
        art.title = title
        art.storage_ref = ref
        art.current_rev = new_rev
        await self._repo.add_revision(
            article_id=art.id, rev_no=new_rev, storage_ref=ref, author_id=author_id, origin="user"
        )
        await self._s.commit()
        return art

    async def rollback(
        self, *, article_id: uuid.UUID, rev_no: int, author_id: uuid.UUID
    ) -> Article:
        art = await self._repo.get(article_id)
        if art is None:
            raise ProblemError(status=404, title="Article not found")
        revisions = await self._repo.list_revisions(article_id)
        target = next((r for r in revisions if r.rev_no == rev_no), None)
        if target is None:
            raise ProblemError(status=404, title="Revision not found")
        new_rev = art.current_rev + 1
        art.storage_ref = target.storage_ref
        art.current_rev = new_rev
        await self._repo.add_revision(
            article_id=art.id,
            rev_no=new_rev,
            storage_ref=target.storage_ref,
            author_id=author_id,
            origin="user",
        )
        await self._s.commit()
        return art

    async def list_by_domain(self, domain_id: uuid.UUID) -> list[Article]:
        return await self._repo.list_by_domain(domain_id)
