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
