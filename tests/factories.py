from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_article_with_entities(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a domain + article + 2 chunks + 2 entities + mention rows.

    Returns (domain_id, article_id).
    """
    domain_id = uuid.uuid4()
    article_id = uuid.uuid4()
    c0, c1 = uuid.uuid4(), uuid.uuid4()
    e0, e1 = uuid.uuid4(), uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO domains (id, name, source_prefix, wiki_prefix) "
            "VALUES (:id, :n, :sp, :wp)"
        ),
        {"id": str(domain_id), "n": f"d-{domain_id.hex[:8]}", "sp": "src/x", "wp": "wiki/x"},
    )
    await s.execute(
        text(
            "INSERT INTO articles (id, domain_id, slug, title, storage_ref, current_rev) "
            "VALUES (:id, :d, :slug, :title, :ref, 1)"
        ),
        {"id": str(article_id), "d": str(domain_id), "slug": "a", "title": "Alpha", "ref": "r"},
    )
    for cid, ordv, kind in ((c0, 0, "summary"), (c1, 1, "section")):
        await s.execute(
            text(
                "INSERT INTO chunks (id, article_id, domain_id, kind, ord, text, embedding_version)"
                " VALUES (:id, :a, :d, :k, :o, :t, 1)"
            ),
            {"id": str(cid), "a": str(article_id), "d": str(domain_id), "k": kind,
             "o": ordv, "t": f"chunk {ordv}"},
        )
    for eid, name in ((e0, "Graphs"), (e1, "Databases")):
        await s.execute(
            text(
                "INSERT INTO entities (id, domain_id, name, kind)"
                " VALUES (:id, :d, :n, 'concept')"
            ),
            {"id": str(eid), "d": str(domain_id), "n": name},
        )
        await s.execute(
            text("INSERT INTO article_entities (article_id, entity_id) VALUES (:a, :e)"),
            {"a": str(article_id), "e": str(eid)},
        )
        await s.execute(
            text("INSERT INTO chunk_entities (chunk_id, entity_id) VALUES (:c, :e)"),
            {"c": str(c1), "e": str(eid)},
        )
    await s.flush()
    return domain_id, article_id
