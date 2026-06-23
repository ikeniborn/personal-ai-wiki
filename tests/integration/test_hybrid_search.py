from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo
from paw.ingest.chunking import ChunkSpec
from paw.vector.embed import embed_and_write


async def _seed(db_session, dim=8):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:1", summary="sum"
    )
    await ensure_embedding_column(db_session, dim)
    specs = [
        ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP summary"),
        ChunkSpec(kind="section", ord=1, heading_path="Reliability", text="TCP is reliable"),
    ]
    ids = await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id, specs=specs,
        embedder=StubEmbeddingProvider(dim=dim),
    )
    await db_session.commit()
    return dom, art, ids


async def test_repo_reads(db_session):
    dom, art, ids = await _seed(db_session)
    repo = ChunkRepo(db_session)
    passages = await repo.fetch_passages(ids)
    assert {p.chunk_id for p in passages} == set(ids)
    assert any(p.slug == "tcp" and p.title == "TCP" for p in passages)
    summaries = await repo.fetch_summaries([art.id])
    assert summaries[0].text == "TCP summary" and summaries[0].slug == "tcp"
    # entity tagging + tagged_with
    e = await EntityRepo(db_session).upsert(domain_id=dom.id, name="TCP")
    await repo.tag_entity(chunk_id=ids[1], entity_id=e.id)
    await db_session.commit()
    tagged = await repo.tagged_with(chunk_ids=ids, entity_ids=[e.id])
    assert tagged == {ids[1]}
    assert [en.name for en in await EntityRepo(db_session).list_by_domain(dom.id)] == ["TCP"]
