from __future__ import annotations

from datetime import UTC, datetime

from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.lint import run_lint
from paw.providers.config import MaintenanceConfig, WikiConfig
from paw.services.ingest_write import upsert_article


async def test_plant_lint_fix_lint_clean(db_session, redis_client, wired_settings, monkeypatch):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    # plant a broken [[ref]]
    await upsert_article(
        db_session, domain_id=dom.id, slug="intro", title="Intro",
        markdown="Welcome. See [[ghost]].", summary="", author_id=None,
    )
    await db_session.commit()

    # 1) Lint reports the broken ref (run the deterministic op directly)
    issues = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    broken = next(i for i in issues if i.kind == "broken_ref")

    # 2) Fix the selected issue via the job (stub LLM removes the broken link)
    async def fake_build(session, box):
        chat = StubChatProvider(
            [StubChatProvider.tool(
                "emit_result", {"markdown": "Welcome to the overview.", "summary": ""}
            )]
        )
        return chat, StubEmbeddingProvider(dim=8), WikiConfig(), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    fix_job = await JobRepo(db_session).create(domain_id=dom.id, kind="fix")
    await db_session.commit()
    out = await tasks_mod.fix_issues(
        {"redis": redis_client}, str(fix_job.id), str(dom.id), [broken.id]
    )
    assert out == "succeeded"

    # 3) Re-run Lint — the broken ref is gone
    after = (
        await run_lint(
            db_session, domain_id=dom.id, cfg=MaintenanceConfig(), now=datetime.now(UTC)
        )
    ).issues
    assert broken.id not in {i.id for i in after}
