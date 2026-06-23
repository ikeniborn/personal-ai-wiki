from paw.harness.prompts import get_prompt


def test_query_overlay_has_grounding_rules():
    p = get_prompt("query", gen_language="en", reasoning_language="en")
    low = p.lower()
    assert "only" in low and "context" in low
    assert "don't know" in low or "do not know" in low
    assert "cite" in low or "citation" in low
