
import pytest
from tests.factories import seed_article_with_entities  # see Step 1a

from paw.db.session import get_sessionmaker
from paw.graph.age import projection, schema
from paw.graph.age.cypher import run_cypher
from paw.graph.age.naming import graph_name


@pytest.mark.usefixtures("wired_settings")
async def test_project_article_creates_nodes_and_edges() -> None:
    async with get_sessionmaker()() as s:
        domain_id, article_id = await seed_article_with_entities(s)
        await schema.ensure_graph(s, domain_id)
        await projection.project_article(s, domain_id=domain_id, article_id=article_id)
        await s.commit()

        g = graph_name(domain_id)
        arts = await run_cypher(
            s, graph=g, body="MATCH (a:Article {id: $id}) RETURN a.title",
            columns="title agtype", params={"id": str(article_id)},
        )
        assert len(arts) == 1
        bridged = await run_cypher(
            s, graph=g,
            body="MATCH (c:Chunk)-[:CHUNK_MENTIONS]->(e:Entity) RETURN count(e)",
            columns="n agtype",
        )
        assert bridged[0][0] >= 1
        await schema.drop_graph(s, domain_id)
        await s.commit()
