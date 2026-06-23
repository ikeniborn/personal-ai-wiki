from paw.vector.search import match_entity_names


def test_matches_case_insensitive_substring():
    names = ["TCP", "OSI Model", "DNS"]
    assert match_entity_names(names, "How does tcp work?") == ["TCP"]
    assert match_entity_names(names, "explain the OSI model layers") == ["OSI Model"]


def test_no_match_returns_empty():
    assert match_entity_names(["TCP"], "tell me about udp") == []
