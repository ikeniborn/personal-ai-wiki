from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class TreeNode:
    id: uuid.UUID
    slug: str
    title: str
    children: list[TreeNode] = field(default_factory=list)


def normalize_parent_child(
    typed_edges: list[tuple[uuid.UUID, uuid.UUID, str]],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Fold ``parent``/``child`` typed links into ``(parent_id, child_id)`` pairs."""
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for src, dst, link_type in typed_edges:
        if link_type == "child":
            pairs.append((src, dst))
        elif link_type == "parent":
            pairs.append((dst, src))
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    deduped: list[tuple[uuid.UUID, uuid.UUID]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            deduped.append(pair)
    return deduped


def build_tree(
    nodes: list[tuple[uuid.UUID, str, str]],
    parent_child: list[tuple[uuid.UUID, uuid.UUID]],
) -> list[TreeNode]:
    """Forest of ``TreeNode`` from ``(id, slug, title)`` nodes + ``(parent, child)`` edges."""
    by_id = {nid: TreeNode(id=nid, slug=slug, title=title) for nid, slug, title in nodes}
    order = [nid for nid, _, _ in nodes]

    children_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    child_ids: set[uuid.UUID] = set()
    for parent, child in parent_child:
        if parent in by_id and child in by_id and parent != child:
            children_of.setdefault(parent, []).append(child)
            child_ids.add(child)

    visited: set[uuid.UUID] = set()

    def attach(nid: uuid.UUID) -> TreeNode:
        visited.add(nid)
        node = by_id[nid]
        node.children = []
        for cid in sorted(set(children_of.get(nid, ())), key=lambda c: by_id[c].title):
            if cid not in visited:
                node.children.append(attach(cid))
        return node

    roots = [nid for nid in order if nid not in child_ids]
    forest = [attach(nid) for nid in sorted(roots, key=lambda r: by_id[r].title)]
    # Defensive: nodes trapped in cycles were never a root and never visited; surface them.
    for nid in order:
        if nid not in visited:
            forest.append(attach(nid))
    return forest
