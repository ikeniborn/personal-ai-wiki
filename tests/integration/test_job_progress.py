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
