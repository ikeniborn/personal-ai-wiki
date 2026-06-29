import json

from paw.graph.age.cypher import agtype_params


def test_agtype_params_encodes_values_as_json_data() -> None:
    malicious = '$$ ) MATCH (x) DETACH DELETE x //'
    out = agtype_params({"title": malicious, "ids": ["a", "b"]})
    # It is valid JSON, and the malicious string is a *string value*, not raw SQL.
    parsed = json.loads(out)
    assert parsed == {"title": malicious, "ids": ["a", "b"]}
    # The dollar-quote sequence is preserved as data, never as a query delimiter.
    assert parsed["title"] == malicious


def test_agtype_params_empty() -> None:
    assert json.loads(agtype_params({})) == {}
