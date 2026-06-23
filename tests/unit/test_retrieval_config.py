from paw.providers.config import RetrievalConfig


def test_defaults():
    c = RetrievalConfig()
    assert c.k1 == 20 and c.k2 == 20 and c.top_n == 8
    assert c.rrf_k == 60
    assert c.vector_weight == 1.0 and c.fts_weight == 1.0
    assert c.bfs_depth == 1
    assert c.context_token_budget == 3000
    assert c.entity_boost == 0.5
    assert c.fts_regconfig == "english"  # matches Phase 2 to_tsvector('english', ...)


def test_domain_override_merge():
    base = RetrievalConfig()
    merged = RetrievalConfig.model_validate({**base.model_dump(), "bfs_depth": 2, "top_n": 5})
    assert merged.bfs_depth == 2 and merged.top_n == 5
    assert merged.rrf_k == 60  # untouched
