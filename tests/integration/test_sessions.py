from paw.security.sessions import SessionStore


async def test_session_lifecycle(redis_client):
    store = SessionStore(redis_client, ttl_seconds=60)
    sid = await store.create("11111111-1111-1111-1111-111111111111")
    assert sid
    assert await store.get(sid) == "11111111-1111-1111-1111-111111111111"
    await store.delete(sid)
    assert await store.get(sid) is None


async def test_unknown_session_is_none(redis_client):
    store = SessionStore(redis_client, ttl_seconds=60)
    assert await store.get("nope") is None
