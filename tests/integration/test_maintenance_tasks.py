from __future__ import annotations

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.lint import issue_id
from paw.services.ingest_write import upsert_article


async def _seed_lintable(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="See [[ghost]].", summary="", author_id=None,
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="lint")
    await db_session.commit()
    return dom, job


async def test_lint_task_records_issues_and_writes_nothing(
    db_session, redis_client, wired_settings
):
    dom, job = await _seed_lintable(db_session)
    out = await tasks_mod.lint_domain({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    got = await JobRepo(db_session).get(job.id)
    assert got is not None and got.status == "succeeded"
    issues_entry = next(e for e in got.log if e.get("step") == "issues")
    ids = {i["id"] for i in issues_entry["issues"]}
    assert issue_id("broken_ref", "intro", "ghost") in ids


async def test_fix_task_resolves_selected_issue(
    db_session, redis_client, wired_settings, monkeypatch
):
    from datetime import UTC, datetime

    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.harness.ops.lint import run_lint
    from paw.providers.config import MaintenanceConfig, WikiConfig

    dom, _ = await _seed_lintable(db_session)  # 'intro' with a broken [[ghost]]
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="fix")
    await db_session.commit()

    issues = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(),
            now=datetime.now(UTC),
        )
    ).issues
    broken = next(i for i in issues if i.kind == "broken_ref")

    async def fake_build(session, box):
        chat = StubChatProvider(
            [StubChatProvider.tool("emit_result", {"markdown": "Clean body.", "summary": ""})]
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.fix_issues(
        {"redis": redis_client}, str(job.id), str(dom.id), [broken.id]
    )
    assert out == "succeeded"
    # a fresh lint no longer reports the broken ref
    after = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    assert broken.id not in {i.id for i in after}


async def test_reindex_task_flips_stale_chunks(
    db_session, redis_client, wired_settings, monkeypatch
):
    from sqlalchemy import text
    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.config import get_settings
    from paw.db.managed import ensure_embedding_column
    from paw.db.repos.articles import ArticleRepo
    from paw.ingest.chunking import ChunkSpec
    from paw.providers.config import WikiConfig
    from paw.security.secrets import SecretBox
    from paw.services.provider_settings import ProviderSettingsService
    from paw.vector.embed import embed_and_write

    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    art = await ArticleRepo(db_session).create(
        domain_id=dom.id, slug="tcp", title="TCP", storage_ref="b:1", summary="s"
    )
    await ensure_embedding_column(db_session, 8)
    await embed_and_write(
        db_session, article_id=art.id, domain_id=dom.id,
        specs=[ChunkSpec(kind="summary", ord=0, heading_path=None, text="TCP")],
        embedder=StubEmbeddingProvider(dim=8), embedding_version=1,
    )
    # bump current version to 2 -> the v1 chunk is now stale
    box = SecretBox(get_settings().fernet_key)
    await ProviderSettingsService(db_session, box=box).bump_embedding_version()
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="reindex")
    await db_session.commit()

    async def fake_build(session, box):
        return StubChatProvider([]), StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.reindex_domain({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    rows = await db_session.execute(
        text("SELECT DISTINCT embedding_version FROM chunks WHERE domain_id = :d"),
        {"d": str(dom.id)},
    )
    assert [r[0] for r in rows.all()] == [2]


async def test_format_task_revises_articles(db_session, redis_client, wired_settings, monkeypatch):
    from tests.stubs import StubChatProvider, StubEmbeddingProvider

    from paw.db.repos.articles import ArticleRepo
    from paw.providers.config import WikiConfig
    from paw.services.ingest_write import upsert_article

    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    art, _ = await upsert_article(
        db_session, domain_id=dom.id, slug="quic", title="QUIC",
        markdown="QUIC over UDP.", summary="", author_id=None,
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="format")
    await db_session.commit()

    async def fake_build(session, box):
        chat = StubChatProvider(
            responder=lambda msgs, tools: StubChatProvider.tool(
                "emit_result", {"markdown": "## Overview\n\nQUIC over UDP."}
            )
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.format_articles({"redis": redis_client}, str(job.id), str(dom.id))
    assert out == "succeeded"
    art_id = art.id
    db_session.expire_all()
    refreshed = await ArticleRepo(db_session).get(art_id)
    assert refreshed is not None and refreshed.current_rev == 2
