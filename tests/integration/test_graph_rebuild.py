import pytest
from tests.factories import seed_article_with_entities

from paw.db.session import get_sessionmaker
from paw.graph.age.cypher import run_cypher
from paw.graph.age.naming import graph_name
from paw.jobs.tasks import _rebuild_domain_graph  # pure-ish core extracted in Step 3


@pytest.mark.usefixtures("wired_settings")
async def test_rebuild_backfills_and_is_idempotent() -> None:
    async with get_sessionmaker()() as s:
        domain_id, article_id = await seed_article_with_entities(s)
        await s.commit()
    # Domain has rows but no graph yet (engine was cte at create time). Rebuild backfills it.
    async with get_sessionmaker()() as s:
        await _rebuild_domain_graph(s, domain_id, on_batch=None)
        await s.commit()
    async with get_sessionmaker()() as s:
        rows = await run_cypher(
            s, graph=graph_name(domain_id),
            body="MATCH (a:Article {id: $id}) RETURN a.title", columns="title agtype",
            params={"id": str(article_id)},
        )
        assert rows == [("Alpha",)]
    # Second rebuild must not error and must keep exactly one article node.
    async with get_sessionmaker()() as s:
        await _rebuild_domain_graph(s, domain_id, on_batch=None)
        await s.commit()
        rows = await run_cypher(
            s, graph=graph_name(domain_id),
            body="MATCH (a:Article) RETURN count(a)", columns="n agtype",
        )
        assert rows[0][0] == 1
