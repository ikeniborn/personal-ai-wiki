from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.graph.repo import GraphRepo
from paw.services.articles import ArticleService


async def _seed(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    a = await repo.create(domain_id=dom.id, slug="a", title="Alpha", storage_ref="b:a")
    b = await repo.create(domain_id=dom.id, slug="b", title="Bravo", storage_ref="b:b")
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="src.md", type="md", checksum="x"
    )
    graph = GraphRepo(db_session)
    await graph.link(domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="child")
    await graph.link(domain_id=dom.id, src_article_id=b.id, dst_article_id=a.id, type="related")
    await CitationRepo(db_session).create(
        article_id=a.id, source_id=src.id, quote="q", locator="l"
    )
    await db_session.commit()
    return dom, a, b


async def test_get_meta_aggregates_links_citations_revisions(db_session):
    dom, a, b = await _seed(db_session)
    meta = await ArticleService(db_session).get_meta(a.id)
    assert {x.article_id for x in meta.backlinks} == {b.id}  # b -> a (related)
    assert {(x.link_type, x.article_id) for x in meta.outgoing} == {("child", b.id)}
    assert meta.citations[0].source_filename == "src.md"
    assert meta.revisions == []  # repo.create makes no revision row by itself


async def test_domain_tree_nests_child_links(db_session):
    dom, a, b = await _seed(db_session)
    tree = await ArticleService(db_session).domain_tree(dom.id)
    # a --child--> b : a is the parent root, b nested under it
    assert [n.title for n in tree] == ["Alpha"]
    assert [c.title for c in tree[0].children] == ["Bravo"]


async def test_slug_map(db_session):
    dom, a, b = await _seed(db_session)
    smap = await ArticleService(db_session).slug_map(dom.id)
    assert smap == {"a": a.id, "b": b.id}
