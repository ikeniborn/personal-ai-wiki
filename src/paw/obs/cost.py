from __future__ import annotations

# USD per 1K tokens: (prompt, completion). Embedding models bill prompt-side only.
# Seeded, not exhaustive — unknown models cost 0.0 (YAGNI; admins extend as needed).
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.00250, 0.01000),
    "text-embedding-3-small": (0.00002, 0.0),
    "text-embedding-3-large": (0.00013, 0.0),
}


def compute_cost(model: str, usage: dict[str, int]) -> float:
    rates = MODEL_COSTS.get(model)
    if rates is None:
        return 0.0
    prompt_rate, completion_rate = rates
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return prompt_rate * (prompt / 1000) + completion_rate * (completion / 1000)
