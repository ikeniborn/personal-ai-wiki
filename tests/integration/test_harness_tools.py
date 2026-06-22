import pytest

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.domains import DomainRepo
from paw.harness.limits import Budget
from paw.harness.tools import ToolContext, run_tool, tools_for
from paw.providers.config import WikiConfig


async def _ctx(db_session):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    return dom, ToolContext(
        session=db_session, domain_id=dom.id, user_id=None, budget=Budget.from_wiki(WikiConfig())
    )


def test_allowlist_ingest():
    names = set(tools_for("ingest"))
    assert {
        "read_source",
        "get_article",
        "list_articles",
        "upsert_article",
        "add_link",
        "report_issue",
    } == names
    with pytest.raises(ValueError):
        tools_for("nonexistent")


async def test_upsert_article_writes_within_scope(db_session):
    dom, ctx = await _ctx(db_session)
    out = await run_tool(
        ctx,
        "upsert_article",
        {"slug": "quic", "title": "QUIC", "markdown": "# QUIC\n\nbody", "summary": "s"},
    )
    await db_session.commit()
    assert out["created"] is True
    arts = await ArticleRepo(db_session).list_by_domain(dom.id)
    assert any(a.slug == "quic" for a in arts)


async def test_add_link_rejects_cross_domain(db_session):
    dom, ctx = await _ctx(db_session)
    a1 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a1", title="A1", storage_ref="blob:1"
    )
    other = await DomainRepo(db_session).create(name="o", source_prefix="s2", wiki_prefix="w2")
    foreign = await ArticleRepo(db_session).create(
        domain_id=other.id, slug="x", title="X", storage_ref="blob:2"
    )
    await db_session.commit()
    with pytest.raises(PermissionError):
        await run_tool(
            ctx, "add_link", {"src_id": str(a1.id), "dst_id": str(foreign.id), "type": "related"}
        )


async def test_add_link_rejects_bad_type(db_session):
    dom, ctx = await _ctx(db_session)
    a1 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a1", title="A1", storage_ref="blob:1"
    )
    a2 = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="a2", title="A2", storage_ref="blob:2"
    )
    await db_session.commit()
    with pytest.raises(ValueError):
        await run_tool(
            ctx, "add_link", {"src_id": str(a1.id), "dst_id": str(a2.id), "type": "NOT_ALLOWED"}
        )
