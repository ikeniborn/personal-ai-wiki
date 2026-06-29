from paw.providers.config import GRAPH_KEY, GraphConfig


def test_graph_config_defaults():
    cfg = GraphConfig()
    assert cfg.default_depth == 2
    assert cfg.max_depth == 4
    assert cfg.link_types == ["related", "parent", "child", "references", "depends_on"]
    assert GRAPH_KEY == "graph"


def test_graph_config_override_validates():
    cfg = GraphConfig.model_validate({"default_depth": 1, "link_types": ["related"]})
    assert cfg.default_depth == 1
    assert cfg.max_depth == 4  # untouched default
    assert cfg.link_types == ["related"]


def test_graph_engine_defaults_to_cte() -> None:
    from paw.providers.config import GraphConfig

    cfg = GraphConfig()
    assert cfg.engine == "cte"
    assert cfg.expand_depth == 1
    assert cfg.max_entities == 8
    assert cfg.max_neighbors == 12


def test_graph_engine_age_override_merges_over_defaults() -> None:
    from paw.providers.config import GraphConfig

    merged = GraphConfig.model_validate({**GraphConfig().model_dump(), "engine": "age"})
    assert merged.engine == "age"
    # untouched fields keep defaults
    assert merged.default_depth == 2
