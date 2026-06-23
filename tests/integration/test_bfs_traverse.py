from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo
from paw.graph.traverse import bfs_expand


async def _art(db_session, dom_id, slug):
    return await ArticleRepo(db_session).create(
        domain_id=dom_id, slug=slug, title=slug.upper(), storage_ref=f"b:{slug}"
    )


async def test_outgoing_only_depth_bound(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    a = await _art(db_session, dom.id, "a")
    b = await _art(db_session, dom.id, "b")
    c = await _art(db_session, dom.id, "c")
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=c.id, type="related")
    await db_session.commit()
    # depth 1 from a -> {a, b} (not c)
    assert set(await bfs_expand(db_session, seed_article_ids=[a.id], max_depth=1)) == {a.id, b.id}
    # depth 2 -> {a, b, c}
    assert set(await bfs_expand(db_session, seed_article_ids=[a.id], max_depth=2)) == {
        a.id, b.id, c.id
    }
    # outgoing-only: from c reaches nothing new
    assert set(await bfs_expand(db_session, seed_article_ids=[c.id], max_depth=2)) == {c.id}


async def test_cycle_safe(db_session):
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s", wiki_prefix="w")
    a = await _art(db_session, dom.id, "a")
    b = await _art(db_session, dom.id, "b")
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=a.id, type="related")
    await db_session.commit()
    # a<->b cycle must terminate
    assert set(await bfs_expand(db_session, seed_article_ids=[a.id], max_depth=5)) == {a.id, b.id}


async def test_empty_seed_returns_empty(db_session):
    assert await bfs_expand(db_session, seed_article_ids=[], max_depth=2) == []
