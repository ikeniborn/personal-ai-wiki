import pytest
from sqlalchemy import text

from paw.db.session import get_sessionmaker


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
