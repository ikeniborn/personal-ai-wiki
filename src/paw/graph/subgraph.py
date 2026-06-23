from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class SubEdge:
    src: uuid.UUID
    dst: uuid.UUID
    type: str


@dataclass(frozen=True)
class Subgraph:
    node_ids: set[uuid.UUID]
    edges: list[SubEdge]


def build_subgraph(
    edges: list[SubEdge],
    root: uuid.UUID,
    depth: int,
    types: set[str] | None = None,
) -> Subgraph:
    """Undirected, depth-bounded, cycle-safe BFS from ``root`` over ``edges``.

    ``types`` filters edges before traversal (``None`` = all types, empty = none).
    Returns the reachable node set (always incl. root) and the induced edges
    (both endpoints reachable).
    """
    if types is not None:
        edges = [e for e in edges if e.type in types]

    adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
    for e in edges:
        adjacency.setdefault(e.src, []).append(e.dst)
        adjacency.setdefault(e.dst, []).append(e.src)

    visited: set[uuid.UUID] = {root}
    frontier: list[uuid.UUID] = [root]
    for _ in range(max(0, depth)):
        nxt: list[uuid.UUID] = []
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    nxt.append(neighbour)
        if not nxt:
            break
        frontier = nxt

    induced = [e for e in edges if e.src in visited and e.dst in visited]
    return Subgraph(node_ids=visited, edges=induced)
