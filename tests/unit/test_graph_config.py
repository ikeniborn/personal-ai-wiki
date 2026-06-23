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
