from __future__ import annotations

from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.providers.config import WikiConfig
from paw.storage.postgres import PostgresStorage


def _draft_chat() -> StubChatProvider:
    return StubChatProvider(
        [
            StubChatProvider.tool("emit_result", {"entities": ["QUIC"], "key_points": ["fast"]}),
            StubChatProvider.tool(
                "emit_result",
                {
                    "slug": "quic",
                    "title": "QUIC",
                    "summary": "QUIC is fast.",
                    "markdown": "## Overview\n\nQUIC over UDP. It is fast. Low latency.",
                    "entities": ["QUIC"],
                    "citations": [{"quote": "QUIC over UDP", "locator": "p1"}],
                },
            ),
        ]
    )


async def _seed(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    ref = await PostgresStorage(db_session).put(
        b"QUIC runs over UDP.", content_type="text/markdown"
    )
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref=ref, filename="q.md", type="md", checksum="c1"
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="ingest")
    await db_session.commit()
    return dom, src, job


async def test_ingest_task_success(db_session, redis_client, wired_settings, monkeypatch):
    dom, src, job = await _seed(db_session)

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
    )
    assert out == "succeeded"
    got = await JobRepo(db_session).get(job.id)
    assert got is not None and got.status == "succeeded" and got.article_id is not None


async def test_ingest_task_cancel_leaves_no_article(
    db_session, redis_client, wired_settings, monkeypatch
):
    dom, src, job = await _seed(db_session)
    await JobRepo(db_session).request_cancel(job.id)  # cancel before it runs
    await db_session.commit()

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
    )
    assert out == "cancelled"
    from sqlalchemy import text

    n = await db_session.execute(
        text("SELECT count(*) FROM articles WHERE domain_id=:d"), {"d": str(dom.id)}
    )
    assert n.scalar_one() == 0  # no partial article
