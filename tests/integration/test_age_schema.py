import uuid

import pytest
from sqlalchemy import text

from paw.db.session import get_sessionmaker
from paw.graph.age import schema
from paw.graph.age.naming import graph_name


@pytest.mark.usefixtures("wired_settings")
async def test_ensure_graph_idempotent_and_creates_labels() -> None:
    did = uuid.uuid4()
    name = graph_name(did)
    async with get_sessionmaker()() as s:
        await schema.ensure_graph(s, did)
        await schema.ensure_graph(s, did)  # second call must not error
        await s.commit()
        row = await s.execute(
            text("SELECT count(*) FROM ag_catalog.ag_graph WHERE name = :n"), {"n": name}
        )
        assert row.scalar_one() == 1
        labels = await s.execute(
            text(
                "SELECT count(*) FROM ag_catalog.ag_label l "
                "JOIN ag_catalog.ag_graph g ON g.graphid = l.graph WHERE g.name = :n"
            ),
            {"n": name},
        )
        # 3 vlabels + 4 elabels + AGE's 2 default labels (_ag_label_vertex/_ag_label_edge)
        assert labels.scalar_one() >= 7
        await schema.drop_graph(s, did)
        await s.commit()
