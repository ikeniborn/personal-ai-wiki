import uuid

from paw.graph.tree import build_tree, normalize_parent_child


def test_normalize_maps_both_directions_and_dedups():
    p, c = uuid.uuid4(), uuid.uuid4()
    edges = [
        (p, c, "child"),    # p is parent of c
        (c, p, "parent"),   # c's parent is p  -> same (p, c)
        (p, c, "related"),  # ignored
    ]
    assert normalize_parent_child(edges) == [(p, c)]


def test_build_tree_nests_children_sorted_by_title():
    root = (uuid.uuid4(), "root", "Root")
    b = (uuid.uuid4(), "beta", "Beta")
    a = (uuid.uuid4(), "alpha", "Alpha")
    nodes = [root, b, a]
    pc = [(root[0], b[0]), (root[0], a[0])]
    forest = build_tree(nodes, pc)
    assert [n.title for n in forest] == ["Root"]
    assert [c.title for c in forest[0].children] == ["Alpha", "Beta"]  # title-sorted


def test_multiple_roots_when_no_links():
    a = (uuid.uuid4(), "a", "Apple")
    b = (uuid.uuid4(), "b", "Banana")
    forest = build_tree([b, a], [])
    assert [n.title for n in forest] == ["Apple", "Banana"]  # roots sorted by title
    assert all(n.children == [] for n in forest)


def test_cycle_does_not_recurse_forever_and_keeps_all_nodes():
    a = (uuid.uuid4(), "a", "A")
    b = (uuid.uuid4(), "b", "B")
    # a -> b -> a : both are children, so neither is a "never a child" root
    forest = build_tree([a, b], [(a[0], b[0]), (b[0], a[0])])
    seen = set()

    def walk(node):
        assert node.id not in seen  # each node appears exactly once
        seen.add(node.id)
        for child in node.children:
            walk(child)

    for node in forest:
        walk(node)
    assert seen == {a[0], b[0]}
