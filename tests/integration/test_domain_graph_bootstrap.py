import pytest
from sqlalchemy import text

from paw.db.session import get_sessionmaker
from paw.graph.age.naming import graph_name
from paw.services.domains import DomainService
from paw.services.provider_settings import ProviderSettingsService


@pytest.mark.usefixtures("wired_settings")
async def test_bootstrap_creates_graph_when_engine_age() -> None:
    async with get_sessionmaker()() as s:
        # set global engine = age
        await ProviderSettingsService(s).set_graph_engine("age")  # see Step 1a
        await s.commit()
        dom = await DomainService(s).create("Bootstrapped")
        async with get_sessionmaker()() as s2:
            row = await s2.execute(
                text("SELECT count(*) FROM ag_catalog.ag_graph WHERE name = :n"),
                {"n": graph_name(dom.id)},
            )
            assert row.scalar_one() == 1


@pytest.mark.usefixtures("wired_settings")
async def test_no_graph_when_engine_cte_default() -> None:
    async with get_sessionmaker()() as s:
        dom = await DomainService(s).create("CteDefault")
        async with get_sessionmaker()() as s2:
            row = await s2.execute(
                text("SELECT count(*) FROM ag_catalog.ag_graph WHERE name = :n"),
                {"n": graph_name(dom.id)},
            )
            assert row.scalar_one() == 0
