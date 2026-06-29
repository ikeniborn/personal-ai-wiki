from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.graph.age.cypher import exec_cypher
from paw.graph.age.naming import graph_name


async def _fetch(session: AsyncSession, sql: str, **params: object) -> list[tuple[object, ...]]:
    res = await session.execute(text(sql), params)
    return [tuple(r) for r in res.all()]


async def project_article(
    session: AsyncSession, *, domain_id: uuid.UUID, article_id: uuid.UUID
) -> None:
    """Mirror one article's relational rows into the domain graph (in-txn, no commit)."""
    g = graph_name(domain_id)
    aid = str(article_id)

    art = await _fetch(
        session, "SELECT slug, title FROM articles WHERE id = :id", id=aid
    )
    if not art:
        return
    slug, title = art[0]

    chunks = await _fetch(
        session,
        "SELECT id::text, ord FROM chunks WHERE article_id = :id ORDER BY ord",
        id=aid,
    )
    ents = await _fetch(
        session,
        "SELECT e.id::text, e.name, COALESCE(e.kind, '') FROM entities e "
        "JOIN article_entities ae ON ae.entity_id = e.id WHERE ae.article_id = :id",
        id=aid,
    )
    chunk_ments = await _fetch(
        session,
        "SELECT ce.chunk_id::text, ce.entity_id::text FROM chunk_entities ce "
        "JOIN chunks c ON c.id = ce.chunk_id WHERE c.article_id = :id",
        id=aid,
    )
    links = await _fetch(
        session,
        "SELECT src_article_id::text, dst_article_id::text, type FROM links "
        "WHERE src_article_id = :id OR dst_article_id = :id",
        id=aid,
    )

    # 1. Article node.
    await exec_cypher(
        session, graph=g,
        body="MERGE (a:Article {id: $id}) SET a.slug = $slug, a.title = $title",
        params={"id": aid, "slug": slug, "title": title},
    )
    # 2. Clear this article's chunks (clean re-projection on edit), then re-merge.
    await exec_cypher(
        session, graph=g, body="MATCH (c:Chunk {article_id: $id}) DETACH DELETE c",
        params={"id": aid},
    )
    if chunks:
        await exec_cypher(
            session, graph=g,
            body=(
                "MATCH (a:Article {id: $aid}) "
                "UNWIND $rows AS r "
                "MERGE (c:Chunk {id: r.id}) SET c.article_id = $aid, c.ord = r.ord "
                "MERGE (c)-[:IN_ARTICLE]->(a)"
            ),
            params={"aid": aid, "rows": [{"id": cid, "ord": ordv} for cid, ordv in chunks]},
        )
    # 3. Entities + Article-MENTIONS-Entity.
    if ents:
        await exec_cypher(
            session, graph=g,
            body=(
                "MATCH (a:Article {id: $aid}) "
                "UNWIND $rows AS r "
                "MERGE (e:Entity {id: r.id}) SET e.name = r.name, e.kind = r.kind "
                "MERGE (a)-[:MENTIONS]->(e)"
            ),
            params={
                "aid": aid,
                "rows": [{"id": eid, "name": n, "kind": k} for eid, n, k in ents],
            },
        )
    # 4. Chunk-CHUNK_MENTIONS-Entity.
    if chunk_ments:
        await exec_cypher(
            session, graph=g,
            body=(
                "UNWIND $rows AS r "
                "MATCH (c:Chunk {id: r.cid}), (e:Entity {id: r.eid}) "
                "MERGE (c)-[:CHUNK_MENTIONS]->(e)"
            ),
            params={"rows": [{"cid": cid, "eid": eid} for cid, eid in chunk_ments]},
        )
    # 5. LINKS (both directions; endpoints merged minimally if absent).
    if links:
        await exec_cypher(
            session, graph=g,
            body=(
                "UNWIND $rows AS r "
                "MERGE (s:Article {id: r.src}) "
                "MERGE (d:Article {id: r.dst}) "
                "MERGE (s)-[l:LINKS {type: r.type}]->(d)"
            ),
            params={"rows": [{"src": s, "dst": d, "type": t} for s, d, t in links]},
        )


async def detach_article(
    session: AsyncSession, *, domain_id: uuid.UUID, article_id: uuid.UUID
) -> None:
    g = graph_name(domain_id)
    await exec_cypher(
        session, graph=g, body="MATCH (c:Chunk {article_id: $id}) DETACH DELETE c",
        params={"id": str(article_id)},
    )
    await exec_cypher(
        session, graph=g, body="MATCH (a:Article {id: $id}) DETACH DELETE a",
        params={"id": str(article_id)},
    )


async def merge_link(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    src_article_id: uuid.UUID,
    dst_article_id: uuid.UUID,
    type: str,
) -> None:
    g = graph_name(domain_id)
    await exec_cypher(
        session, graph=g,
        body=(
            "MERGE (s:Article {id: $src}) MERGE (d:Article {id: $dst}) "
            "MERGE (s)-[l:LINKS {type: $type}]->(d)"
        ),
        params={"src": str(src_article_id), "dst": str(dst_article_id), "type": type},
    )
