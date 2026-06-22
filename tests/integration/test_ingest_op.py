from sqlalchemy import text
from tests.stubs import StubChatProvider, StubEmbeddingProvider

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.ingest import run_ingest
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article


async def test_upsert_article_creates_then_merges(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art, created = await upsert_article(
        db_session,
        domain_id=dom.id,
        slug="quic",
        title="QUIC",
        markdown="# QUIC",
        summary="s",
        author_id=None,
    )
    await db_session.commit()
    assert created is True and art.current_rev == 1
    art2, created2 = await upsert_article(
        db_session,
        domain_id=dom.id,
        slug="quic",
        title="QUIC v2",
        markdown="# QUIC v2",
        summary="s2",
        author_id=None,
    )
    await db_session.commit()
    assert created2 is False
    assert art2.id == art.id and art2.current_rev == 2


def _ingest_chat() -> StubChatProvider:
    # A: extraction, B: drafting — scripted tool-call results in order.
    extraction = StubChatProvider.tool(
        "emit_result", {"entities": ["QUIC", "UDP"], "key_points": ["fast transport"]}
    )
    draft = StubChatProvider.tool(
        "emit_result",
        {
            "slug": "quic",
            "title": "QUIC",
            "summary": "QUIC is a transport protocol.",
            "markdown": "## Overview\n\nQUIC runs over UDP. It is fast. It reduces latency.",
            "entities": ["QUIC", "UDP"],
            "citations": [{"quote": "QUIC runs over UDP", "locator": "p1"}],
        },
    )
    return StubChatProvider([extraction, draft])


async def test_run_ingest_writes_full_article(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    res = await run_ingest(
        db_session,
        domain_id=dom.id,
        source_md="QUIC is a transport protocol that runs over UDP.",
        chat=_ingest_chat(),
        embedder=StubEmbeddingProvider(dim=8),
        cfg=WikiConfig(chunk_target_size=60),
        dim=8,
    )
    await db_session.commit()
    assert res.entity_count >= 1
    assert res.citation_count >= 1
    assert res.chunk_count >= 1
    # acceptance: an ord=0 summary chunk exists + embeddings present
    summ = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE article_id=:a AND kind='summary' AND ord=0"),
        {"a": str(res.article_id)},
    )
    assert summ.scalar_one() == 1
    emb = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE article_id=:a AND embedding IS NOT NULL"),
        {"a": str(res.article_id)},
    )
    assert emb.scalar_one() == res.chunk_count
    assert await ChunkRepo(db_session).count_for_article(res.article_id) == res.chunk_count
    ce = await db_session.execute(text("SELECT count(*) FROM chunk_entities"))
    assert ce.scalar_one() > 0
    # articles.summary populated
    art = await ArticleRepo(db_session).get(res.article_id)
    assert art is not None and art.summary
