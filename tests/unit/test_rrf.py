import uuid

from paw.vector.search import rrf_merge


def _ids(n):
    return [uuid.uuid4() for _ in range(n)]


def test_single_list_ranks_by_reciprocal():
    a, b, c = _ids(3)
    out = rrf_merge([([a, b, c], 1.0)], rrf_k=60)
    assert [cid for cid, _ in out] == [a, b, c]
    assert out[0][1] == 1.0 / 61
    assert out[1][1] == 1.0 / 62


def test_two_lists_fuse_overlap_to_top():
    a, b, c, d = _ids(4)
    # a is rank-1 in list-1 and rank-2 in list-2 -> highest fused score
    out = rrf_merge([([b, a, c], 1.0), ([d, a], 1.0)], rrf_k=60)
    assert out[0][0] == a


def test_weights_scale_contribution():
    a, b = _ids(2)
    out = rrf_merge([([a], 2.0), ([b], 1.0)], rrf_k=60)
    assert out[0][0] == a
    assert out[0][1] == 2.0 / 61
