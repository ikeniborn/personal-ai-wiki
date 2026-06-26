from unittest.mock import MagicMock

from paw.obs.langfuse_client import LangfuseConfig, OpTrace, get_langfuse, trace_op


def _disabled() -> LangfuseConfig:
    return LangfuseConfig(enabled=False, host="", public_key="", secret_key="")


def test_disabled_returns_no_client():
    assert get_langfuse(_disabled()) is None


def test_disabled_trace_is_total_noop():
    trace = trace_op(_disabled(), name="ingest", trace_id="job-1", metadata={})
    # None of these may raise or require a network call.
    trace.generation(
        model="gpt-4o-mini", op="ingest", usage={"total_tokens": 5},
        latency_s=0.1, cost_usd=0.0,
    )
    trace.span(name="tool:search_wiki", metadata={})
    trace.flush()


def test_enabled_but_unreachable_never_raises():
    # Even "enabled" with a dead host must not raise from the helpers.
    cfg = LangfuseConfig(
        enabled=True, host="http://127.0.0.1:1", public_key="pk", secret_key="sk"
    )
    trace = trace_op(cfg, name="ingest", trace_id="job-2", metadata={"domain_id": "d"})
    trace.generation(
        model="gpt-4o", op="ingest", usage={"prompt_tokens": 1, "completion_tokens": 1},
        latency_s=0.2, cost_usd=0.01,
    )
    trace.flush()  # fire-and-forget; a dead endpoint must be swallowed


def test_redact_input_masks_sensitive_data():
    """Verify redact_input=True causes generation to forward input/output as None."""
    # Build a stub root observation that records the kwargs it receives.
    stub_root = MagicMock()
    stub_gen = MagicMock()
    stub_root.start_observation.return_value = stub_gen

    # Create an OpTrace with redact=True.
    trace = OpTrace(client=MagicMock(), root=stub_root, redact=True)
    trace.generation(
        model="gpt-4o", op="test_op", usage={"total_tokens": 10},
        latency_s=0.1, cost_usd=0.01,
        input="secret_input", output="secret_output",
    )

    # Assert that the stub root was called with input=None and output=None.
    call_kwargs = stub_root.start_observation.call_args.kwargs
    assert call_kwargs["input"] is None
    assert call_kwargs["output"] is None


def test_redact_input_false_forwards_data():
    """Verify redact_input=False causes generation to forward actual input/output."""
    stub_root = MagicMock()
    stub_gen = MagicMock()
    stub_root.start_observation.return_value = stub_gen

    # Create an OpTrace with redact=False.
    trace = OpTrace(client=MagicMock(), root=stub_root, redact=False)
    trace.generation(
        model="gpt-4o", op="test_op", usage={"total_tokens": 10},
        latency_s=0.1, cost_usd=0.01,
        input="real_input", output="real_output",
    )

    # Assert that the stub root was called with actual values.
    call_kwargs = stub_root.start_observation.call_args.kwargs
    assert call_kwargs["input"] == "real_input"
    assert call_kwargs["output"] == "real_output"
