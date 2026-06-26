from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.managed import (
    ensure_query_cache_embedding_column,
    query_cache_embedding_dim,
)
from paw.db.repos.domains import DomainRepo
from paw.db.repos.query_cache import QueryCacheRepo
from paw.harness.retrieve import Passage, Ref
from paw.obs import metrics
from paw.providers.config import QueryCacheConfig
from paw.providers.factory import build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.vector.embed_cache import embed_query_cached

_WS = re.compile(r"\s+")

PROMPT_VERSION = "1"  # bump if the query system prompt changes materially


def normalize_query(q: str) -> str:
    """Lower, trim, collapse internal whitespace — the exact-match key."""
    return _WS.sub(" ", q.strip().lower())


def passes_threshold(distance: float, sim_threshold: float) -> bool:
    """pgvector <=> is cosine distance (1 - similarity); compare similarity."""
    return (1.0 - distance) >= sim_threshold


def dep_article_ids(refs: list[Ref]) -> list[uuid.UUID]:
    """Dependency article ids from the answer's refs, deduped, order-preserving."""
    return list(dict.fromkeys(r.article_id for r in refs))


def ref_to_json(r: Ref) -> dict[str, str]:
    return {"article_id": str(r.article_id), "slug": r.slug, "title": r.title}


def passage_to_json(p: Passage) -> dict[str, object]:
    return {
        "chunk_id": str(p.chunk_id),
        "article_id": str(p.article_id),
        "slug": p.slug,
        "heading_path": p.heading_path,
        "text": p.text,
        "score": p.score,
    }


@dataclass(frozen=True)
class CacheHit:
    id: uuid.UUID
    answer_md: str
    refs: list[dict[str, object]]
    passages: list[dict[str, object]]
    stale: bool


class QueryCacheService:
    def __init__(self, session: AsyncSession, *, fernet_key: str | None = None) -> None:
        self._s = session
        self._box = SecretBox(fernet_key or get_settings().fernet_key)
        self._redis: object | None = None
        self._repo = QueryCacheRepo(session)
        self._embed_memo: dict[str, tuple[list[float], int]] = {}

    def with_redis(self, redis: object | None) -> QueryCacheService:
        self._redis = redis
        return self

    async def config(self, domain_id: uuid.UUID) -> QueryCacheConfig:
        psvc = ProviderSettingsService(self._s, box=self._box)
        glob = await psvc.get_query_cache()
        dom = await DomainRepo(self._s).get(domain_id)
        overrides = (
            dom.config.get("query_cache") if dom is not None and isinstance(dom.config, dict)
            else None
        )
        if isinstance(overrides, dict):
            return QueryCacheConfig.model_validate({**glob.model_dump(), **overrides})
        return glob

    async def _embed(self, *, question: str) -> tuple[list[float], int] | None:
        """Return (query_vector, embedding_dim) or None if no provider is configured.

        Memoized per question for the service's lifetime: a cache miss both looks up
        (ANN arm) and then upserts, so without this the same question is embedded twice
        per miss (M1). One service instance is request-scoped, so the memo is safe.
        """
        if question in self._embed_memo:
            return self._embed_memo[question]
        psvc = ProviderSettingsService(self._s, box=self._box)
        pc = await psvc.get_provider()
        if pc is None:
            return None
        embedder = build_embedding_provider(pc, self._box)
        vec = await embed_query_cached(
            self._redis, embedder, query=question, model=pc.embedding_model,
            embedding_version=await psvc.get_embedding_version(),
        )
        result = (vec, pc.embedding_dim)
        self._embed_memo[question] = result
        return result

    async def _lookup_impl(
        self, *, domain_id: uuid.UUID, question: str, cfg: QueryCacheConfig
    ) -> CacheHit | None:
        norm = normalize_query(question)
        exact = await self._repo.get_by_norm(domain_id=domain_id, query_norm=norm)
        if exact is not None:
            return CacheHit(exact.id, exact.answer_md, exact.refs, exact.passages, exact.stale)
        if await query_cache_embedding_dim(self._s) is None:
            return None  # no ANN column yet -> exact-only
        embedded = await self._embed(question=question)
        if embedded is None:
            return None
        vec, _dim = embedded
        near = await self._repo.ann_nearest(domain_id=domain_id, query_vector=vec)
        if near is None:
            return None
        row, dist = near
        if not passes_threshold(dist, cfg.sim_threshold):
            return None
        return CacheHit(row.id, row.answer_md, row.refs, row.passages, row.stale)

    async def lookup(
        self, *, domain_id: uuid.UUID, question: str, cfg: QueryCacheConfig
    ) -> CacheHit | None:
        result = await self._lookup_impl(domain_id=domain_id, question=question, cfg=cfg)
        metrics.CACHE_HITS.labels(result="hit" if result is not None else "miss").inc()
        return result

    async def touch(self, cache_id: uuid.UUID) -> None:
        await self._repo.touch(cache_id=cache_id)
        await self._s.commit()

    async def upsert(
        self,
        *,
        domain_id: uuid.UUID,
        question: str,
        answer_md: str,
        refs: list[Ref],
        passages: list[Passage],
        model: str,
    ) -> None:
        embedded = await self._embed(question=question)
        if embedded is None:
            raise ProblemError(status=422, title="Provider not configured")
        vec, dim = embedded
        await ensure_query_cache_embedding_column(self._s, dim)
        refs_json = cast("list[dict[str, object]]", [ref_to_json(r) for r in refs])
        cache_id = await self._repo.upsert(
            domain_id=domain_id,
            query_norm=normalize_query(question),
            answer_md=answer_md,
            refs=refs_json,
            passages=[passage_to_json(p) for p in passages],
            model=model,
            prompt_version=PROMPT_VERSION,
            query_vector=vec,
        )
        ids = dep_article_ids(refs)
        deps: list[tuple[uuid.UUID, int]] = []
        if ids:
            res = await self._s.execute(
                text("SELECT id, current_rev FROM articles WHERE id = ANY(:ids)"),
                {"ids": [str(i) for i in ids]},
            )
            rev_of = {uuid.UUID(str(r[0])): int(r[1]) for r in res.all()}
            deps = [(aid, rev_of[aid]) for aid in ids if aid in rev_of]
        await self._repo.set_deps(cache_id=cache_id, deps=deps)
        await self._s.commit()

    async def suggest(
        self, *, domain_id: uuid.UUID, q: str, top_k: int
    ) -> list[str]:
        norm = normalize_query(q)
        if not norm:
            return []
        return await self._repo.suggest(domain_id=domain_id, q=norm, limit=top_k)
