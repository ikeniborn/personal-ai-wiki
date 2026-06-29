from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.graph.age.naming import assert_graph_name, graph_name

VLABELS: tuple[str, ...] = ("Article", "Entity", "Chunk")
ELABELS: tuple[str, ...] = ("LINKS", "MENTIONS", "IN_ARTICLE", "CHUNK_MENTIONS")

# Property-index targets: (label, property). MERGE and rebuild are slow without these.
_PROP_INDEXES: tuple[tuple[str, str], ...] = (
    ("Article", "id"),
    ("Entity", "id"),
    ("Chunk", "id"),
    ("Chunk", "article_id"),
)


async def _graph_exists(session: AsyncSession, name: str) -> bool:
    res = await session.execute(
        text("SELECT count(*) FROM ag_catalog.ag_graph WHERE name = :n"), {"n": name}
    )
    return bool(res.scalar_one())


async def _label_exists(session: AsyncSession, name: str, label: str) -> bool:
    res = await session.execute(
        text(
            "SELECT count(*) FROM ag_catalog.ag_label l "
            "JOIN ag_catalog.ag_graph g ON g.graphid = l.graph "
            "WHERE g.name = :n AND l.name = :l"
        ),
        {"n": name, "l": label},
    )
    return bool(res.scalar_one())


async def ensure_graph(session: AsyncSession, domain_id: uuid.UUID) -> str:
    name = assert_graph_name(graph_name(domain_id))
    if not await _graph_exists(session, name):
        await session.execute(text(f"SELECT create_graph('{name}')"))
    for label in VLABELS:
        if not await _label_exists(session, name, label):
            await session.execute(text(f"SELECT create_vlabel('{name}', '{label}')"))
    for label in ELABELS:
        if not await _label_exists(session, name, label):
            await session.execute(text(f"SELECT create_elabel('{name}', '{label}')"))
    for label, prop in _PROP_INDEXES:
        idx = f"ix_{name}_{label.lower()}_{prop}"
        await session.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS "{idx}" ON "{name}"."{label}" '
                f"USING btree (ag_catalog.agtype_access_operator("
                f"properties, '\"{prop}\"'::agtype))"
            )
        )
    return name


async def drop_graph(session: AsyncSession, domain_id: uuid.UUID) -> None:
    name = assert_graph_name(graph_name(domain_id))
    if await _graph_exists(session, name):
        await session.execute(text(f"SELECT drop_graph('{name}', true)"))
