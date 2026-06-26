"""Integration tests for Task 5: worker /metrics server + arq job/lock/queue metrics.

Test A: ingest_domain increments paw_job_total{kind="ingest",status="succeeded"} and
        paw_job_duration_seconds_count{kind="ingest"} by exactly 1 per invocation.
Test B: set_queue_depth reads the live arq Redis sorted-set length and stores it in
        metrics.QUEUE_DEPTH; the gauge reflects N then 0 after the queue is cleared.
"""
from __future__ import annotations

import arq.constants
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.obs import metrics
from paw.providers.config import WikiConfig
from paw.storage.postgres import PostgresStorage
from paw.worker import set_queue_depth


def _draft_chat() -> StubChatProvider:
    return StubChatProvider(
        [
            StubChatProvider.tool("emit_result", {"entities": ["QUIC"], "key_points": ["fast"]}),
            StubChatProvider.tool(
                "emit_result",
                {
                    "slug": "quic",
                    "title": "QUIC",
                    "summary": "QUIC is fast.",
                    "markdown": "## Overview\n\nQUIC over UDP. It is fast. Low latency.",
                    "entities": ["QUIC"],
                    "citations": [{"quote": "QUIC over UDP", "locator": "p1"}],
                },
            ),
        ]
    )


async def _seed(db_session):  # type: ignore[no-untyped-def]
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    ref = await PostgresStorage(db_session).put(
        b"QUIC runs over UDP.", content_type="text/markdown"
    )
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref=ref, filename="q.md", type="md", checksum="c1"
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="ingest")
    await db_session.commit()
    return dom, src, job


# ---------------------------------------------------------------------------
# Test A: ingest_domain emits JOB_TOTAL + JOB_DURATION
# ---------------------------------------------------------------------------


async def test_ingest_job_increments_metrics(
    db_session, redis_client, wired_settings, monkeypatch
):
    dom, src, job = await _seed(db_session)

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)

    # Read before-values — registry is process-global so use deltas.
    before_total = _job_total_count("ingest", "succeeded")
    before_duration = _job_duration_count("ingest")

    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
    )

    assert out == "succeeded"
    assert _job_total_count("ingest", "succeeded") - before_total == 1
    assert _job_duration_count("ingest") - before_duration == 1


# ---------------------------------------------------------------------------
# Test B: set_queue_depth reflects live arq queue length
# ---------------------------------------------------------------------------


async def test_set_queue_depth_reflects_live_queue(redis_client, wired_settings):
    queue_key = arq.constants.default_queue_name  # "arq:queue"

    # Seed N fake entries into the arq sorted set.
    n = 5
    await redis_client.zadd(queue_key, {f"fake-job-{i}": float(i) for i in range(n)})

    await set_queue_depth(redis_client)
    assert metrics.QUEUE_DEPTH._value.get() == n  # type: ignore[attr-defined]

    # Clear the queue and verify the gauge drops to 0.
    await redis_client.delete(queue_key)
    await set_queue_depth(redis_client)
    assert metrics.QUEUE_DEPTH._value.get() == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_total_count(kind: str, status: str) -> float:
    """Return the current value of paw_job_total{kind, status}."""
    try:
        return metrics.JOB_TOTAL.labels(kind=kind, status=status)._value.get()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0.0


def _job_duration_count(kind: str) -> float:
    """Return the current sample count of paw_job_duration_seconds{kind}."""
    try:
        child = metrics.JOB_DURATION.labels(kind=kind)
        # prometheus_client stores count in _samples() as Sample(name='_count', ...)
        for s in child._samples():  # type: ignore[attr-defined]
            if s.name == "_count":
                return float(s.value)
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0
