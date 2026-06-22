import pytest

from paw.storage.postgres import PostgresStorage


async def test_blob_roundtrip(db_session):
    store = PostgresStorage(db_session)
    ref = await store.put(b"hello small", content_type="text/plain")
    assert ref.startswith("blob:")
    assert await store.exists(ref) is True
    assert await store.get(ref) == b"hello small"
    await store.delete(ref)
    assert await store.exists(ref) is False


async def test_large_object_roundtrip(db_session):
    store = PostgresStorage(db_session)
    big = b"x" * (2 * 1024 * 1024)
    ref = await store.put(big, content_type="application/octet-stream", large=True)
    assert ref.startswith("lo:")
    chunks = [c async for c in store.open(ref)]
    assert b"".join(chunks) == big
    await store.delete(ref)


async def test_get_missing_raises(db_session):
    store = PostgresStorage(db_session)
    with pytest.raises(KeyError):
        await store.get("blob:00000000-0000-0000-0000-000000000000")
