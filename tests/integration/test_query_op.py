from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.query import DONT_KNOW, build_messages, to_answer
from paw.harness.retrieve import retrieve
from paw.ingest.chunking import ChunkSpec
from paw.providers.config import RetrievalConfig, WikiConfig
from paw.vector.embed import embed_and_write


async def _ctx_with_corpus(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:a", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    emb = StubEmbeddingProvider(dim=8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="section", ord=1, heading_path="R", text="TCP reliable delivery")],
        embedder=emb,
    )
    await db_session.commit()
    return dom, emb


async def test_messages_carry_context_and_question(db_session):
    dom, emb = await _ctx_with_corpus(db_session)
    ctx = await retrieve(
        db_session, domain_id=dom.id, query="reliable", embedder=emb,
        cfg=RetrievalConfig(k1=10, k2=10, top_n=5), embed_model="m",
    )
    msgs = build_messages("reliable?", ctx, WikiConfig())
    assert msgs[0].role == "system" and "ONLY" in msgs[0].content
    assert "reliable?" in msgs[1].content and "<<CONTEXT" in msgs[1].content


async def test_to_answer_maps_refs_passages(db_session):
    dom, emb = await _ctx_with_corpus(db_session)
    ctx = await retrieve(
        db_session, domain_id=dom.id, query="reliable", embedder=emb,
        cfg=RetrievalConfig(k1=10, k2=10, top_n=5), embed_model="m",
    )
    ans = to_answer("the answer [tcp]", ctx)
    assert ans.answer_md == "the answer [tcp]"
    assert any(r.slug == "tcp" for r in ans.refs)
    assert ans.passages == ctx.passages


def test_dont_know_constant():
    assert "don't" in DONT_KNOW.lower() or "do not" in DONT_KNOW.lower()
