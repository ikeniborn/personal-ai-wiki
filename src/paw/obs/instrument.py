"""Provider wrappers that record latency, tokens, cost and Langfuse spans.

There is no single LLM call site (``.chat()``/``.structured()``/``.embed()`` are
invoked from many modules), so we wrap the provider object once per op where it is
built in ``jobs/tasks.py``. Every downstream call is then timed and costed
uniformly without editing the harness or the request path.

Observability must NEVER change behaviour: provider failures re-raise after the
error counter is bumped; success-path metrics/traces are best-effort and the
Langfuse ``OpTrace`` is already fire-and-forget.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from paw.obs import metrics
from paw.obs.cost import compute_cost
from paw.obs.langfuse_client import OpTrace
from paw.providers.base import ChatProvider, ChatResult, EmbeddingProvider


class InstrumentedChatProvider:
    """Wraps a ChatProvider; records metrics + a Langfuse generation per ``chat``.

    Structurally satisfies the ``ChatProvider`` protocol; ``__getattr__`` forwards
    attributes such as ``chat_model``/``supports_tools`` to the inner provider.
    """

    def __init__(self, inner: ChatProvider, *, op: str, model: str, trace: OpTrace) -> None:
        self._inner = inner
        self._op = op
        self._model = model
        self._trace = trace

    async def chat(self, *args: Any, **kwargs: Any) -> ChatResult:
        start = time.perf_counter()
        try:
            result = await self._inner.chat(*args, **kwargs)
        except Exception:
            metrics.LLM_ERRORS.labels(op=self._op).inc()
            raise
        latency = time.perf_counter() - start
        usage = result.usage or {}
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        if not prompt and not completion:
            # No breakdown available: count everything as input.
            prompt = usage.get("total_tokens", 0)
        cost = compute_cost(self._model, usage)
        metrics.LLM_LATENCY.labels(op=self._op).observe(latency)
        metrics.LLM_TOKENS.labels(op=self._op, direction="in").inc(prompt)
        metrics.LLM_TOKENS.labels(op=self._op, direction="out").inc(completion)
        metrics.LLM_COST.labels(op=self._op, model=self._model).inc(cost)
        self._trace.generation(
            model=self._model, op=self._op, usage=usage, latency_s=latency, cost_usd=cost
        )
        return result

    async def structured(
        self,
        messages: Any,
        schema: Any,
        *,
        model: str | None = None,
        retries: int = 2,
    ) -> Any:
        # Pass ``self`` so each model round-trip flows through the instrumented
        # ``chat`` above, capturing structured-call cost/tokens.
        from paw.providers.structured import coerce_structured

        return await coerce_structured(
            self,
            messages,
            schema,
            model=model,
            retries=retries,
            use_tools=getattr(self._inner, "supports_tools", True),
        )

    def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        return self._inner.stream(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Forward chat_model, embedding_model, supports_tools, etc.
        return getattr(self._inner, name)


class InstrumentedEmbeddingProvider:
    """Wraps an EmbeddingProvider; records latency + an embeddings counter."""

    def __init__(self, inner: EmbeddingProvider, *, op: str, model: str, trace: OpTrace) -> None:
        self._inner = inner
        self._op = op
        self._model = model
        self._trace = trace

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        start = time.perf_counter()
        result = await self._inner.embed(texts, model=model)
        latency = time.perf_counter() - start
        metrics.LLM_LATENCY.labels(op=self._op).observe(latency)
        metrics.EMBEDDINGS.inc(len(texts))
        # Embedding APIs here return no token usage; compute_cost over an empty
        # usage dict yields 0.0, which is the correct value when unknown.
        metrics.LLM_COST.labels(op=self._op, model=self._model).inc(compute_cost(self._model, {}))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def instrument_chat(
    inner: ChatProvider, *, op: str, trace: OpTrace
) -> InstrumentedChatProvider:
    model = getattr(inner, "chat_model", "") or ""
    return InstrumentedChatProvider(inner, op=op, model=model, trace=trace)


def instrument_embedding(
    inner: EmbeddingProvider, *, op: str, trace: OpTrace
) -> InstrumentedEmbeddingProvider:
    model = getattr(inner, "embedding_model", "") or ""
    return InstrumentedEmbeddingProvider(inner, op=op, model=model, trace=trace)
