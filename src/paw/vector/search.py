from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.managed import embedding_dim
from paw.db.repos.chunks import ChunkRepo
from paw.db.repos.entities import EntityRepo
from paw.providers.config import RetrievalConfig

CURRENT_EMBEDDING_VERSION = 1  # static in Phase 3; reindex/versioning lands in Phase 6


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    article_id: uuid.UUID
    score: float


def _vector_literal(vec: list[float]) -> str:
    parts: list[str] = []
    for x in vec:
        f = float(x)
        if not math.isfinite(f):
            raise ValueError(f"query embedding contains non-finite value: {f!r}")
        parts.append(repr(f))
    return "[" + ",".join(parts) + "]"


def rrf_merge(
    ranked_lists: list[tuple[list[uuid.UUID], float]], *, rrf_k: int
) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal Rank Fusion.

    Each input is (ids in rank order, weight); rank is 1-based.
    score(id) = Σ weight_i / (rrf_k + rank_i). Ties broken by id string for
    determinism. Returns [(id, score)] sorted by score desc.
    """
    scores: dict[uuid.UUID, float] = {}
    for ids, weight in ranked_lists:
        for rank, cid in enumerate(ids, start=1):
            scores[cid] = scores.get(cid, 0.0) + weight / (rrf_k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))


async def vector_arm(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    query_vector: list[float],
    embedding_version: int,
    limit: int,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    res = await session.execute(
        text(
            "SELECT c.id, c.article_id "
            "FROM chunks c JOIN articles a ON a.id = c.article_id "
            "WHERE a.domain_id = :dom AND c.embedding_version = :ver "
            "ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k"
        ),
        {
            "dom": str(domain_id),
            "ver": embedding_version,
            "q": _vector_literal(query_vector),
            "k": limit,
        },
    )
    return [(uuid.UUID(str(r[0])), uuid.UUID(str(r[1]))) for r in res.all()]


async def fts_arm(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    query: str,
    regconfig: str,
    limit: int,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    res = await session.execute(
        text(
            "SELECT c.id, c.article_id "
            "FROM chunks c JOIN articles a ON a.id = c.article_id, "
            "     websearch_to_tsquery(CAST(:cfg AS regconfig), :q) q "
            "WHERE a.domain_id = :dom AND c.tsv @@ q "
            "ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT :k"
        ),
        {"cfg": regconfig, "q": query, "dom": str(domain_id), "k": limit},
    )
    return [(uuid.UUID(str(r[0])), uuid.UUID(str(r[1]))) for r in res.all()]


def match_entity_names(names: list[str], query: str) -> list[str]:
    q = query.lower()
    return [n for n in names if n.lower() in q]


async def query_entities(
    session: AsyncSession, *, domain_id: uuid.UUID, query: str
) -> list[uuid.UUID]:
    ents = await EntityRepo(session).list_by_domain(domain_id)
    matched = set(match_entity_names([e.name for e in ents], query))
    return [e.id for e in ents if e.name in matched]


async def hybrid_search(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    query: str,
    query_vector: list[float],
    cfg: RetrievalConfig,
    embedding_version: int = CURRENT_EMBEDDING_VERSION,
    boost_entity_ids: list[uuid.UUID] | None = None,
) -> list[Hit]:
    arms: list[tuple[list[uuid.UUID], float]] = []
    article_of: dict[uuid.UUID, uuid.UUID] = {}
    # vector arm only if the managed embedding column exists (skips empty corpora)
    if await embedding_dim(session) is not None:
        vec = await vector_arm(
            session,
            domain_id=domain_id,
            query_vector=query_vector,
            embedding_version=embedding_version,
            limit=cfg.k1,
        )
        arms.append(([cid for cid, _ in vec], cfg.vector_weight))
        article_of.update(dict(vec))
    fts = await fts_arm(
        session, domain_id=domain_id, query=query, regconfig=cfg.fts_regconfig, limit=cfg.k2
    )
    arms.append(([cid for cid, _ in fts], cfg.fts_weight))
    article_of.update(dict(fts))

    fused = rrf_merge(arms, rrf_k=cfg.rrf_k)
    if boost_entity_ids:
        tagged = await ChunkRepo(session).tagged_with(
            chunk_ids=[c for c, _ in fused], entity_ids=boost_entity_ids
        )
        fused = [(c, s + (cfg.entity_boost if c in tagged else 0.0)) for c, s in fused]
        fused.sort(key=lambda kv: (-kv[1], str(kv[0])))
    return [Hit(chunk_id=c, article_id=article_of[c], score=s) for c, s in fused[: cfg.top_n]]
