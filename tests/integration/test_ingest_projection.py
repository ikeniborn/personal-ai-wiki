"""Tests for in-transaction AGE projection during ingest (Task 10).

Step 1: atomicity — rollback leaves no orphan graph nodes.
Step 4: end-to-end — run_ingest with engine=age projects the article node.
"""
from __future__ import annotations

import pytest
from tests.factories import seed_article_with_entities
from tests.stubs import StubChatProvider, StubEmbeddingProvider

from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.db.session import get_sessionmaker
from paw.graph.age import projection, schema
from paw.graph.age.cypher import run_cypher
from paw.graph.age.naming import graph_name
from paw.providers.config import WikiConfig
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage

# ---------------------------------------------------------------------------
# Step 1: atomicity test
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("wired_settings")
async def test_rollback_leaves_no_orphan_graph_nodes() -> None:
    async with get_sessionmaker()() as s:
        domain_id, article_id = await seed_article_with_entities(s)
        await schema.ensure_graph(s, domain_id)
        await s.commit()
    # New txn: project then roll back.
    async with get_sessionmaker()() as s:
        await projection.project_article(s, domain_id=domain_id, article_id=article_id)
        await s.rollback()
    # Third txn: node must be gone — AGE shares the transaction.
    async with get_sessionmaker()() as s:
        rows = await run_cypher(
            s, graph=graph_name(domain_id),
            body="MATCH (a:Article {id: $id}) RETURN a.id",
            columns="id agtype", params={"id": str(article_id)},
        )
        assert rows == []  # AGE shares the txn -> rollback removed the node
        await schema.drop_graph(s, domain_id)
        await s.commit()


# ---------------------------------------------------------------------------
# Step 4: end-to-end ingest projection test
# ---------------------------------------------------------------------------


def _draft_chat() -> StubChatProvider:
    return StubChatProvider(
        [
            StubChatProvider.tool(
                "emit_result", {"entities": ["QUIC", "UDP"], "key_points": ["fast transport"]}
            ),
            StubChatProvider.tool(
                "emit_result",
                {
                    "slug": "quic",
                    "title": "QUIC",
                    "summary": "QUIC is a transport protocol.",
                    "markdown": "## Overview\n\nQUIC runs over UDP. It is fast. Low latency.",
                    "entities": ["QUIC", "UDP"],
                    "citations": [{"quote": "QUIC runs over UDP", "locator": "p1"}],
                },
            ),
        ]
    )


@pytest.mark.usefixtures("wired_settings")
async def test_ingest_task_projects_article_into_age_graph() -> None:
    """Running ingest_domain with engine=age must create an Article node in the graph."""
    maker = get_sessionmaker()

    # Seed domain with engine=age, source, and job.
    async with maker() as s:
        await ProviderSettingsService(s).set_graph_engine("age")
        await s.commit()

    async with maker() as s:
        dom = await DomainRepo(s).create(name="net", source_prefix="s", wiki_prefix="w")
        ref = await PostgresStorage(s).put(
            b"QUIC runs over UDP.", content_type="text/markdown"
        )
        src = await SourceRepo(s).create(
            domain_id=dom.id, storage_ref=ref, filename="q.md", type="md", checksum="c2"
        )
        job = await JobRepo(s).create(domain_id=dom.id, kind="ingest")
        # Bootstrap the AGE graph for this domain (engine=age is now active globally).
        await schema.ensure_graph(s, dom.id)
        await s.commit()

    domain_id = dom.id

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    # Run the real ingest_domain task with stubbed providers.
    import paw.jobs.tasks as tasks_mod_local  # noqa: PLC0415 (avoids shadowing outer import)

    old_build = tasks_mod_local._build_providers
    tasks_mod_local._build_providers = fake_build
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415

        from paw.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            out = await tasks_mod_local.ingest_domain(
                {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
            )
        finally:
            await redis_client.aclose()
    finally:
        tasks_mod_local._build_providers = old_build

    assert out == "succeeded"

    # Verify the Article node exists in AGE.
    async with maker() as s:
        got_job = await JobRepo(s).get(job.id)
        assert got_job is not None and got_job.article_id is not None
        article_id = got_job.article_id
        rows = await run_cypher(
            s,
            graph=graph_name(domain_id),
            body="MATCH (a:Article {id: $id}) RETURN a.title",
            columns="title agtype",
            params={"id": str(article_id)},
        )
        assert len(rows) == 1
        # Cleanup: drop the AGE graph so _clean_db teardown isn't confused.
        await schema.drop_graph(s, domain_id)
        await s.commit()
