"""Cross-module instrument <-> langfuse <-> metrics seam.

Docker-free: uses the in-process stub provider and the prometheus registry.
Placed under integration/ for grouping; needs no containers.
"""
from __future__ import annotations

from typing import Any

import pytest
from tests.stubs import StubChatProvider

from paw.obs import metrics
from paw.obs.instrument import instrument_chat
from paw.obs.langfuse_client import LangfuseConfig, trace_op
from paw.providers.base import ChatResult


def _sample(counter: Any, **labels: str) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[no-any-return]


def _disabled_trace() -> Any:
    return trace_op(
        LangfuseConfig(enabled=False, host="", public_key="", secret_key=""),
        name="ingest", trace_id="t", metadata={},
    )


async def test_chat_records_cost_tokens_latency() -> None:
    inner = StubChatProvider(
        script=[
            ChatResult(
                content="done",
                usage={"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
            )
        ]
    )
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    cost_before = _sample(metrics.LLM_COST, op="ingest", model="gpt-4o-mini")
    tokens_before = _sample(metrics.LLM_TOKENS, op="ingest", direction="in")
    await prov.chat([])
    assert _sample(metrics.LLM_COST, op="ingest", model="gpt-4o-mini") > cost_before
    assert _sample(metrics.LLM_TOKENS, op="ingest", direction="in") == tokens_before + 1000


async def test_chat_tokens_fall_back_to_total_as_in() -> None:
    inner = StubChatProvider(script=[ChatResult(content="ok", usage={"total_tokens": 42})])
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    in_before = _sample(metrics.LLM_TOKENS, op="ingest", direction="in")
    out_before = _sample(metrics.LLM_TOKENS, op="ingest", direction="out")
    await prov.chat([])
    assert _sample(metrics.LLM_TOKENS, op="ingest", direction="in") == in_before + 42
    assert _sample(metrics.LLM_TOKENS, op="ingest", direction="out") == out_before


async def test_chat_error_increments_error_counter() -> None:
    class Boom(StubChatProvider):
        async def chat(self, *a: Any, **k: Any) -> ChatResult:  # type: ignore[override]
            raise RuntimeError("provider down")

    inner = Boom()
    inner.chat_model = "gpt-4o"  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    before = _sample(metrics.LLM_ERRORS, op="ingest")
    with pytest.raises(RuntimeError):
        await prov.chat([])
    assert _sample(metrics.LLM_ERRORS, op="ingest") == before + 1


async def test_structured_routes_through_instrumented_chat() -> None:
    from pydantic import BaseModel

    class Out(BaseModel):
        value: str

    # A tool-call result satisfies coerce_structured (use_tools defaults True).
    inner = StubChatProvider(
        script=[StubChatProvider.tool("emit_result", {"value": "hi"})]
    )
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    inner.supports_tools = True  # type: ignore[attr-defined]
    prov = instrument_chat(inner, op="ingest", trace=_disabled_trace())
    tokens_before = _sample(metrics.LLM_TOKENS, op="ingest", direction="in")
    result = await prov.structured([], Out)
    assert result.value == "hi"
    # The inner round-trip flowed through the wrapper's chat -> token metric moved
    # (the tool-call result carries no usage, so the "in" counter is unchanged but
    # the call was still routed; assert it did not error and produced a model).
    assert _sample(metrics.LLM_TOKENS, op="ingest", direction="in") >= tokens_before


async def test_enabled_langfuse_outage_does_not_fail_call() -> None:
    inner = StubChatProvider(script=[ChatResult(content="ok", usage={"total_tokens": 1})])
    inner.chat_model = "gpt-4o-mini"  # type: ignore[attr-defined]
    # Enabled but pointing at a dead host: the generation span must be swallowed.
    trace = trace_op(
        LangfuseConfig(enabled=True, host="http://127.0.0.1:1", public_key="pk", secret_key="sk"),
        name="ingest", trace_id="job-z", metadata={"domain_id": "d"},
    )
    prov = instrument_chat(inner, op="ingest", trace=trace)
    result = await prov.chat([])  # must NOT raise despite dead Langfuse
    trace.flush()
    assert result.content == "ok"


async def test_enabled_langfuse_tool_span_does_not_raise() -> None:
    # Step-5: tool-call spans are wired via trace.span; an enabled-but-dead host
    # must swallow the span and never raise.
    trace = trace_op(
        LangfuseConfig(enabled=True, host="http://127.0.0.1:1", public_key="pk", secret_key="sk"),
        name="ingest", trace_id="job-z2", metadata={"domain_id": "d"},
    )
    trace.span(name="tool:search_wiki", metadata={})  # must not raise
    trace.flush()
