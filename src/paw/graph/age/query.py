from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from paw.graph.age.cypher import as_uuid_list, run_cypher
from paw.graph.age.naming import graph_name
from paw.providers.config import GraphConfig


@dataclass(frozen=True)
class Neighbor:
    article_id: uuid.UUID
    shared: int
    via: list[str]


# Entity-bridge (GraphRAG core): seed chunks -> shared entities -> other articles' chunks.
# AGE 1.5.0 note: ORDER BY must repeat the aggregate expression, not its alias.
# "ORDER BY shared DESC" causes UndefinedColumnError in AGE 1.5.0 because AGE cannot resolve
# the alias back to its aggregate during ORDER BY planning. We use the expression directly.
# Secondary sort by article_id is omitted to avoid the same alias-resolution issue.
_BRIDGE = (
    "MATCH (c:Chunk)-[:CHUNK_MENTIONS]->(e:Entity)<-[:CHUNK_MENTIONS]-"
    "(c2:Chunk)-[:IN_ARTICLE]->(a:Article) "
    "WHERE c.id IN $seed_ids AND NOT a.id IN $seed_article_ids "
    "RETURN a.id AS article_id, count(DISTINCT e) AS shared, "
    "collect(DISTINCT e.name)[..5] AS via "
    "ORDER BY count(DISTINCT e) DESC LIMIT $k"
)


def _merge_neighbors(
    bridge: Sequence[tuple[Any, ...]],
    links: Sequence[tuple[Any, ...]],
    *,
    max_neighbors: int,
) -> list[Neighbor]:
    merged: dict[uuid.UUID, Neighbor] = {}
    for row in bridge:
        aid = uuid.UUID(str(row[0]))
        merged[aid] = Neighbor(article_id=aid, shared=int(row[1]), via=list(row[2] or []))
    for row in links:
        aid = uuid.UUID(str(row[0]))
        merged.setdefault(aid, Neighbor(article_id=aid, shared=0, via=[]))
    ordered = sorted(merged.values(), key=lambda n: (-n.shared, str(n.article_id)))
    return ordered[:max_neighbors]


async def graph_expand(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    seed_chunk_ids: Sequence[uuid.UUID],
    seed_article_ids: Sequence[uuid.UUID],
    cfg: GraphConfig,
) -> list[Neighbor]:
    if not seed_chunk_ids:
        return []
    g = graph_name(domain_id)
    seed_arts = as_uuid_list(seed_article_ids)
    bridge = await run_cypher(
        session, graph=g, body=_BRIDGE,
        columns="article_id agtype, shared agtype, via agtype",
        params={
            "seed_ids": as_uuid_list(seed_chunk_ids),
            "seed_article_ids": seed_arts,
            "k": cfg.max_neighbors,
        },
    )
    # Link-expand: depth is a validated int -> safe to inline into the fixed body.
    depth = max(1, int(cfg.expand_depth))
    link_body = (
        f"MATCH (s:Article)-[:LINKS*1..{depth}]->(a:Article) "
        "WHERE s.id IN $seed_article_ids AND NOT a.id IN $seed_article_ids "
        "RETURN DISTINCT a.id AS article_id LIMIT $k"
    )
    links = await run_cypher(
        session, graph=g, body=link_body, columns="article_id agtype",
        params={"seed_article_ids": seed_arts, "k": cfg.max_neighbors},
    )
    return _merge_neighbors(bridge, links, max_neighbors=cfg.max_neighbors)
