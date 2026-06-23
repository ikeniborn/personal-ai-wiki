from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.graph.repo import GraphRepo
from paw.harness.retrieve import retrieve
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig
from paw.vector.embed import embed_and_write


async def _seed_two(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    a = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    b = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="ip", title="IP", storage_ref="b:b", summary="s"
    )
    await ensure_embedding_column(db_session, dim)
    emb = StubEmbeddingProvider(dim=dim)
    await embed_and_write(
        db_session, article_id=a.id, domain_id=dom.id,
        specs=[
            ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP summary"),
            ChunkSpec(kind="section", ord=1, heading_path="Reliable", text="TCP reliable delivery"),
        ],
        embedder=emb,
    )
    await embed_and_write(
        db_session, article_id=b.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="summary", ord=0, heading_path=None, text="IP addressing summary")],
        embedder=emb,
    )
    await GraphRepo(db_session).link(
        domain_id=dom.id, src_article_id=a.id, dst_article_id=b.id, type="related"
    )
    await db_session.commit()
    return dom, a, b, emb


async def test_retrieve_assembles_seed_and_neighbor(db_session):
    dom, a, b, emb = await _seed_two(db_session)
    cfg = RetrievalConfig(k1=10, k2=10, top_n=5, bfs_depth=1)
    ctx = await retrieve(
        db_session, domain_id=dom.id, query="reliable delivery", embedder=emb,
        cfg=cfg, embedding_version=1, redis=None, embed_model="m",
    )
    assert ctx.passages, "expected seed passages"
    assert any(p.slug == "tcp" for p in ctx.passages)
    # BFS neighbor IP surfaces as a ref via its summary
    assert {r.slug for r in ctx.refs} >= {"tcp", "ip"}
    assert "<<CONTEXT" in ctx.prompt_block and "[seed]" in ctx.prompt_block


async def test_retrieve_empty_on_no_match(db_session):
    dom, a, b, emb = await _seed_two(db_session)
    # use a domain with no chunks to force empty.
    empty = await DomainRepo(db_session).create(name="empty", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    cfg = RetrievalConfig()
    ctx = await retrieve(
        db_session, domain_id=empty.id, query="anything", embedder=emb,
        cfg=cfg, embedding_version=1, redis=None, embed_model="m",
    )
    assert ctx.passages == [] and ctx.refs == []
