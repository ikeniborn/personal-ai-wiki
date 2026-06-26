from paw.obs.cost import MODEL_COSTS, compute_cost


def test_known_model_cost():
    model = next(iter(MODEL_COSTS))
    p_rate, c_rate = MODEL_COSTS[model]
    usage = {"prompt_tokens": 1000, "completion_tokens": 2000}
    expected = p_rate * 1 + c_rate * 2  # per-1K rates
    assert compute_cost(model, usage) == expected


def test_unknown_model_is_free():
    assert compute_cost("no-such-model", {"prompt_tokens": 5}) == 0.0


def test_missing_usage_keys_are_zero():
    model = next(iter(MODEL_COSTS))
    assert compute_cost(model, {}) == 0.0
