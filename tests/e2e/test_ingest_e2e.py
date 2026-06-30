import pytest
from httpx import ASGITransport, AsyncClient
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
from paw.db.repos.jobs import JobRepo
from paw.main import create_app
from paw.providers.config import WikiConfig

_STRONG_PASSWORD = "pw12345678901"


def _chat() -> StubChatProvider:
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


@pytest.fixture
async def client(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_ingest_e2e(client, db_session, redis_client, monkeypatch):
    # setup wizard (captures dim 8, creates vector column)
    await client.post(
        "/api/v1/setup",
        json={
            "email": "admin@example.com",
            "password": _STRONG_PASSWORD,
            "base_url": "https://api.example/v1",
            "api_key": "sk-x",
            "chat_model": "gpt-x",
            "embedding_model": "emb-x",
            "embedding_dim": 8,
        },
    )
    await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _STRONG_PASSWORD},
    )
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nQUIC over UDP.", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources", files=files, headers=h)).json()
    job_id = (
        await client.post(
            f"/api/v1/domains/{dom['id']}/ingest", json={"source_id": src["id"]}, headers=h
        )
    ).json()["job_id"]

    # run the worker task inline with stub providers
    async def fake_build(session, box):
        return _chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, job_id, dom["id"], source_id=src["id"]
    )
    assert out == "succeeded"

    got = await JobRepo(db_session).get(__import__("uuid").UUID(job_id))
    assert got is not None and got.article_id is not None
    page = await client.get(f"/api/v1/articles/{got.article_id}")
    assert page.status_code == 200
    assert "QUIC" in page.json()["html"]
    from sqlalchemy import text

    n = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE article_id=:a AND embedding IS NOT NULL"),
        {"a": str(got.article_id)},
    )
    assert n.scalar_one() >= 1
