from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import db, require_role
from paw.services.graph import GraphService

router = APIRouter(tags=["graph"])


@router.get(
    "/graph",
    dependencies=[Depends(require_role("admin", "editor", "viewer"))],
)
async def get_graph(
    domain: uuid.UUID,
    root: uuid.UUID,
    depth: int | None = None,
    types: str | None = None,
    session: AsyncSession = Depends(db),
) -> dict[str, object]:
    # types is a CSV; absent -> None (full allowlist); "" -> [] (no edges, root only)
    type_list = None if types is None else [t for t in types.split(",") if t]
    payload = await GraphService(session).subgraph(
        domain_id=domain, root=root, depth=depth, types=type_list
    )
    return {
        "root": str(payload.root),
        "depth": payload.depth,
        "nodes": [
            {"id": str(n.id), "slug": n.slug, "title": n.title, "summary": n.summary}
            for n in payload.nodes
        ],
        "edges": [
            {"src": str(e.src), "dst": str(e.dst), "type": e.type} for e in payload.edges
        ],
    }
