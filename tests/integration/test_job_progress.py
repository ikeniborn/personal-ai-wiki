import asyncio

from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.jobs.progress import channel, publish, sse_events


async def test_channel_and_publish(redis_client):
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel("j1"))
    await publish(redis_client, "j1", {"step": "draft"})
    # drain subscribe ack + message
    msg = None
    for _ in range(5):
        m = await pubsub.get_message(timeout=1.0)
        if m and m["type"] == "message":
            msg = m
            break
    assert msg is not None and b"draft" in (
        msg["data"] if isinstance(msg["data"], bytes) else msg["data"].encode()
    )
    await pubsub.unsubscribe(channel("j1"))


async def test_sse_replays_log_for_terminal_job(db_session, redis_client):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.append_log(job.id, {"step": "extract"})
    await repo.append_log(job.id, {"step": "done", "status": "succeeded"})
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()
    frames = [frame async for frame in sse_events(redis_client, repo, job.id)]
    body = "".join(frames)
    assert "extract" in body
    assert "succeeded" in body
    assert all(f.startswith("data: ") and f.endswith("\n\n") for f in frames)


async def test_sse_live_tail_subscribe_then_publish(db_session, redis_client):
    """Live-tail path: non-terminal job → replay buffered log → receive live event → terminate."""
    dom = await DomainRepo(db_session).create(name="d2", source_prefix="s2", wiki_prefix="w2")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.append_log(job.id, {"step": "extract"})
    # Job stays non-terminal (status remains "queued") so sse_events enters the subscribe loop.
    await db_session.commit()

    async def _consume():
        return [f async for f in sse_events(redis_client, repo, job.id)]

    task = asyncio.create_task(_consume())

    # Poll until sse_events has subscribed to avoid the pub/sub race.
    ch = channel(job.id)
    for _ in range(100):
        subs = await redis_client.pubsub_numsub(ch)  # list[(channel, count)]
        if subs and subs[0][1] >= 1:
            break
        await asyncio.sleep(0.02)

    await publish(redis_client, job.id, {"step": "done", "status": "succeeded"})

    frames = await asyncio.wait_for(task, timeout=5)

    body = "".join(frames)
    assert "extract" in body
    assert "succeeded" in body
    assert all(f.startswith("data: ") and f.endswith("\n\n") for f in frames)


async def test_sse_emits_keepalive_on_idle(db_session, redis_client):
    """An idle live-tail (no event before idle_timeout) yields an SSE keep-alive comment."""
    dom = await DomainRepo(db_session).create(name="d4", source_prefix="s4", wiki_prefix="w4")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")  # non-terminal, empty log
    await db_session.commit()
    gen = sse_events(redis_client, repo, job.id, idle_timeout=0.05)
    try:
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert frame == ": keep-alive\n\n"
        assert not frame.startswith("data: ")  # comment line, ignored by EventSource clients
    finally:
        await gen.aclose()
