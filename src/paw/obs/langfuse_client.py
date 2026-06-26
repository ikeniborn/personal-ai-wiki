"""Langfuse observability adapter for paw.

Wraps Langfuse v4 (langfuse==4.x) in a fire-and-forget, always-non-fatal
interface. When Langfuse is disabled or misconfigured, every call is a no-op.

v4 API notes
------------
- Client: ``Langfuse(public_key=..., secret_key=..., host=..., sample_rate=...,
  tracing_enabled=True, timeout=2)`` — ``sample_rate`` is native in v4.
- Observation: ``client.start_observation(name=..., as_type="span"|"generation",
  ...)`` returns a span/generation object with ``.start_observation(...)``,
  ``.end()``, and ``.update(...)``.
- Background log noise: constructing a client at a dead host emits OTel
  "Transient error … retrying" lines from the SDK's background exporter.
  We silence the langfuse logger to ERROR to keep test output clean.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langfuse import Langfuse

# Silence SDK background-transport noise at import time so unit-test output
# stays clean when tests point at a deliberately-dead host.
logging.getLogger("langfuse").setLevel(logging.ERROR)

# Module-level cache: (host, public_key, secret_key) -> Langfuse client.
# Keyed on credentials so distinct configs get distinct clients.
_CLIENT_CACHE: dict[tuple[str, str, str], Any] = {}


@dataclass(frozen=True)
class LangfuseConfig:
    enabled: bool
    host: str
    public_key: str
    secret_key: str
    redact_input: bool = False
    sample_rate: float = 1.0


def get_langfuse(cfg: LangfuseConfig) -> Langfuse | None:
    """Return a memoised Langfuse client, or None when disabled/misconfigured.

    Never raises. Construction failures are logged once and return None.
    """
    if not cfg.enabled:
        return None
    if not (cfg.host and cfg.public_key and cfg.secret_key):
        return None

    cache_key = (cfg.host, cfg.public_key, cfg.secret_key)
    if cache_key in _CLIENT_CACHE:
        return _CLIENT_CACHE[cache_key]  # type: ignore[no-any-return]

    try:
        from langfuse import Langfuse as LangfuseClient  # lazy import — only touched when enabled

        client = LangfuseClient(
            public_key=cfg.public_key,
            secret_key=cfg.secret_key,
            host=cfg.host,
            sample_rate=cfg.sample_rate,
            tracing_enabled=True,
            timeout=2,
        )
        _CLIENT_CACHE[cache_key] = client
        return client
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to construct Langfuse client; observability disabled."
        )
        return None


def trace_op(
    cfg: LangfuseConfig,
    *,
    name: str,
    trace_id: str,
    metadata: dict[str, object],
) -> OpTrace:
    """Return an OpTrace for a single operation.

    Returns a no-op OpTrace when Langfuse is disabled or the client cannot
    be obtained. Never raises.
    """
    client = get_langfuse(cfg)
    if client is None:
        return OpTrace(client=None, root=None, redact=cfg.redact_input)

    try:
        root = client.start_observation(
            name=name,
            as_type="span",
            metadata={**metadata, "trace_id": trace_id},
        )
        return OpTrace(client=client, root=root, redact=cfg.redact_input)
    except Exception:
        return OpTrace(client=None, root=None, redact=cfg.redact_input)


class OpTrace:
    """Fire-and-forget wrapper around a Langfuse root span.

    All methods are no-ops when the underlying span is None (disabled path).
    Every SDK call is wrapped in try/except so SDK failures never propagate.
    """

    def __init__(self, *, client: Any, root: Any, redact: bool) -> None:
        self._client = client
        self._root = root
        self._redact = redact

    def generation(
        self,
        *,
        model: str,
        op: str,
        usage: dict[str, int] | None,
        latency_s: float,
        cost_usd: float,
        input: object = None,
        output: object = None,
    ) -> None:
        if self._root is None:
            return
        try:
            gen = self._root.start_observation(
                name=op,
                as_type="generation",
                model=model,
                usage_details=usage or None,
                cost_details={"total": cost_usd},
                metadata={"latency_s": latency_s},
                input=None if self._redact else input,
                output=None if self._redact else output,
            )
            gen.end()
        except Exception:
            pass

    def span(self, *, name: str, metadata: dict[str, object]) -> None:
        if self._root is None:
            return
        try:
            sp = self._root.start_observation(
                name=name,
                as_type="span",
                metadata=metadata,
            )
            sp.end()
        except Exception:
            pass

    def flush(self) -> None:
        """Best-effort flush — fire-and-forget, never raises."""
        if self._root is None:
            return
        try:
            self._root.end()
            self._client.flush()
        except Exception:
            pass
