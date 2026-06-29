import pytest
from tests.factories import seed_cross_domain_pair, seed_two_linked_articles

from paw.db.session import get_sessionmaker
from paw.graph.age import projection, schema
from paw.graph.age.query import graph_expand
from paw.providers.config import GraphConfig


@pytest.mark.usefixtures("wired_settings")
async def test_entity_bridge_returns_neighbour_with_provenance() -> None:
    async with get_sessionmaker()() as s:
        domain_id, seed_chunk_id, seed_article_id, other_article_id = (
            await seed_two_linked_articles(s)
        )
        await schema.ensure_graph(s, domain_id)
        await projection.project_article(s, domain_id=domain_id, article_id=seed_article_id)
        await projection.project_article(s, domain_id=domain_id, article_id=other_article_id)
        await s.commit()
        try:
            out = await graph_expand(
                s, domain_id=domain_id, seed_chunk_ids=[seed_chunk_id],
                seed_article_ids=[seed_article_id], cfg=GraphConfig(engine="age"),
            )
            ids = [n.article_id for n in out]
            assert other_article_id in ids
            bridged = next(n for n in out if n.article_id == other_article_id)
            assert bridged.via  # carries "via concepts"
        finally:
            await s.rollback()
            await schema.drop_graph(s, domain_id)
            await s.commit()


@pytest.mark.usefixtures("wired_settings")
async def test_cross_domain_isolation() -> None:
    async with get_sessionmaker()() as s:
        a, b = await seed_cross_domain_pair(s)  # returns two (domain, chunk, article) tuples
        for dom, _ch, art in (a, b):
            await schema.ensure_graph(s, dom)
            await projection.project_article(s, domain_id=dom, article_id=art)
        await s.commit()
        try:
            # Expand on domain A with A's seed -> zero domain-B articles.
            out = await graph_expand(
                s, domain_id=a[0], seed_chunk_ids=[a[1]], seed_article_ids=[a[2]],
                cfg=GraphConfig(engine="age"),
            )
            assert b[2] not in [n.article_id for n in out]
        finally:
            await s.rollback()
            for dom, _ch, _art in (a, b):
                await schema.drop_graph(s, dom)
            await s.commit()


@pytest.mark.usefixtures("wired_settings")
async def test_injection_title_is_inert() -> None:
    async with get_sessionmaker()() as s:
        evil = "$$ ) MATCH (x) DETACH DELETE x //"
        domain_id, seed_chunk_id, seed_article_id, other_article_id = (
            await seed_two_linked_articles(s, other_title=evil)
        )
        await schema.ensure_graph(s, domain_id)
        await projection.project_article(s, domain_id=domain_id, article_id=seed_article_id)
        await projection.project_article(s, domain_id=domain_id, article_id=other_article_id)
        await s.commit()
        try:
            await graph_expand(
                s, domain_id=domain_id, seed_chunk_ids=[seed_chunk_id],
                seed_article_ids=[seed_article_id], cfg=GraphConfig(engine="age"),
            )
            # The malicious title did not delete anything: the seed article still exists.
            from paw.graph.age.cypher import run_cypher
            from paw.graph.age.naming import graph_name

            still = await run_cypher(
                s, graph=graph_name(domain_id),
                body="MATCH (a:Article {id: $id}) RETURN a.id", columns="id agtype",
                params={"id": str(seed_article_id)},
            )
            assert len(still) == 1
        finally:
            await s.rollback()
            await schema.drop_graph(s, domain_id)
            await s.commit()
