import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf, require_role
from paw.db.models import User
from paw.security.sanitize import render_markdown
from paw.services.articles import ArticleService

router = APIRouter(tags=["articles"])


class ArticleCreate(BaseModel):
    slug: str
    title: str
    markdown: str


class ArticleUpdate(BaseModel):
    title: str
    markdown: str
    expected_rev: int


class RollbackRequest(BaseModel):
    rev_no: int


class ArticleOut(BaseModel):
    id: str
    slug: str
    title: str
    current_rev: int


class ArticleDetail(ArticleOut):
    html: str


@router.post("/domains/{domain_id}/articles", status_code=201, response_model=ArticleOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def create_article(domain_id: uuid.UUID, body: ArticleCreate,
                         user: User = Depends(current_user),
                         session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).create(
        domain_id=domain_id, slug=body.slug, title=body.title,
        markdown=body.markdown, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)


@router.get("/articles/{article_id}", response_model=ArticleDetail)
async def get_article(
    article_id: uuid.UUID,
    session: AsyncSession = Depends(db),
    _: User = Depends(require_role("admin", "editor", "viewer")),
) -> ArticleDetail:
    body = await ArticleService(session).get_body(article_id)
    return ArticleDetail(
        id=str(body.article.id), slug=body.article.slug, title=body.article.title,
        current_rev=body.article.current_rev, html=render_markdown(body.markdown),
    )


@router.put("/articles/{article_id}", response_model=ArticleOut,
            dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def update_article(article_id: uuid.UUID, body: ArticleUpdate,
                         user: User = Depends(current_user),
                         session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).update(
        article_id=article_id, expected_rev=body.expected_rev, title=body.title,
        markdown=body.markdown, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)


@router.post("/articles/{article_id}/rollback", response_model=ArticleOut,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def rollback_article(article_id: uuid.UUID, body: RollbackRequest,
                           user: User = Depends(current_user),
                           session: AsyncSession = Depends(db)) -> ArticleOut:
    art = await ArticleService(session).rollback(
        article_id=article_id, rev_no=body.rev_no, author_id=user.id,
    )
    return ArticleOut(id=str(art.id), slug=art.slug, title=art.title,
                      current_rev=art.current_rev)
