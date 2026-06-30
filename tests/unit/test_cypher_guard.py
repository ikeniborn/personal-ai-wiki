import pytest

from paw.graph.age import cypher


async def test_run_cypher_rejects_non_internal_graph_name():
    with pytest.raises(ValueError):
        await cypher.run_cypher(
            object(),  # type: ignore[arg-type]
            graph="drop table users",
            body="RETURN 1",
            columns="x agtype",
        )


async def test_exec_cypher_rejects_non_internal_graph_name():
    with pytest.raises(ValueError):
        await cypher.exec_cypher(
            object(),  # type: ignore[arg-type]
            graph="g_not_hex",
            body="CREATE (:Node)",
        )
