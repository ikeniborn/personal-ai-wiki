import pytest

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.graph.repo import GraphRepo


async def _two_articles(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    a1 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a1", title="A1", storage_ref="blob:1"
    )
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="blob:2"
    )
    return dom, a1, a2


async def test_link_is_idempotent_and_rejects_self(db_session):
    dom, a1, a2 = await _two_articles(db_session)
    repo = GraphRepo(db_session)
    assert (
        await repo.link(
            domain_id=dom.id, src_article_id=a1.id, dst_article_id=a2.id, type="related"
        )
        is True
    )
    assert (
        await repo.link(
            domain_id=dom.id, src_article_id=a1.id, dst_article_id=a2.id, type="related"
        )
        is False
    )
    await db_session.commit()
    with pytest.raises(ValueError):
        await repo.link(
            domain_id=dom.id, src_article_id=a1.id, dst_article_id=a1.id, type="related"
        )


async def test_cooccurrence_threshold(db_session):
    dom, a1, a2 = await _two_articles(db_session)
    ents = EntityRepo(db_session)
    for name in ("QUIC", "UDP", "TLS"):
        e = await ents.upsert(domain_id=dom.id, name=name)
        await ents.tag_article(article_id=a1.id, entity_id=e.id)
        await ents.tag_article(article_id=a2.id, entity_id=e.id)
    await db_session.commit()
    repo = GraphRepo(db_session)
    assert await repo.cooccurrence_targets(domain_id=dom.id, article_id=a1.id, threshold=3) == [
        a2.id
    ]
    assert await repo.cooccurrence_targets(domain_id=dom.id, article_id=a1.id, threshold=4) == []
