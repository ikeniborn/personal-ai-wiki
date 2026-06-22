import pytest

from paw.db.managed import ensure_embedding_column
from paw.db.repos.articles import ArticleRepo
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.entities import EntityRepo


async def _seed_article(db_session, slug="a"):
    dom = await DomainRepo(db_session).create(name=f"d-{slug}", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug=slug, title=slug.upper(), storage_ref="blob:x"
    )
    return dom, art


async def test_entity_upsert_is_idempotent(db_session):
    dom, _ = await _seed_article(db_session)
    repo = EntityRepo(db_session)
    e1 = await repo.upsert(domain_id=dom.id, name="QUIC", kind="protocol")
    e2 = await repo.upsert(domain_id=dom.id, name="QUIC")
    await db_session.commit()
    assert e1.id == e2.id


async def test_shared_with_counts(db_session):
    dom, a1 = await _seed_article(db_session, "a1")
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="blob:y"
    )
    repo = EntityRepo(db_session)
    e_quic = await repo.upsert(domain_id=dom.id, name="QUIC")
    e_udp = await repo.upsert(domain_id=dom.id, name="UDP")
    for e in (e_quic, e_udp):
        await repo.tag_article(article_id=a1.id, entity_id=e.id)
        await repo.tag_article(article_id=a2.id, entity_id=e.id)
    await db_session.commit()
    shared = await repo.shared_with(domain_id=dom.id, article_id=a1.id)
    assert shared == [(a2.id, 2)]


async def test_citation_create(db_session):
    _, art = await _seed_article(db_session)
    c = await CitationRepo(db_session).create(
        article_id=art.id, source_id=None, quote="q", locator="p1"
    )
    await db_session.commit()
    assert c.article_id == art.id


async def test_chunk_create_sets_tsv_and_embedding(db_session):
    dom, art = await _seed_article(db_session)
    await ensure_embedding_column(db_session, 4)
    await db_session.commit()
    repo = ChunkRepo(db_session)
    cid = await repo.create(
        article_id=art.id,
        domain_id=dom.id,
        kind="summary",
        ord=0,
        heading_path=None,
        text_body="QUIC is a transport protocol",
    )
    await repo.set_embedding(chunk_id=cid, vector=[0.1, 0.2, 0.3, 0.4])
    await db_session.commit()
    assert await repo.count_for_article(art.id) == 1


async def test_set_embedding_rejects_non_finite(db_session):
    dom, art = await _seed_article(db_session)
    await ensure_embedding_column(db_session, 4)
    await db_session.commit()
    repo = ChunkRepo(db_session)
    cid = await repo.create(
        article_id=art.id,
        domain_id=dom.id,
        kind="summary",
        ord=0,
        heading_path=None,
        text_body="test non-finite embedding",
    )
    await db_session.commit()
    with pytest.raises(ValueError):
        await repo.set_embedding(chunk_id=cid, vector=[0.1, float("inf"), 0.3, 0.4])
