from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo


async def _domain(db_session):
    return await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")


async def test_job_lifecycle_and_log(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    assert job.status == "queued"
    await repo.set_status(job.id, "running")
    await repo.append_log(job.id, {"step": "draft", "msg": "started"})
    await repo.append_log(job.id, {"step": "write", "msg": "done"})
    await repo.set_status(job.id, "succeeded", article_id=None)
    await db_session.commit()
    got = await repo.get(job.id)
    assert got is not None
    assert got.status == "succeeded"
    assert got.started_at is not None and got.finished_at is not None
    assert len(got.log) == 2


async def test_cancel_flag(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await db_session.commit()
    assert await repo.is_cancel_requested(job.id) is False
    await repo.request_cancel(job.id)
    await db_session.commit()
    assert await repo.is_cancel_requested(job.id) is True


async def test_reconcile_marks_stale_running_failed(db_session):
    dom = await _domain(db_session)
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.set_status(job.id, "running")
    await db_session.commit()
    # heartbeat_at is NULL right after set_status -> treated as stale
    n = await repo.reconcile_stuck(older_than_seconds=0)
    await db_session.commit()
    assert n == 1
    got = await repo.get(job.id)
    assert got is not None and got.status == "failed"
