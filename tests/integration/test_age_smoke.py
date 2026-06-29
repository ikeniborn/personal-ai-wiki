import pytest
from sqlalchemy import text

from paw.db.session import get_sessionmaker

# A valid graph name: "g_" + 32 lowercase hex chars (matches naming.GRAPH_NAME_RE)
_G_PARAMS = "g_00000000000000000000000000000001"


@pytest.mark.usefixtures("wired_settings")
async def test_cypher_callable_over_asyncpg() -> None:
    async with get_sessionmaker()() as s:
        await s.execute(text("SELECT create_graph('g_smoke')"))
        res = await s.execute(
            text("SELECT * FROM cypher('g_smoke', $$ RETURN 1 $$) AS (n agtype)")
        )
        assert [r[0] for r in res.all()] == ["1"]
        await s.execute(text("SELECT drop_graph('g_smoke', true)"))
        await s.commit()


@pytest.mark.usefixtures("wired_settings")
async def test_run_cypher_binds_params() -> None:
    from sqlalchemy import text as _text

    from paw.graph.age import cypher

    async with get_sessionmaker()() as s:
        await s.execute(_text(f"SELECT create_graph('{_G_PARAMS}')"))
        await s.execute(_text(f"SELECT create_vlabel('{_G_PARAMS}', 'T')"))
        try:
            await cypher.exec_cypher(
                s, graph=_G_PARAMS, body="MERGE (n:T {id: $id, name: $name})",
                params={"id": "1", "name": '$$ evil //'},
            )
            rows = await cypher.run_cypher(
                s, graph=_G_PARAMS,
                body="MATCH (n:T) WHERE n.id IN $ids RETURN n.name",
                columns="name agtype", params={"ids": ["1"]},
            )
            assert rows == [('$$ evil //',)]
        finally:
            await s.execute(_text(f"SELECT drop_graph('{_G_PARAMS}', true)"))
            await s.commit()
