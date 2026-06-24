from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Article
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.links import LinkedArticle, LinkRepo
from paw.harness.retrieve import retrieve
from paw.providers.base import EmbeddingProvider
from paw.providers.config import RetrievalConfig
from paw.storage.postgres import PostgresStorage
from paw.vector.search import CURRENT_EMBEDDING_VERSION


class PassageOut(BaseModel):
    chunk_id: str
    slug: str
    heading_path: str | None
    text: str
    score: float


class RefOut(BaseModel):
    article_id: str
    slug: str
    title: str


class SearchResult(BaseModel):
    passages: list[PassageOut]
    refs: list[RefOut]


class ArticleResult(BaseModel):
    id: str
    slug: str
    title: str
    summary: str | None
    current_rev: int
    updated_at: str
    markdown: str


class LinkOut(BaseModel):
    type: str
    article_id: str
    slug: str
    title: str


class LinksResult(BaseModel):
    article_id: str
    outgoing: list[LinkOut]
    backlinks: list[LinkOut]


async def _resolve_article(session: AsyncSession, domain_id: uuid.UUID, ref: str) -> Article:
    repo = ArticleRepo(session)
    try:
        uid: uuid.UUID | None = uuid.UUID(ref)
    except ValueError:
        uid = None
    art = await repo.get(uid) if uid is not None else await repo.get_by_slug(domain_id, ref)
    if art is None or art.domain_id != domain_id:
        raise ValueError("article not found in domain")
    return art


async def search_wiki(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    query: str,
    embedder: EmbeddingProvider,
    cfg: RetrievalConfig,
    embedding_version: int = CURRENT_EMBEDDING_VERSION,
    top_k: int | None = None,
) -> SearchResult:
    if top_k is not None:
        cfg = cfg.model_copy(update={"top_n": int(top_k)})
    ctx = await retrieve(
        session,
        domain_id=domain_id,
        query=query,
        embedder=embedder,
        cfg=cfg,
        embedding_version=embedding_version,
        embed_model=getattr(embedder, "embedding_model", ""),
    )
    return SearchResult(
        passages=[
            PassageOut(
                chunk_id=str(p.chunk_id),
                slug=p.slug,
                heading_path=p.heading_path,
                text=p.text,
                score=p.score,
            )
            for p in ctx.passages
        ],
        refs=[RefOut(article_id=str(r.article_id), slug=r.slug, title=r.title) for r in ctx.refs],
    )


async def get_article(session: AsyncSession, *, domain_id: uuid.UUID, ref: str) -> ArticleResult:
    art = await _resolve_article(session, domain_id, ref)
    markdown = (await PostgresStorage(session).get(art.storage_ref)).decode()
    return ArticleResult(
        id=str(art.id),
        slug=art.slug,
        title=art.title,
        summary=art.summary,
        current_rev=art.current_rev,
        updated_at=art.updated_at.isoformat(),
        markdown=markdown,
    )


async def list_links(session: AsyncSession, *, domain_id: uuid.UUID, ref: str) -> LinksResult:
    art = await _resolve_article(session, domain_id, ref)
    repo = LinkRepo(session)

    def _conv(items: list[LinkedArticle]) -> list[LinkOut]:
        return [
            LinkOut(type=la.link_type, article_id=str(la.article_id), slug=la.slug, title=la.title)
            for la in items
        ]

    return LinksResult(
        article_id=str(art.id),
        outgoing=_conv(await repo.outgoing(art.id)),
        backlinks=_conv(await repo.backlinks(art.id)),
    )
