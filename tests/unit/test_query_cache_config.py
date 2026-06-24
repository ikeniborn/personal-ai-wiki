from paw.providers.config import QUERY_CACHE_KEY, QueryCacheConfig


def test_defaults():
    c = QueryCacheConfig()
    assert c.enabled is True
    assert c.sim_threshold == 0.92
    assert c.ttl_seconds == 30 * 24 * 3600
    assert c.suggest_top_k == 5


def test_key_constant():
    assert QUERY_CACHE_KEY == "query_cache"


def test_domain_override_merge():
    base = QueryCacheConfig()
    merged = QueryCacheConfig.model_validate(
        {**base.model_dump(), "enabled": False, "sim_threshold": 0.8}
    )
    assert merged.enabled is False and merged.sim_threshold == 0.8
    assert merged.ttl_seconds == 30 * 24 * 3600  # untouched
