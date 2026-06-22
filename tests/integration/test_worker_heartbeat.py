from paw.worker import heartbeat


async def test_heartbeat_writes_marker(redis_client):
    ctx = {"redis": redis_client}
    out = await heartbeat(ctx)
    assert out == "ok"
    assert await redis_client.get("paw:worker:heartbeat") is not None
