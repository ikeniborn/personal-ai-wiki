from tests.stubs import StubChatProvider

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.format import run_format_article
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


async def test_format_writes_revision_preserving_facts(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC runs over UDP.", summary="", author_id=None,
    )
    await db_session.commit()

    # stub keeps the facts, changes the formatting
    chat = StubChatProvider(
        [StubChatProvider.tool(
            "emit_result", {"markdown": "## Overview\n\nQUIC runs over UDP.\n"}
        )]
    )
    ok = await run_format_article(
        db_session, domain_id=dom.id, article=art,
        entity_names=["QUIC", "UDP"], citation_quotes=["runs over UDP"],
        chat=chat, cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is True
    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 2
    body = (await PostgresStorage(db_session).get(refreshed.storage_ref)).decode()
    assert "QUIC runs over UDP" in body and body.startswith("## Overview")


async def test_format_rejects_fact_drift(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC runs over UDP.", summary="", author_id=None,
    )
    await db_session.commit()

    # stub drops the 'UDP' fact -> invariant guard must reject the write
    chat = StubChatProvider(
        [StubChatProvider.tool("emit_result", {"markdown": "## Overview\n\nQUIC is fast."})]
    )
    ok = await run_format_article(
        db_session, domain_id=dom.id, article=art,
        entity_names=["QUIC", "UDP"], citation_quotes=[],
        chat=chat, cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is False
    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 1  # unchanged
