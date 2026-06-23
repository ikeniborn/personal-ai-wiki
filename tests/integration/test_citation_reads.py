from paw.db.repos.articles import ArticleRepo
from paw.db.repos.citations import CitationRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo


async def test_list_for_article_outer_joins_source(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a", title="A", storage_ref="b:a"
    )
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref="b:s", filename="rfc793.txt", type="md", checksum="x"
    )
    repo = CitationRepo(db_session)
    await repo.create(article_id=art.id, source_id=src.id, quote="reliable", locator="p1")
    await repo.create(article_id=art.id, source_id=None, quote="no-source", locator=None)
    await db_session.commit()

    views = await repo.list_for_article(art.id)
    by_quote = {v.quote: v for v in views}
    assert by_quote["reliable"].source_filename == "rfc793.txt"
    assert by_quote["no-source"].source_id is None
    assert by_quote["no-source"].source_filename is None
