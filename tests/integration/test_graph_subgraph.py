from sqlalchemy import text

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo


async def test_backlink_index_exists(db_session):
    res = await db_session.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_links_dst_article_id'")
    )
    assert res.scalar_one_or_none() == 1


async def _domain_with_articles(db_session, n):
    dom = await DomainRepo(db_session).create(name="g", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    arts = []
    for i in range(n):
        arts.append(
            await repo.create(
                domain_id=dom.id,
                slug=f"a{i}",
                title=f"A{i}",
                storage_ref=f"blob:{i}",
                summary=f"summary {i}",
            )
        )
    return dom, arts


async def test_subgraph_returns_nodes_and_typed_edges(db_session):
    dom, arts = await _domain_with_articles(db_session, 3)
    graph = GraphRepo(db_session)
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[1].id, type="related"
    )
    await graph.link(
        domain_id=dom.id, src_article_id=arts[1].id, dst_article_id=arts[2].id, type="related"
    )
    await db_session.commit()

    nodes, edges = await graph.subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=1, types=None
    )
    assert {n.id for n in nodes} == {arts[0].id, arts[1].id}
    assert any(n.summary == "summary 0" for n in nodes)  # briefs carry the summary
    assert {(e.src, e.dst, e.type) for e in edges} == {(arts[0].id, arts[1].id, "related")}


async def test_subgraph_type_filter_excludes_other_types(db_session):
    dom, arts = await _domain_with_articles(db_session, 3)
    graph = GraphRepo(db_session)
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[1].id, type="related"
    )
    await graph.link(
        domain_id=dom.id, src_article_id=arts[0].id, dst_article_id=arts[2].id, type="parent"
    )
    await db_session.commit()

    nodes, edges = await graph.subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=2, types=["related"]
    )
    assert {n.id for n in nodes} == {arts[0].id, arts[1].id}  # parent edge + its node excluded
    assert [e.type for e in edges] == ["related"]


async def test_subgraph_isolated_root_returns_just_itself(db_session):
    dom, arts = await _domain_with_articles(db_session, 2)
    nodes, edges = await GraphRepo(db_session).subgraph(
        domain_id=dom.id, root_article_id=arts[0].id, depth=2, types=None
    )
    assert [n.id for n in nodes] == [arts[0].id]
    assert edges == []
