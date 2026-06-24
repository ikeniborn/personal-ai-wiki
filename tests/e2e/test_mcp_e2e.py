import asyncio

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from tests.stubs import StubEmbeddingProvider

import paw.mcp.server as mcp_server
from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.graph.repo import GraphRepo
from paw.ingest.chunking import ChunkSpec
from paw.main import create_app
from paw.security.secrets import SecretBox
from paw.services.api_keys import ApiKeyService
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage
from paw.vector.embed import embed_and_write

_FERNET = "k" * 43 + "="


async def _seed(db_session) -> str:
    """Seed provider + corpus + an api key; return the full bearer token."""
    await ProviderSettingsService(db_session, box=SecretBox(_FERNET)).persist_provider(
        base_url="http://x", chat_model="m", embedding_model="e", embedding_dim=8, api_key="k"
    )
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    store = PostgresStorage(db_session)
    repo = ArticleRepo(db_session)
    tcp_ref = await store.put(b"# TCP\nreliable ordered delivery", content_type="text/markdown")
    udp_ref = await store.put(b"# UDP\ndatagram", content_type="text/markdown")
    tcp = await repo.create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref=tcp_ref, summary="reliable"
    )
    udp = await repo.create(
        domain_id=dom.id, slug="udp", title="UDP", storage_ref=udp_ref, summary="datagram"
    )
    await ensure_embedding_column(db_session, 8)
    await embed_and_write(
        db_session, article_id=tcp.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable ordered")],
        embedder=StubEmbeddingProvider(dim=8),
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=tcp.id, dst_article_id=udp.id, type="related"
    )
    user = await UserRepo(db_session).create(email="mcp@b.c", pw_hash="x", role="admin")
    await db_session.commit()
    issued = await ApiKeyService(db_session).issue(user_id=user.id, scopes=["read"])
    return issued.token


async def test_mcp_round_trip(db_session, wired_settings, monkeypatch):
    monkeypatch.setattr(
        mcp_server, "build_embedding_provider", lambda pc, box: StubEmbeddingProvider(dim=8)
    )
    token = await _seed(db_session)

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        deadline = asyncio.get_event_loop().time() + 10.0
        while not server.started:  # wait for the socket to bind
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("uvicorn failed to start within 10s")
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/mcp"
        headers = {"Authorization": f"Bearer {token}"}

        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "search_wiki",
                    "get_article",
                    "list_links",
                }

                search = await session.call_tool(
                    "search_wiki", {"query": "reliable", "domain": "net"}
                )
                assert search.structuredContent is not None
                assert any(p["slug"] == "tcp" for p in search.structuredContent["passages"])

                article = await session.call_tool(
                    "get_article", {"ref": "tcp", "domain": "net"}
                )
                assert article.structuredContent is not None
                assert article.structuredContent["slug"] == "tcp"
                assert "reliable" in article.structuredContent["markdown"]

                links = await session.call_tool(
                    "list_links", {"article": "tcp", "domain": "net"}
                )
                assert links.structuredContent is not None
                assert any(
                    e["type"] == "related" and e["slug"] == "udp"
                    for e in links.structuredContent["outgoing"]
                )
    finally:
        server.should_exit = True
        await serve_task
