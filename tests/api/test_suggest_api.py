import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.managed import ensure_query_cache_embedding_column
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password

_FERNET = "k" * 43 + "="


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="a@b.c", pw_hash=hash_password("pw12345"), role="viewer"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await ensure_query_cache_embedding_column(db_session, 4)
    repo = QueryCacheRepo(db_session)
    for norm, hits in [("tcp basics", 1), ("tcp handshake", 5), ("udp facts", 9)]:
        cid = await repo.upsert(
            domain_id=dom.id, query_norm=norm, answer_md="A", refs=[], passages=[],
            model="m", prompt_version="1", query_vector=[1.0, 0.0, 0.0, 0.0],
        )
        for _ in range(hits):
            await repo.touch(cache_id=cid)
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "pw12345"})
        c._dom = dom  # type: ignore[attr-defined]
        yield c


async def test_suggest_ranks_by_hit_count(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest", params={"q": "tcp"})
    assert r.status_code == 200
    assert r.json()["suggestions"] == ["tcp handshake", "tcp basics"]


async def test_suggest_empty_query_returns_empty(client):
    r = await client.get(f"/api/v1/domains/{client._dom.id}/suggest", params={"q": ""})
    assert r.status_code == 200 and r.json()["suggestions"] == []
