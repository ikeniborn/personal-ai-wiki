import pytest
from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo
from paw.ingest.chunking import ChunkSpec
from paw.mcp import tools as mcp_tools
from paw.providers.config import RetrievalConfig
from paw.storage.postgres import PostgresStorage
from paw.vector.embed import embed_and_write


async def _seed(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    repo = ArticleRepo(db_session)
    store = PostgresStorage(db_session)
    tcp_ref = await store.put(b"# TCP\nreliable ordered delivery", content_type="text/markdown")
    udp_ref = await store.put(b"# UDP\ndatagram", content_type="text/markdown")
    tcp = await repo.create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref=tcp_ref, summary="reliable"
    )
    udp = await repo.create(
        domain_id=dom.id, slug="udp", title="UDP", storage_ref=udp_ref, summary="datagram"
    )
    await ensure_embedding_column(db_session, dim)
    emb = StubEmbeddingProvider(dim=dim)
    await embed_and_write(
        db_session, article_id=tcp.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable ordered")],
        embedder=emb,
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=tcp.id, dst_article_id=udp.id, type="related"
    )
    await db_session.commit()
    return dom, tcp, udp, emb


async def test_search_wiki_returns_passages_and_refs(db_session):
    dom, tcp, _udp, emb = await _seed(db_session)
    out = await mcp_tools.search_wiki(
        db_session, domain_id=dom.id, query="reliable",
        embedder=emb, cfg=RetrievalConfig(k1=10, k2=10, top_n=5), embedding_version=1,
    )
    assert out.passages and any(p.slug == "tcp" for p in out.passages)
    assert any(r.slug == "tcp" for r in out.refs)


async def test_get_article_by_slug_and_id(db_session):
    dom, tcp, _udp, _emb = await _seed(db_session)
    by_slug = await mcp_tools.get_article(db_session, domain_id=dom.id, ref="tcp")
    assert by_slug.slug == "tcp" and by_slug.title == "TCP"
    by_id = await mcp_tools.get_article(db_session, domain_id=dom.id, ref=str(tcp.id))
    assert by_id.id == str(tcp.id)


async def test_get_article_cross_domain_rejected(db_session):
    dom, tcp, _udp, _emb = await _seed(db_session)
    other = await DomainRepo(db_session).create(name="other", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    with pytest.raises(ValueError):
        await mcp_tools.get_article(db_session, domain_id=other.id, ref="tcp")
    with pytest.raises(ValueError):
        await mcp_tools.get_article(db_session, domain_id=other.id, ref=str(tcp.id))


async def test_list_links_returns_typed_edges(db_session):
    dom, tcp, udp, _emb = await _seed(db_session)
    out = await mcp_tools.list_links(db_session, domain_id=dom.id, ref="tcp")
    assert out.article_id == str(tcp.id)
    assert any(e.type == "related" and e.slug == "udp" for e in out.outgoing)
    back = await mcp_tools.list_links(db_session, domain_id=dom.id, ref="udp")
    assert any(e.type == "related" and e.slug == "tcp" for e in back.backlinks)
