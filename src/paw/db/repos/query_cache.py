from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.vector.search import _vector_literal


@dataclass(frozen=True)
class CacheRow:
    id: uuid.UUID
    query_norm: str
    answer_md: str
    refs: list[dict[str, object]]
    passages: list[dict[str, object]]
    stale: bool
    hit_count: int
    last_hit_at: datetime | None


def _row(r: Row[Any]) -> CacheRow:
    # r: (id, query_norm, answer_md, refs::text, passages::text, stale, hit_count, last_hit_at)
    return CacheRow(
        id=uuid.UUID(str(r[0])),
        query_norm=r[1],
        answer_md=r[2],
        refs=json.loads(r[3]),
        passages=json.loads(r[4]),
        stale=bool(r[5]),
        hit_count=int(r[6]),
        last_hit_at=r[7],
    )


_SELECT = (
    "id, query_norm, answer_md, refs::text, passages::text, stale, hit_count, last_hit_at"
)


class QueryCacheRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_norm(
        self, *, domain_id: uuid.UUID, query_norm: str
    ) -> CacheRow | None:
        res = await self._s.execute(
            text(
                f"SELECT {_SELECT} FROM query_cache "
                "WHERE domain_id = :d AND query_norm = :n"
            ),
            {"d": str(domain_id), "n": query_norm},
        )
        row = res.first()
        return _row(row) if row else None

    async def ann_nearest(
        self, *, domain_id: uuid.UUID, query_vector: list[float]
    ) -> tuple[CacheRow, float] | None:
        res = await self._s.execute(
            text(
                f"SELECT {_SELECT}, (query_embedding <=> CAST(:q AS vector)) AS dist "
                "FROM query_cache "
                "WHERE domain_id = :d AND query_embedding IS NOT NULL "
                "ORDER BY query_embedding <=> CAST(:q AS vector) LIMIT 1"
            ),
            {"d": str(domain_id), "q": _vector_literal(query_vector)},
        )
        row = res.first()
        if row is None:
            return None
        return _row(row), float(row[8])

    async def upsert(
        self,
        *,
        domain_id: uuid.UUID,
        query_norm: str,
        answer_md: str,
        refs: list[dict[str, object]],
        passages: list[dict[str, object]],
        model: str | None,
        prompt_version: str,
        query_vector: list[float],
    ) -> uuid.UUID:
        res = await self._s.execute(
            text(
                "INSERT INTO query_cache "
                "(domain_id, query_norm, answer_md, refs, passages, model, prompt_version, "
                " stale, hit_count, last_hit_at) "
                "VALUES (:d, :n, :a, CAST(:refs AS jsonb), CAST(:ps AS jsonb), :m, :pv, "
                " false, 0, now()) "
                "ON CONFLICT (domain_id, query_norm) DO UPDATE SET "
                " answer_md = EXCLUDED.answer_md, refs = EXCLUDED.refs, "
                " passages = EXCLUDED.passages, model = EXCLUDED.model, "
                " prompt_version = EXCLUDED.prompt_version, stale = false, "
                " last_hit_at = now() "
                "RETURNING id"
            ),
            {
                "d": str(domain_id),
                "n": query_norm,
                "a": answer_md,
                "refs": json.dumps(refs),
                "ps": json.dumps(passages),
                "m": model,
                "pv": prompt_version,
            },
        )
        cid = uuid.UUID(str(res.scalar_one()))
        await self._s.execute(
            text("UPDATE query_cache SET query_embedding = CAST(:v AS vector) WHERE id = :i"),
            {"v": _vector_literal(query_vector), "i": str(cid)},
        )
        await self._s.flush()
        return cid

    async def set_deps(
        self, *, cache_id: uuid.UUID, deps: list[tuple[uuid.UUID, int]]
    ) -> None:
        await self._s.execute(
            text("DELETE FROM query_cache_articles WHERE cache_id = :c"),
            {"c": str(cache_id)},
        )
        for article_id, rev in deps:
            await self._s.execute(
                text(
                    "INSERT INTO query_cache_articles (cache_id, article_id, rev) "
                    "VALUES (:c, :a, :r)"
                ),
                {"c": str(cache_id), "a": str(article_id), "r": rev},
            )
        await self._s.flush()

    async def touch(self, *, cache_id: uuid.UUID) -> None:
        await self._s.execute(
            text(
                "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = now() "
                "WHERE id = :i"
            ),
            {"i": str(cache_id)},
        )
        await self._s.flush()

    async def mark_stale_for_articles(
        self, *, domain_id: uuid.UUID, article_ids: list[uuid.UUID]
    ) -> int:
        if not article_ids:
            return 0
        res = cast(
            "CursorResult[Any]",
            await self._s.execute(
                text(
                    "UPDATE query_cache SET stale = true "
                    "WHERE domain_id = :d AND id IN ("
                    "  SELECT cache_id FROM query_cache_articles WHERE article_id = ANY(:aids))"
                ),
                {"d": str(domain_id), "aids": [str(a) for a in article_ids]},
            ),
        )
        await self._s.flush()
        return res.rowcount or 0

    async def suggest(
        self, *, domain_id: uuid.UUID, q: str, limit: int
    ) -> list[str]:
        res = await self._s.execute(
            text(
                "SELECT query_norm FROM query_cache "
                "WHERE domain_id = :d AND query_norm ILIKE :pat "
                "ORDER BY hit_count DESC, query_norm ASC LIMIT :k"
            ),
            {"d": str(domain_id), "pat": f"{q}%", "k": limit},
        )
        return [r[0] for r in res.all()]

    async def delete_expired(self, *, cutoff: datetime) -> int:
        res = cast(
            "CursorResult[Any]",
            await self._s.execute(
                text("DELETE FROM query_cache WHERE COALESCE(last_hit_at, created_at) < :c"),
                {"c": cutoff},
            ),
        )
        await self._s.flush()
        return res.rowcount or 0
