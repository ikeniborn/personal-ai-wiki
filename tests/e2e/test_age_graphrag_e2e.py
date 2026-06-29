"""E2E: AGE GraphRAG — flag-on → rebuild → entity-bridged context → flag-off identical.

Approach
--------
We use `retrieve()` directly (same pattern as test_retrieve.py integration tests) so we
can control `graph_cfg` explicitly and observe `prompt_block` without needing an HTTP
stub round-trip.  The three assertions are:

1. CTE baseline  — no "via concepts" in prompt_block (BFS finds no neighbours because
   there is deliberately NO link row between the two articles).
2. AGE phase     — after enabling engine=age + _rebuild_domain_graph, the entity-bridge
   connects the articles; "via concepts" appears in prompt_block.
3. CTE flip-back — revert engine to cte; prompt_block is identical to the baseline.

The seed article ("alpha") gets the query text so it is found by vector search.
The related article ("beta") gets deliberately different text so it is NOT found
by vector search alone — only the AGE entity-bridge can pull it in as a neighbour.
Both share the entity "SharedConcept" so the bridge fires.

Important: chunks are created via embed_and_write (not raw SQL) so that the chunk IDs
used in chunk_entities match the IDs the vector search returns — the AGE entity bridge
needs to match on those same IDs.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from tests.factories import _set_domain_engine_age
from tests.stubs import StubEmbeddingProvider

from paw.db.managed import ensure_embedding_column
from paw.db.session import get_sessionmaker
from paw.graph.age import schema as age_schema
from paw.harness.retrieve import retrieve
from paw.ingest.chunking import ChunkSpec
from paw.jobs.tasks import _rebuild_domain_graph
from paw.providers.config import GraphConfig, RetrievalConfig
from paw.vector.embed import embed_and_write

# ---------------------------------------------------------------------------
# Retrieval config — narrow top_n=1 so only the seed article lands in the seed
# set; the other article is reachable only via the AGE entity-bridge.
# ---------------------------------------------------------------------------
_RETR = RetrievalConfig(k1=10, k2=10, top_n=1, bfs_depth=1)
_DIM = 8
_QUERY = "reliable delivery protocol"
_OTHER_TEXT = "completely unrelated xyzzy foobar"  # will NOT match the query


async def _seed_corpus(s, emb: StubEmbeddingProvider) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed domain + two articles sharing one entity, NO link row.

    Chunks are created via embed_and_write so chunk IDs in chunk_entities match
    what the vector search will return as seed_chunk_ids for the entity bridge.

    Returns (domain_id, seed_article_id, other_article_id).
    """
    domain_id = uuid.uuid4()
    seed_id = uuid.uuid4()
    other_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    await s.execute(
        text(
            "INSERT INTO domains (id, name, source_prefix, wiki_prefix) "
            "VALUES (:id, :n, :sp, :wp)"
        ),
        {"id": str(domain_id), "n": f"d-{domain_id.hex[:8]}", "sp": "src/x", "wp": "wiki/x"},
    )
    for art_id, slug, title in (
        (seed_id, "alpha", "Alpha"),
        (other_id, "beta", "Beta"),
    ):
        await s.execute(
            text(
                "INSERT INTO articles (id, domain_id, slug, title, storage_ref, current_rev)"
                " VALUES (:id, :d, :slug, :title, :ref, 1)"
            ),
            {"id": str(art_id), "d": str(domain_id), "slug": slug, "title": title, "ref": "r"},
        )

    # Create chunks via embed_and_write so embeddings + chunk IDs are in sync.
    # Seed article chunk matches the query; other article chunk does not.
    [seed_chunk_id] = await embed_and_write(
        s, article_id=seed_id, domain_id=domain_id,
        specs=[ChunkSpec(kind="summary", ord=0, heading_path=None, text=_QUERY)],
        embedder=emb,
    )
    [other_chunk_id] = await embed_and_write(
        s, article_id=other_id, domain_id=domain_id,
        specs=[ChunkSpec(kind="summary", ord=0, heading_path=None, text=_OTHER_TEXT)],
        embedder=emb,
    )

    # Shared entity — both articles and both chunks mention it (entity-bridge fires here).
    await s.execute(
        text("INSERT INTO entities (id, domain_id, name, kind) VALUES (:id, :d, :n, 'concept')"),
        {"id": str(entity_id), "d": str(domain_id), "n": "SharedConcept"},
    )
    for art_id, cid in ((seed_id, seed_chunk_id), (other_id, other_chunk_id)):
        await s.execute(
            text("INSERT INTO article_entities (article_id, entity_id) VALUES (:a, :e)"),
            {"a": str(art_id), "e": str(entity_id)},
        )
        await s.execute(
            text("INSERT INTO chunk_entities (chunk_id, entity_id) VALUES (:c, :e)"),
            {"c": str(cid), "e": str(entity_id)},
        )
    # Deliberately NO row in the `links` table — CTE BFS finds no neighbours.
    await s.flush()
    return domain_id, seed_id, other_id


@pytest.mark.usefixtures("wired_settings")
async def test_age_graphrag_cte_age_cte() -> None:
    """Full round-trip: cte baseline → age (entity-bridge + via concepts) → cte flip-back."""
    emb = StubEmbeddingProvider(dim=_DIM)
    maker = get_sessionmaker()

    # -----------------------------------------------------------------------
    # Phase 0: seed corpus with shared entity, no link row.
    # -----------------------------------------------------------------------
    async with maker() as s:
        await ensure_embedding_column(s, _DIM)
        domain_id, seed_id, other_id = await _seed_corpus(s, emb)
        await s.commit()

    # -----------------------------------------------------------------------
    # Phase 1: CTE baseline — BFS finds no neighbours (no links row).
    # -----------------------------------------------------------------------
    async with maker() as s:
        ctx_cte = await retrieve(
            s, domain_id=domain_id, query=_QUERY, embedder=emb,
            cfg=_RETR, embedding_version=1, redis=None, embed_model="m",
            graph_cfg=GraphConfig(engine="cte"),
        )
    assert ctx_cte.passages, "seed passages must be found"
    assert any(p.slug == "alpha" for p in ctx_cte.passages), "alpha must be in seed"
    assert all(p.slug != "beta" for p in ctx_cte.passages), "beta must NOT be a seed passage"
    assert "via concepts" not in ctx_cte.prompt_block, (
        "CTE baseline must NOT surface via-concepts provenance"
    )
    cte_block = ctx_cte.prompt_block  # save for later regression check

    # -----------------------------------------------------------------------
    # Phase 2: Enable engine=age + rebuild graph → entity-bridge connects articles.
    # -----------------------------------------------------------------------
    async with maker() as s:
        await _set_domain_engine_age(s, domain_id)
        await s.commit()

    async with maker() as s:
        await _rebuild_domain_graph(s, domain_id, on_batch=None)
        await s.commit()

    async with maker() as s:
        ctx_age = await retrieve(
            s, domain_id=domain_id, query=_QUERY, embedder=emb,
            cfg=_RETR, embedding_version=1, redis=None, embed_model="m",
            graph_cfg=GraphConfig(engine="age"),
        )
    assert "via concepts" in ctx_age.prompt_block, (
        "AGE entity-bridge must surface 'via concepts' provenance"
    )
    age_slugs = {r.slug for r in ctx_age.refs}
    assert "alpha" in age_slugs and "beta" in age_slugs, (
        "AGE must return both seed (alpha) and entity-bridged neighbour (beta)"
    )

    # -----------------------------------------------------------------------
    # Phase 3: Flip back to CTE → prompt_block must be identical to baseline.
    # -----------------------------------------------------------------------
    async with maker() as s:
        await s.execute(
            text(
                "UPDATE domains SET config = jsonb_set("
                "config, '{graph}', "
                "COALESCE(config->'graph', '{}'::jsonb) || '{\"engine\":\"cte\"}'::jsonb, true)"
                " WHERE id = :id"
            ),
            {"id": str(domain_id)},
        )
        await s.commit()

    async with maker() as s:
        ctx_cte2 = await retrieve(
            s, domain_id=domain_id, query=_QUERY, embedder=emb,
            cfg=_RETR, embedding_version=1, redis=None, embed_model="m",
            graph_cfg=GraphConfig(engine="cte"),
        )
    assert "via concepts" not in ctx_cte2.prompt_block, (
        "CTE flip-back must NOT surface via-concepts (regression guard)"
    )
    assert ctx_cte2.prompt_block == cte_block, (
        "CTE flip-back prompt_block must be identical to the baseline"
    )

    # Cleanup: drop the AGE graph created by the rebuild.
    async with maker() as s:
        await age_schema.drop_graph(s, domain_id)
        await s.commit()
