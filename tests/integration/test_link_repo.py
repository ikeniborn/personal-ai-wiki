from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.links import LinkRepo
from paw.graph.repo import GraphRepo


async def _three(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    a = await repo.create(domain_id=dom.id, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=dom.id, slug="b", title="Bravo", storage_ref="b:b")
    c = await repo.create(domain_id=dom.id, slug="c", title="Charlie", storage_ref="b:c")
    return dom, a, b, c


async def test_backlinks_are_reverse_of_outgoing(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=a.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=c.id, dst_article_id=a.id, type="references")
    await db_session.commit()

    links = LinkRepo(db_session)
    back = await links.backlinks(a.id)
    assert {(x.link_type, x.article_id) for x in back} == {
        ("references", c.id),
        ("related", b.id),
    }
    # reciprocity: a is the outgoing target's backlink
    out_b = await links.outgoing(b.id)
    assert [(x.link_type, x.article_id) for x in out_b] == [("related", a.id)]


async def test_outgoing_grouped_orderable_by_type(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=c.id, type="related")
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await db_session.commit()
    out = await LinkRepo(db_session).outgoing(a.id)
    # ordered by (type, title): child(Bravo) before related(Charlie)
    assert [x.link_type for x in out] == ["child", "related"]
    assert [x.title for x in out] == ["Bravo", "Charlie"]


async def test_parent_child_raw_filters_types(db_session):
    dom, a, b, c = await _three(db_session)
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=c.id, type="related")
    await db_session.commit()
    raw = await LinkRepo(db_session).parent_child_raw(dom.id)
    assert raw == [(a.id, b.id, "child")]
