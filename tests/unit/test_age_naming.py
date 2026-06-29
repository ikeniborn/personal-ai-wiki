import uuid

import pytest

from paw.graph.age.naming import GRAPH_NAME_RE, assert_graph_name, graph_name


def test_graph_name_is_deterministic_and_valid() -> None:
    d = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
    name = graph_name(d)
    assert name == "g_000000000000000000000000000000ab"
    assert GRAPH_NAME_RE.match(name)
    assert len(name) <= 63  # valid Postgres/AGE identifier length


def test_assert_graph_name_rejects_injection() -> None:
    with pytest.raises(ValueError):
        assert_graph_name("g_abc'; DROP GRAPH x; --")
    with pytest.raises(ValueError):
        assert_graph_name("public")
