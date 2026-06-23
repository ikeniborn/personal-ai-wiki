from paw.harness.retrieve import budget_by_score


def test_keeps_highest_score_within_budget():
    items = [("a", "x" * 40, 0.1), ("b", "y" * 40, 0.9), ("c", "z" * 40, 0.5)]
    # each text ~ 40/4 = 10 tokens; budget 25 -> keep top-2 by score (b, c)
    kept = budget_by_score(items, token_budget=25)
    assert kept == ["b", "c"]


def test_always_keeps_first_even_if_over_budget():
    items = [("a", "x" * 400, 0.9)]  # ~100 tokens, budget 10
    assert budget_by_score(items, token_budget=10) == ["a"]


def test_empty():
    assert budget_by_score([], token_budget=100) == []
