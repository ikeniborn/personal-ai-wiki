from tests.stubs import StubChatProvider

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.fix import run_fix_issue
from paw.harness.ops.lint import LintIssue, issue_id
from paw.providers.config import WikiConfig
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


async def test_fix_resolves_broken_ref_with_ai_revision(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[ghost]].", summary="", author_id=None,
    )
    await db_session.commit()

    issue = LintIssue(
        id=issue_id("broken_ref", "intro", "broken wikilink [[ghost]]"),
        kind="broken_ref", target_slug="intro",
        detail="broken wikilink [[ghost]]", fix="remove or correct the [[ghost]] link",
    )
    # stub returns corrected markdown with the broken link removed
    chat = StubChatProvider(
        [StubChatProvider.tool("emit_result", {"markdown": "See the overview.", "summary": ""})]
    )

    ok = await run_fix_issue(
        db_session, domain_id=dom.id, issue=issue, chat=chat,
        cfg=WikiConfig(), author_id=None,
    )
    await db_session.commit()
    assert ok is True

    refreshed = await ArticleRepo(db_session).get(art.id)
    assert refreshed is not None and refreshed.current_rev == 2  # new ai revision
    revs = await ArticleRepo(db_session).list_revisions(art.id)
    assert revs[0].origin == "ai"
    body = (await PostgresStorage(db_session).get(refreshed.storage_ref)).decode()
    assert "ghost" not in body  # the broken ref is gone -> a fresh lint would not re-flag it


async def test_fix_skips_issue_without_target(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await db_session.commit()
    issue = LintIssue(
        id="x", kind="duplicate_entity", target_slug=None,
        detail="duplicate entity names: QUIC, quic", fix="merge (deferred)",
    )
    chat = StubChatProvider([])  # must not be called
    ok = await run_fix_issue(
        db_session, domain_id=dom.id, issue=issue, chat=chat, cfg=WikiConfig(), author_id=None
    )
    assert ok is False
    assert chat.calls == []
