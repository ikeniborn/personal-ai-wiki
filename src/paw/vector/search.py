from __future__ import annotations

import math
import uuid
from dataclasses import dataclass


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
