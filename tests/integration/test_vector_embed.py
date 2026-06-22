from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.domains import DomainRepo
from paw.ingest.chunking import ChunkSpec
from paw.vector.embed import embed_and_write


async def test_embed_and_write_persists_vectors(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a", title="A", storage_ref="blob:1"
    )
    await ensure_embedding_column(db_session, 8)
    await db_session.commit()
    specs = [
        ChunkSpec(kind="summary", ord=0, heading_path=None, text="summary text"),
        ChunkSpec(kind="section", ord=1, heading_path="A", text="section text"),
    ]
    ids = await embed_and_write(
        db_session,
        article_id=art.id,
        domain_id=dom.id,
        specs=specs,
        embedder=StubEmbeddingProvider(dim=8),
    )
    await db_session.commit()
    assert len(ids) == 2
    assert await ChunkRepo(db_session).count_for_article(art.id) == 2
    # all rows have a non-null embedding
    from sqlalchemy import text

    row = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE article_id=:a AND embedding IS NOT NULL"),
        {"a": str(art.id)},
    )
    assert row.scalar_one() == 2
