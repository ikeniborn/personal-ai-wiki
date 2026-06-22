import pytest

from paw.db.managed import embedding_dim, ensure_embedding_column


async def test_creates_vector_column_and_index_idempotently(db_session):
    assert await embedding_dim(db_session) is None
    await ensure_embedding_column(db_session, 8)
    await db_session.commit()
    assert await embedding_dim(db_session) == 8
    # idempotent: second call must not raise
    await ensure_embedding_column(db_session, 8)
    await db_session.commit()
    assert await embedding_dim(db_session) == 8


async def test_rejects_non_positive_dim(db_session):
    with pytest.raises(ValueError):
        await ensure_embedding_column(db_session, 0)
