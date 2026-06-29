from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from paw.graph.age.naming import assert_graph_name


def agtype_params(params: Mapping[str, Any]) -> str:
    """Serialize a params map for AGE's `parameters` agtype argument.

    Every user-derived value lands here as JSON *data*. The Cypher body never
    interpolates these values; it references them as `$key`.
    """
    return json.dumps(dict(params), ensure_ascii=False)


def _load(cell: Any) -> Any:
    # asyncpg returns agtype scalars as text (e.g. '5', '"abc"', '["a","b"]').
    if isinstance(cell, str):
        try:
            return json.loads(cell)
        except json.JSONDecodeError:
            return cell
    return cell


async def run_cypher(
    session: AsyncSession,
    *,
    graph: str,
    body: str,
    columns: str,
    params: Mapping[str, Any] | None = None,
) -> list[tuple[Any, ...]]:
    """Run a read Cypher query and return deserialized rows.

    `graph` is validated; `body` and `columns` are fixed code literals. `params`
    is bound as a single agtype argument.
    """
    g = assert_graph_name(graph)
    sql = text(
        f"SELECT * FROM cypher('{g}', $cy${body}$cy$, CAST(:p AS agtype)) AS ({columns})"
    )
    res = await session.execute(sql, {"p": agtype_params(params or {})})
    return [tuple(_load(c) for c in row) for row in res.all()]


async def exec_cypher(
    session: AsyncSession,
    *,
    graph: str,
    body: str,
    params: Mapping[str, Any] | None = None,
) -> None:
    """Run a write Cypher statement; AGE still requires a result column, so we
    append `RETURN 1` projected as a single discarded column."""
    g = assert_graph_name(graph)
    sql = text(
        f"SELECT * FROM cypher('{g}', $cy${body}\nRETURN 1$cy$, CAST(:p AS agtype)) AS (ok agtype)"
    )
    await session.execute(sql, {"p": agtype_params(params or {})})


def as_uuid_list(values: Sequence[Any]) -> list[str]:
    """Normalize a list of UUIDs to strings for agtype params."""
    return [str(v) for v in values]
