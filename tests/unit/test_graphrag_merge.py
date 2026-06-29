import uuid

from paw.graph.age.query import Neighbor, _merge_neighbors


def test_merge_orders_by_shared_then_id_and_caps() -> None:
    a = uuid.UUID(int=1)
    b = uuid.UUID(int=2)
    c = uuid.UUID(int=3)
    bridge = [(str(a), 3, ["X", "Y"]), (str(b), 1, ["Z"])]
    links = [(str(b),), (str(c),)]  # b also link-reachable; c only via links
    out = _merge_neighbors(bridge, links, max_neighbors=2)
    assert [n.article_id for n in out] == [a, b]   # a (3) > b (1) > c (0), capped to 2
    assert out[0].via == ["X", "Y"]
    assert out[1].shared == 1                       # bridge value wins over link's 0


def test_merge_link_only_neighbor_has_zero_shared() -> None:
    c = uuid.UUID(int=3)
    out = _merge_neighbors([], [(str(c),)], max_neighbors=5)
    assert out == [Neighbor(article_id=c, shared=0, via=[])]
