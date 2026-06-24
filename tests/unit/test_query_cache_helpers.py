import uuid

from paw.harness.retrieve import Ref
from paw.services.query_cache import dep_article_ids, normalize_query, passes_threshold


def test_normalize_lowercases_trims_collapses_ws():
    assert normalize_query("  What   IS  TCP?\n") == "what is tcp?"
    assert normalize_query("A\tB") == "a b"


def test_passes_threshold_uses_cosine_distance():
    # similarity = 1 - distance
    assert passes_threshold(0.05, 0.92) is True   # sim 0.95 >= 0.92
    assert passes_threshold(0.10, 0.92) is False  # sim 0.90 <  0.92
    assert passes_threshold(0.08, 0.92) is True   # sim 0.92 == 0.92 (boundary)


def test_dep_article_ids_dedups_preserving_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    refs = [
        Ref(article_id=a, slug="x", title="X"),
        Ref(article_id=b, slug="y", title="Y"),
        Ref(article_id=a, slug="x", title="X"),
    ]
    assert dep_article_ids(refs) == [a, b]


def test_dep_article_ids_empty():
    assert dep_article_ids([]) == []
