from paw.db.repos.domains import DomainRepo
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
