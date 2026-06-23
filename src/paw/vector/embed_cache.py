from __future__ import annotations

import hashlib
import json
from typing import Any

from paw.providers.base import EmbeddingProvider

_TTL_SECONDS = 3600


def _key(query: str, model: str, embedding_version: int) -> str:
    h = hashlib.sha256(f"{model}:{embedding_version}:{query}".encode()).hexdigest()
    return f"paw:qembed:{h}"


async def embed_query_cached(
    redis: Any | None,
    embedder: EmbeddingProvider,
    *,
    query: str,
    model: str,
    embedding_version: int,
) -> list[float]:
    """Return the query embedding, served from Redis when present.

    `redis` is a decode_responses=True client (or None to bypass the cache).
    Distinct from the Phase 7 answer cache.
    """
    if redis is None:
        return (await embedder.embed([query]))[0]
    key = _key(query, model, embedding_version)
    cached = await redis.get(key)
    if cached:
        return [float(x) for x in json.loads(cached)]
    vec = (await embedder.embed([query]))[0]
    await redis.set(key, json.dumps(vec), ex=_TTL_SECONDS)
    return vec
