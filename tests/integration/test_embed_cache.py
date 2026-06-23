from tests.stubs import StubEmbeddingProvider

from paw.vector.embed_cache import embed_query_cached


class _CountingEmbedder(StubEmbeddingProvider):
    def __init__(self, dim=8):
        super().__init__(dim=dim)
        self.calls = 0

    async def embed(self, texts, *, model=None):
        self.calls += 1
        return await super().embed(texts, model=model)


async def test_caches_query_vector(redis_client):
    emb = _CountingEmbedder(dim=8)
    v1 = await embed_query_cached(
        redis_client, emb, query="hello", model="m", embedding_version=1
    )
    v2 = await embed_query_cached(
        redis_client, emb, query="hello", model="m", embedding_version=1
    )
    assert v1 == v2
    assert emb.calls == 1  # second call served from Redis
    assert len(v1) == 8


async def test_none_redis_skips_cache(db_session):
    emb = _CountingEmbedder(dim=8)
    v = await embed_query_cached(None, emb, query="hi", model="m", embedding_version=1)
    assert len(v) == 8 and emb.calls == 1
