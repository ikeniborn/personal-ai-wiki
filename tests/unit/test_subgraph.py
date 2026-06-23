import uuid

from paw.graph.subgraph import SubEdge, build_subgraph


def _ids(n):
    return [uuid.uuid4() for _ in range(n)]


def test_depth_bounds_reachable_nodes():
    a, b, c, d = _ids(4)
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related"), SubEdge(c, d, "related")]
    assert build_subgraph(edges, a, 1).node_ids == {a, b}
    assert build_subgraph(edges, a, 2).node_ids == {a, b, c}
    assert build_subgraph(edges, a, 3).node_ids == {a, b, c, d}


def test_depth_zero_is_root_only():
    a, b = _ids(2)
    sg = build_subgraph([SubEdge(a, b, "related")], a, 0)
    assert sg.node_ids == {a}
    assert sg.edges == []


def test_undirected_expansion_follows_incoming_edges():
    a, b = _ids(2)
    # edge points b -> a; rooted at a we must still reach b
    assert build_subgraph([SubEdge(b, a, "related")], a, 1).node_ids == {a, b}


def test_type_filter_removes_edges_and_unreachable_nodes():
    a, b, c = _ids(3)
    edges = [SubEdge(a, b, "related"), SubEdge(a, c, "parent")]
    sg = build_subgraph(edges, a, 2, types={"related"})
    assert sg.node_ids == {a, b}
    assert [e.type for e in sg.edges] == ["related"]


def test_empty_type_set_yields_root_only():
    a, b = _ids(2)
    sg = build_subgraph([SubEdge(a, b, "related")], a, 2, types=set())
    assert sg.node_ids == {a} and sg.edges == []


def test_cycle_is_safe():
    a, b, c = _ids(3)
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related"), SubEdge(c, a, "related")]
    sg = build_subgraph(edges, a, 5)
    assert sg.node_ids == {a, b, c}
    assert len(sg.edges) == 3


def test_induced_edges_only_between_included_nodes():
    a, b, c = _ids(3)
    # c is beyond depth 1; the b->c edge must be excluded
    edges = [SubEdge(a, b, "related"), SubEdge(b, c, "related")]
    sg = build_subgraph(edges, a, 1)
    assert sg.node_ids == {a, b}
    assert sg.edges == [SubEdge(a, b, "related")]
