from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.limits import Budget
from paw.harness.tools import ToolContext, run_tool, tools_for
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig, WikiConfig
from paw.vector.embed import embed_and_write


def test_query_allowlist_is_read_only():
    tools = tools_for("query")
    assert set(tools) == {"search_wiki", "get_article", "list_articles"}
    assert all(not t.writes for t in tools.values())


async def test_search_wiki_returns_hits(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable")],
        embedder=emb,
    )
    await db_session.commit()
    ctx = ToolContext(
        session=db_session, domain_id=dom.id, user_id=None,
        budget=Budget.from_wiki(WikiConfig()),
        embedder=emb, retrieval=RetrievalConfig(k1=10, k2=10, top_n=5),
    )
    out = await run_tool(ctx, "search_wiki", {"query": "reliable"})
    assert out["passages"], "expected passages"
    assert any(p["slug"] == "tcp" for p in out["passages"])
    assert {r["slug"] for r in out["refs"]} >= {"tcp"}
