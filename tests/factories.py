from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _set_domain_engine_age(s: AsyncSession, domain_id: uuid.UUID) -> None:
    # NB: a fresh domain has config = {} (server_default). jsonb_set with a 2-level path
    # is a no-op when the 'graph' parent is missing, so merge the parent explicitly.
    await s.execute(
        text(
            "UPDATE domains SET config = jsonb_set("
            "config, '{graph}', "
            "COALESCE(config->'graph', '{}'::jsonb) || '{\"engine\":\"age\"}'::jsonb, true) "
            "WHERE id = :id"
        ),
        {"id": str(domain_id)},
    )
    await s.flush()


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


async def seed_two_linked_articles(
    s: AsyncSession,
    *,
    other_title: str = "Beta",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert one domain, two articles with a shared entity and a link row.

    Returns (domain_id, seed_chunk_id, seed_article_id, other_article_id).
    The shared entity is mentioned by seed_chunk and other_chunk so the
    entity-bridge Cypher can connect the two articles.
    """
    domain_id = uuid.uuid4()
    seed_article_id = uuid.uuid4()
    other_article_id = uuid.uuid4()
    seed_chunk_id = uuid.uuid4()
    other_chunk_id = uuid.uuid4()
    shared_entity_id = uuid.uuid4()

    await s.execute(
        text(
            "INSERT INTO domains (id, name, source_prefix, wiki_prefix) "
            "VALUES (:id, :n, :sp, :wp)"
        ),
        {"id": str(domain_id), "n": f"d-{domain_id.hex[:8]}", "sp": "src/x", "wp": "wiki/x"},
    )
    for art_id, slug, title in (
        (seed_article_id, "alpha", "Alpha"),
        (other_article_id, "beta", other_title),
    ):
        await s.execute(
            text(
                "INSERT INTO articles (id, domain_id, slug, title, storage_ref, current_rev) "
                "VALUES (:id, :d, :slug, :title, :ref, 1)"
            ),
            {"id": str(art_id), "d": str(domain_id), "slug": slug, "title": title, "ref": "r"},
        )
    for cid, art_id, ordv in (
        (seed_chunk_id, seed_article_id, 0),
        (other_chunk_id, other_article_id, 0),
    ):
        await s.execute(
            text(
                "INSERT INTO chunks (id, article_id, domain_id, kind, ord, text, embedding_version)"
                " VALUES (:id, :a, :d, :k, :o, :t, 1)"
            ),
            {"id": str(cid), "a": str(art_id), "d": str(domain_id), "k": "summary",
             "o": ordv, "t": "chunk text"},
        )
    # Shared entity mentioned by BOTH chunks so entity-bridge connects the articles.
    await s.execute(
        text(
            "INSERT INTO entities (id, domain_id, name, kind)"
            " VALUES (:id, :d, :n, 'concept')"
        ),
        {"id": str(shared_entity_id), "d": str(domain_id), "n": "SharedConcept"},
    )
    for art_id, cid in ((seed_article_id, seed_chunk_id), (other_article_id, other_chunk_id)):
        await s.execute(
            text("INSERT INTO article_entities (article_id, entity_id) VALUES (:a, :e)"),
            {"a": str(art_id), "e": str(shared_entity_id)},
        )
        await s.execute(
            text("INSERT INTO chunk_entities (chunk_id, entity_id) VALUES (:c, :e)"),
            {"c": str(cid), "e": str(shared_entity_id)},
        )
    # Link row: seed -> other (NOT-NULL domain_id required).
    await s.execute(
        text(
            "INSERT INTO links (domain_id, src_article_id, dst_article_id, type)"
            " VALUES (:d, :s, :o, 'related')"
        ),
        {"d": str(domain_id), "s": str(seed_article_id), "o": str(other_article_id)},
    )
    await s.flush()
    return domain_id, seed_chunk_id, seed_article_id, other_article_id


async def seed_cross_domain_pair(
    s: AsyncSession,
) -> tuple[
    tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    tuple[uuid.UUID, uuid.UUID, uuid.UUID],
]:
    """Insert two independent domains each with one article + chunk + entity.

    Returns ((domain_a, chunk_a, article_a), (domain_b, chunk_b, article_b)).
    """
    results = []
    for letter in ("a", "b"):
        domain_id = uuid.uuid4()
        article_id = uuid.uuid4()
        chunk_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO domains (id, name, source_prefix, wiki_prefix) "
                "VALUES (:id, :n, :sp, :wp)"
            ),
            {"id": str(domain_id), "n": f"d{letter}-{domain_id.hex[:8]}",
             "sp": f"src/{letter}", "wp": f"wiki/{letter}"},
        )
        await s.execute(
            text(
                "INSERT INTO articles (id, domain_id, slug, title, storage_ref, current_rev) "
                "VALUES (:id, :d, :slug, :title, :ref, 1)"
            ),
            {"id": str(article_id), "d": str(domain_id), "slug": letter,
             "title": f"Article{letter.upper()}", "ref": "r"},
        )
        await s.execute(
            text(
                "INSERT INTO chunks (id, article_id, domain_id, kind, ord, text, embedding_version)"
                " VALUES (:id, :a, :d, :k, :o, :t, 1)"
            ),
            {"id": str(chunk_id), "a": str(article_id), "d": str(domain_id),
             "k": "summary", "o": 0, "t": "chunk text"},
        )
        await s.execute(
            text(
                "INSERT INTO entities (id, domain_id, name, kind)"
                " VALUES (:id, :d, :n, 'concept')"
            ),
            {"id": str(entity_id), "d": str(domain_id), "n": f"Entity{letter.upper()}"},
        )
        await s.execute(
            text("INSERT INTO article_entities (article_id, entity_id) VALUES (:a, :e)"),
            {"a": str(article_id), "e": str(entity_id)},
        )
        await s.execute(
            text("INSERT INTO chunk_entities (chunk_id, entity_id) VALUES (:c, :e)"),
            {"c": str(chunk_id), "e": str(entity_id)},
        )
        results.append((domain_id, chunk_id, article_id))
    await s.flush()
    return (results[0][0], results[0][1], results[0][2]), (
        results[1][0], results[1][1], results[1][2]
    )
