---
review:
  plan_hash: 57221f77bd2d2c95
  spec_hash: 5a6ed65bca4c1452
  last_run: 2026-06-22
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      severity: WARNING
      section: "Global Constraints / Task 4 (worker)"
      section_hash: 0a1c1191a4fb4b20
      text: >-
        Spec LLD §7 lists arq queues (LLM vs light), retries/backoff and
        poison->dead-letter. Plan implements a single LLM-bound queue
        (WorkerSettings.functions=[heartbeat, ingest_domain]) plus a startup
        stuck-job reconciler, but no explicit per-task retries/backoff config
        or dead-letter path. Reconciler covers stuck->failed; arq defaults
        apply otherwise. Partial coverage, not blocking.
      verdict: open
    - id: F-002
      severity: WARNING
      section: "Task 6 / Step 6 (Web UI)"
      section_hash: 0b307af079db34d0
      text: >-
        Task 6 Step 6 web-template work and the tests/api/test_web_pages.py
        assertion body are non-literal fill-ins (directed to follow existing
        Jinja/login patterns). DoD is measurable (exact strings named:
        hx-post=".../ingest", id="job-drawer", dim-change warning), but the
        test code is not provided verbatim like other steps. Deliberate and
        bounded per design; logged for transparency.
      verdict: open
chain:
  intent: null
  spec:   docs/superpowers/specs/2026-06-22-paw-phase-2-ingest-design.md
---

# Phase 2D — Jobs/Worker + API + SSE + Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the ingest pipeline as a real background job — extend the upload guard to pdf/docx/html, add Redis domain/model locks, job progress via Redis pub/sub with `jobs.log` replay, the cooperative-cancel `ingest_domain` arq task (heartbeat + reconciler, no partial article on cancel), the API (`ingest`/`init`/`jobs`/SSE/`cancel` + binary sources), the setup-wizard embedding-dim capture, and the Web UI ingest drawer + Connection settings.

**Architecture:** The API creates a `jobs(queued)` row and enqueues an arq job carrying `job_id`; the worker runs the Plan 2C `run_ingest` on a **dedicated data session** while writing progress/heartbeat/status on a **separate job session** — so cancel/failure rolls back the data session leaving no partial article while job bookkeeping still commits. Progress streams over Redis pub/sub channel `job:{id}`; the SSE endpoint first replays `jobs.log` (reconnect-safe) then tails the channel. A per-domain Redis lock (non-blocking) blocks a second writing job; a per-model Redis lock (blocking) serializes same-model LLM calls. The setup wizard captures the embedding dim and triggers the 2B managed migration.

**Tech Stack:** FastAPI (async) + `StreamingResponse` SSE · `arq` worker (existing `WorkerSettings`) · `redis.asyncio` pub/sub + locks · Jinja2 + HTMX (existing templates, `hx-sse`) · the 2C `run_ingest` / `build_structure_plan` · pytest (httpx `AsyncClient`, testcontainers PG+Redis, stub-LLM).

## Global Constraints

- Depends on **Plans 2A, 2B, 2C** (providers + factory, repos + managed migration + jobs repo, `run_ingest`/`build_structure_plan`, loaders). Implement them first.
- Lint/type/test gates: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`.
- **Transaction rule:** repos `flush()`, services `commit()`. The worker owns its commits explicitly (two sessions; data session commits only on success).
- API conventions (existing): RFC 9457 `ProblemError`; `require_role(...)`; `require_csrf` on mutations (SSE GET is exempt); routers mounted under `/api/v1`; web routes unprefixed. Reuse `Depends(db)`, `current_user`, `get_redis`.
- **No partial article on cancel/failure:** ingest data writes happen on a session that is committed only on success; bookkeeping (status/log/heartbeat) is a different session.
- API key never logged; provider built only inside the worker/init via the 2A factory (decrypt at call-site).
- New deps: none (`arq`, `redis`, `python-multipart` already present).
- arq queues: LLM-bound `ingest_domain` registered in `WorkerSettings.functions`; `on_startup` runs the stuck-job reconciler.

---

### Task 1: Upload guard for pdf/docx/html

**Files:**
- Modify: `src/paw/security/uploads.py`
- Modify: `src/paw/services/sources.py`
- Modify: `src/paw/api/routers/sources.py`
- Test: `tests/unit/test_uploads.py` (append), `tests/api/test_sources.py` (append)

**Interfaces:**
- Produces:
  - `def validate_source_upload(filename: str, data: bytes, *, max_bytes: int) -> str` — returns the normalized source type (`md`/`txt`/`html`/`pdf`/`docx`); raises `UploadRejected` on bad extension, oversize, bad magic bytes, or (for text types) non-UTF-8. Magic checks: pdf → `%PDF-`; docx → zip `PK\x03\x04`.
  - `SourceService.upload(self, *, domain_id, filename, data, content_type) -> Source` — replaces the text-only path; stores large/binary blobs with `large=True` when `len(data) > 256*1024`; sets `Source.type` from the validated type.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_uploads.py`:

```python
from paw.security.uploads import validate_source_upload


def test_validate_source_accepts_pdf_magic():
    assert validate_source_upload("doc.pdf", b"%PDF-1.7\n...", max_bytes=1024) == "pdf"


def test_validate_source_rejects_pdf_bad_magic():
    import pytest

    from paw.security.uploads import UploadRejected

    with pytest.raises(UploadRejected):
        validate_source_upload("doc.pdf", b"not a pdf", max_bytes=1024)


def test_validate_source_accepts_docx_zip_magic():
    assert validate_source_upload("d.docx", b"PK\x03\x04rest", max_bytes=1024) == "docx"


def test_validate_source_accepts_html_and_md():
    assert validate_source_upload("p.html", b"<html></html>", max_bytes=1024) == "html"
    assert validate_source_upload("n.md", b"# h", max_bytes=1024) == "md"


def test_validate_source_rejects_unknown_ext():
    import pytest

    from paw.security.uploads import UploadRejected

    with pytest.raises(UploadRejected):
        validate_source_upload("x.exe", b"MZ", max_bytes=1024)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_uploads.py -k validate_source -v`
Expected: FAIL — `ImportError: cannot import name 'validate_source_upload'`

- [ ] **Step 3: Extend the upload guard**

Append to `src/paw/security/uploads.py`:

```python
_TEXT_EXT = {".md": "md", ".markdown": "md", ".txt": "txt"}
_HTML_EXT = {".html": "html", ".htm": "html"}
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"


def validate_source_upload(filename: str, data: bytes, *, max_bytes: int) -> str:
    lower = filename.lower()
    if len(data) > max_bytes:
        raise UploadRejected("file too large")
    if not data:
        raise UploadRejected("empty file")
    for ext, kind in {**_TEXT_EXT, **_HTML_EXT}.items():
        if lower.endswith(ext):
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise UploadRejected("not valid UTF-8 text") from e
            return kind
    if lower.endswith(".pdf"):
        if not data.startswith(_PDF_MAGIC):
            raise UploadRejected("not a valid PDF (magic bytes)")
        return "pdf"
    if lower.endswith(".docx"):
        if not data.startswith(_ZIP_MAGIC):
            raise UploadRejected("not a valid DOCX (magic bytes)")
        return "docx"
    raise UploadRejected(f"extension not allowed: {filename}")
```

- [ ] **Step 4: Extend the source service + router**

In `src/paw/services/sources.py`, add the general `upload` method (keep `upload_text` if other callers use it, else replace its body to delegate):

```python
    async def upload(
        self, *, domain_id: uuid.UUID, filename: str, data: bytes, content_type: str | None
    ) -> Source:
        from paw.security.uploads import validate_source_upload

        kind = validate_source_upload(filename, data, max_bytes=get_settings().max_upload_bytes)
        checksum = hashlib.sha256(data).hexdigest()
        ref = await self._store.put(
            data, content_type=content_type, large=len(data) > 256 * 1024
        )
        src = await self._repo.create(
            domain_id=domain_id, storage_ref=ref, filename=filename, type=kind, checksum=checksum
        )
        await self._s.commit()
        return src
```

In `src/paw/api/routers/sources.py`, change the handler to call `.upload(...)`:

```python
        src = await SourceService(session).upload(
            domain_id=domain_id, filename=file.filename or "upload",
            data=data, content_type=file.content_type,
        )
```

- [ ] **Step 5: Add an API test for a pdf upload**

Append to `tests/api/test_sources.py`:

```python
async def test_upload_pdf_source(client):
    import fitz

    csrf = client.cookies.get("paw_csrf")
    dom = (await client.post("/api/v1/domains", json={"name": "pdfs"},
                             headers={"x-csrf-token": csrf})).json()
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "pdf body")
    files = {"file": ("paper.pdf", doc.tobytes(), "application/pdf")}
    r = await client.post(f"/api/v1/domains/{dom['id']}/sources",
                          files=files, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    assert r.json()["type"] == "pdf"
```

- [ ] **Step 6: Run tests + commit**

Run: `uv run pytest tests/unit/test_uploads.py tests/api/test_sources.py -v`
Expected: PASS (existing + new).

```bash
git add src/paw/security/uploads.py src/paw/services/sources.py src/paw/api/routers/sources.py tests/unit/test_uploads.py tests/api/test_sources.py
git commit -m "feat(sources): upload guard + service for pdf/docx/html (magic-byte + size)"
```

---

### Task 2: Redis domain + model locks

**Files:**
- Create: `src/paw/jobs/__init__.py`, `src/paw/jobs/locks.py`
- Test: `tests/integration/test_job_locks.py`

**Interfaces:**
- Produces (consumed by Task 4):
  - `domain_lock(redis, domain_id, *, ttl=3600)` — async context manager, **non-blocking** `SET NX`; yields `bool` (acquired?). Releases only if acquired. (One writing job per domain.)
  - `model_lock(redis, model, *, ttl=600, poll=0.05, timeout=120.0)` — async context manager, **blocking** acquire (spins on `SET NX`); raises `TimeoutError` past `timeout`. (Serialize one model; parallel across models.)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_job_locks.py`:

```python
import pytest

from paw.jobs.locks import domain_lock, model_lock


async def test_domain_lock_blocks_second(redis_client):
    async with domain_lock(redis_client, "dom-1") as got1:
        assert got1 is True
        async with domain_lock(redis_client, "dom-1") as got2:
            assert got2 is False  # already held -> second job rejected
    # released after the with-block
    async with domain_lock(redis_client, "dom-1") as got3:
        assert got3 is True


async def test_model_lock_serializes(redis_client):
    async with model_lock(redis_client, "gpt-x"):
        with pytest.raises(TimeoutError):
            async with model_lock(redis_client, "gpt-x", timeout=0.2):
                pass
    # different model is independent
    async with model_lock(redis_client, "other-model", timeout=0.2):
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_job_locks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.jobs'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/jobs/__init__.py` (empty) and `src/paw/jobs/locks.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def domain_lock(redis: Any, domain_id: str, *, ttl: int = 3600) -> AsyncIterator[bool]:
    key = f"lock:domain:{domain_id}"
    acquired = bool(await redis.set(key, "1", nx=True, ex=ttl))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.delete(key)


@asynccontextmanager
async def model_lock(
    redis: Any, model: str, *, ttl: int = 600, poll: float = 0.05, timeout: float = 120.0
) -> AsyncIterator[None]:
    key = f"lock:model:{model}"
    waited = 0.0
    while not await redis.set(key, "1", nx=True, ex=ttl):
        await asyncio.sleep(poll)
        waited += poll
        if waited >= timeout:
            raise TimeoutError(f"model lock timeout: {model}")
    try:
        yield
    finally:
        await redis.delete(key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_job_locks.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/jobs/locks.py tests/integration/test_job_locks.py
git commit -m "feat(jobs): redis domain job-lock (non-blocking) + model-lock (blocking)"
```

---

### Task 3: Job progress (pub/sub + replay)

**Files:**
- Create: `src/paw/jobs/progress.py`
- Test: `tests/integration/test_job_progress.py`

**Interfaces:**
- Consumes: `JobRepo` (2B).
- Produces (consumed by Tasks 4,6):
  - `def channel(job_id: uuid.UUID | str) -> str` → `f"job:{job_id}"`.
  - `async def publish(redis, job_id, event: dict[str, object]) -> None` — `redis.publish(channel, json.dumps(event))`.
  - `async def sse_events(redis, job_repo: JobRepo, job_id) -> AsyncIterator[str]` — async generator yielding SSE frames (`f"data: {json}\n\n"`): first replays `jobs.log` entries; if the job is already terminal, stops; otherwise subscribes to `channel(job_id)` and yields until an event with `status` in terminal set arrives (or the channel closes).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_job_progress.py`:

```python
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.jobs.progress import channel, publish, sse_events


async def test_channel_and_publish(redis_client):
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel("j1"))
    await publish(redis_client, "j1", {"step": "draft"})
    # drain subscribe ack + message
    msg = None
    for _ in range(5):
        m = await pubsub.get_message(timeout=1.0)
        if m and m["type"] == "message":
            msg = m
            break
    assert msg is not None and b"draft" in (
        msg["data"] if isinstance(msg["data"], bytes) else msg["data"].encode()
    )
    await pubsub.unsubscribe(channel("j1"))


async def test_sse_replays_log_for_terminal_job(db_session, redis_client):
    dom = await DomainRepo(db_session).create(name="d", source_prefix="s", wiki_prefix="w")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.append_log(job.id, {"step": "extract"})
    await repo.append_log(job.id, {"step": "done", "status": "succeeded"})
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()
    frames = [frame async for frame in sse_events(redis_client, repo, job.id)]
    body = "".join(frames)
    assert "extract" in body
    assert "succeeded" in body
    assert all(f.startswith("data: ") and f.endswith("\n\n") for f in frames)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_job_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.jobs.progress'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/jobs/progress.py`:

```python
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from paw.db.repos.jobs import JobRepo

_TERMINAL = {"succeeded", "failed", "cancelled"}


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


def _frame(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def publish(redis: Any, job_id: uuid.UUID | str, event: dict[str, Any]) -> None:
    await redis.publish(channel(job_id), json.dumps(event, ensure_ascii=False))


async def sse_events(redis: Any, job_repo: JobRepo, job_id: uuid.UUID) -> AsyncIterator[str]:
    job = await job_repo.get(job_id)
    if job is None:
        return
    for entry in job.log:
        yield _frame(entry)
    if job.status in _TERMINAL:
        return
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(job_id))
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
            if msg is None:
                continue
            raw = msg["data"]
            text = raw.decode() if isinstance(raw, bytes) else raw
            event = json.loads(text)
            yield _frame(event)
            if event.get("status") in _TERMINAL:
                return
    finally:
        await pubsub.unsubscribe(channel(job_id))
        await pubsub.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_job_progress.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/jobs/progress.py tests/integration/test_job_progress.py
git commit -m "feat(jobs): progress pub/sub channel + SSE replay generator"
```

---

### Task 4: ingest_domain arq task (cancel + heartbeat + reconciler)

**Files:**
- Create: `src/paw/jobs/tasks.py`
- Modify: `src/paw/worker.py` (register task + reconciler on startup)
- Test: `tests/integration/test_ingest_task.py`

**Interfaces:**
- Consumes: `JobRepo` (2B), `domain_lock`/`model_lock` (Task 2), `publish` (Task 3), `run_ingest` (2C), `ProviderSettingsService`/factory (2A), `load_source`/`SourceRepo`/`PostgresStorage`.
- Produces:
  - `class IngestCancelled(Exception)`
  - `async def _build_providers(session, box) -> tuple[ChatProvider, EmbeddingProvider, WikiConfig, int]` — reads `ProviderConfig` + `WikiConfig` from `app_settings`, builds chat + embedding providers; returns `(chat, embedder, wiki, embedding_dim)`. **Seam for tests to monkeypatch.**
  - `async def ingest_domain(ctx, job_id: str, domain_id: str, source_id: str | None = None, topic: str | None = None) -> str` — the arq task. Two sessions (job bookkeeping vs ingest data); acquires `domain_lock` (fail fast → job `failed` "domain busy"); status→running + heartbeat; loads source markdown (from `source_id`, or uses `topic` as the seed text); runs `run_ingest` under `model_lock(chat_model)` and `asyncio.wait_for(timeout=wiki.request_timeout_s * wiki.max_steps)` with an `on_step` that checks `cancel_requested` (→`IngestCancelled`), heartbeats, appends log, publishes; commits the **data** session only on success; on cancel/exception rolls the data session back (no partial article) and records terminal status on the job session. Returns the terminal status string.
  - `WorkerSettings.functions` includes `ingest_domain`; `on_startup` calls `JobRepo.reconcile_stuck(older_than_seconds=120)`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_ingest_task.py`:

```python
import paw.jobs.tasks as tasks_mod
from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.providers.config import WikiConfig
from paw.storage.postgres import PostgresStorage

from tests.stubs import StubChatProvider, StubEmbeddingProvider


def _draft_chat() -> StubChatProvider:
    return StubChatProvider(
        [
            StubChatProvider.tool("emit_result", {"entities": ["QUIC"], "key_points": ["fast"]}),
            StubChatProvider.tool(
                "emit_result",
                {"slug": "quic", "title": "QUIC", "summary": "QUIC is fast.",
                 "markdown": "## Overview\n\nQUIC over UDP. It is fast. Low latency.",
                 "entities": ["QUIC"], "citations": [{"quote": "QUIC over UDP", "locator": "p1"}]},
            ),
        ]
    )


async def _seed(db_session):
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    ref = await PostgresStorage(db_session).put(b"QUIC runs over UDP.", content_type="text/markdown")
    src = await SourceRepo(db_session).create(
        domain_id=dom.id, storage_ref=ref, filename="q.md", type="md", checksum="c1"
    )
    job = await JobRepo(db_session).create(domain_id=dom.id, kind="ingest")
    await db_session.commit()
    return dom, src, job


async def test_ingest_task_success(db_session, redis_client, wired_settings, monkeypatch):
    dom, src, job = await _seed(db_session)

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
    )
    assert out == "succeeded"
    got = await JobRepo(db_session).get(job.id)
    assert got is not None and got.status == "succeeded" and got.article_id is not None


async def test_ingest_task_cancel_leaves_no_article(db_session, redis_client, wired_settings, monkeypatch):
    dom, src, job = await _seed(db_session)
    await JobRepo(db_session).request_cancel(job.id)  # cancel before it runs
    await db_session.commit()

    async def fake_build(session, box):
        return _draft_chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, str(job.id), str(dom.id), source_id=str(src.id)
    )
    assert out == "cancelled"
    from sqlalchemy import text
    n = await db_session.execute(text("SELECT count(*) FROM articles WHERE domain_id=:d"),
                                 {"d": str(dom.id)})
    assert n.scalar_one() == 0  # no partial article
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ingest_task.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.jobs.tasks'`

- [ ] **Step 3: Write the task**

Create `src/paw/jobs/tasks.py`:

```python
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from paw.config import get_settings
from paw.db.managed import embedding_dim
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.db.session import get_sessionmaker
from paw.harness.ops.ingest import run_ingest
from paw.ingest.loaders import load_source
from paw.jobs.locks import domain_lock, model_lock
from paw.jobs.progress import publish
from paw.providers.base import ChatProvider, EmbeddingProvider
from paw.providers.config import WikiConfig
from paw.providers.factory import build_chat_provider, build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage


class IngestCancelled(Exception):
    pass


async def _build_providers(
    session: Any, box: SecretBox
) -> tuple[ChatProvider, EmbeddingProvider, WikiConfig, int]:
    svc = ProviderSettingsService(session, box=box)
    pc = await svc.get_provider()
    if pc is None:
        raise RuntimeError("provider not configured")
    wiki = await svc.get_wiki()
    chat = build_chat_provider(pc, box)
    embedder = build_embedding_provider(pc, box)
    return chat, embedder, wiki, pc.embedding_dim


async def _source_markdown(session: Any, source_id: str) -> str:
    src = await SourceRepo(session).get(uuid.UUID(source_id))
    if src is None:
        raise RuntimeError("source not found")
    data = await PostgresStorage(session).get(src.storage_ref)
    return load_source(data, src.type)


async def ingest_domain(
    ctx: dict[str, Any],
    job_id: str,
    domain_id: str,
    source_id: str | None = None,
    topic: str | None = None,
) -> str:
    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()

            async def on_step(msg: str) -> None:
                if await jobs.is_cancel_requested(jid):
                    raise IngestCancelled()
                await jobs.heartbeat(jid)
                await jobs.append_log(jid, {"step": msg})
                await job_s.commit()
                await publish(redis, jid, {"step": msg})

            try:
                chat, embedder, wiki, dim = await _build_providers(data_s, box)
                if dim != await embedding_dim(data_s):
                    # managed dim column may not exist yet; run_ingest ensures it.
                    pass
                source_md = (
                    await _source_markdown(data_s, source_id) if source_id else (topic or "")
                )
                if not source_md.strip():
                    raise RuntimeError("empty source")
                async with model_lock(redis, getattr(chat, "chat_model", "default")):
                    result = await asyncio.wait_for(
                        run_ingest(
                            data_s, domain_id=did, source_md=source_md, chat=chat,
                            embedder=embedder, cfg=wiki, dim=dim, on_step=on_step,
                        ),
                        timeout=wiki.request_timeout_s * wiki.max_steps,
                    )
                await data_s.commit()
                await jobs.set_status(jid, "succeeded", article_id=result.article_id)
                await jobs.append_log(jid, {"step": "done"})
                await job_s.commit()
                await publish(
                    redis, jid,
                    {"step": "done", "status": "succeeded", "article_id": str(result.article_id)},
                )
                return "succeeded"
            except IngestCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
```

- [ ] **Step 4: Register in the worker**

Modify `src/paw/worker.py` — add the reconciler + register the task. Replace the `WorkerSettings` block with:

```python
async def reconcile_jobs(ctx: dict[str, Any]) -> str:
    from paw.db.repos.jobs import JobRepo
    from paw.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        n = await JobRepo(session).reconcile_stuck(older_than_seconds=120)
        await session.commit()
    return f"reconciled:{n}"


class WorkerSettings:
    functions = [heartbeat, ingest_domain]
    redis_settings = _LazyRedisSettings()

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        await heartbeat(ctx)
        await reconcile_jobs(ctx)
```

Add the import near the top of `worker.py`:

```python
from paw.jobs.tasks import ingest_domain
```

- [ ] **Step 5: Run the task tests**

Run: `uv run pytest tests/integration/test_ingest_task.py -v`
Expected: PASS (2 tests) — success path writes an article; cancel path leaves zero articles.

- [ ] **Step 6: Commit**

```bash
git add src/paw/jobs/tasks.py src/paw/worker.py tests/integration/test_ingest_task.py
git commit -m "feat(jobs): ingest_domain task (locks, cancel cleanup, heartbeat, reconciler)"
```

---

### Task 5: API — ingest / init / jobs / cancel + enqueue seam

**Files:**
- Create: `src/paw/jobs/queue.py`
- Create: `src/paw/services/jobs.py`
- Create: `src/paw/api/routers/jobs.py`
- Modify: `src/paw/api/routers/domains.py` (add ingest + init endpoints) **or** create `src/paw/api/routers/ingest.py`
- Modify: `src/paw/main.py` (mount the jobs + ingest routers)
- Test: `tests/api/test_jobs_api.py`

**Interfaces:**
- Produces:
  - `async def enqueue_ingest(redis, *, job_id, domain_id, source_id=None, topic=None) -> None` — `arq` enqueue of `ingest_domain` (via `arq.connections.create_pool` from settings, or a passed pool). **Seam for tests to monkeypatch.**
  - `JobService(session)`:
    - `async def start_ingest(self, *, domain_id, source_id) -> Job` — create `jobs(queued)`, **commit**, enqueue, return job.
    - `async def init_domain(self, *, domain_id, brief, redis) -> list[tuple[str, uuid.UUID]]` — build structure plan (chat from settings), create + enqueue one ingest job per topic; returns `[(topic, job_id)]`.
    - `async def cancel(self, job_id) -> None` — `JobRepo.request_cancel`, commit.
  - Routes (under `/api/v1`):
    - `POST /domains/{domain_id}/ingest` (role admin/editor, csrf) → `202 {"job_id": ...}` (body `{"source_id": "<uuid>"}`).
    - `POST /domains/{domain_id}/init` (role admin/editor, csrf) → `200 {"topics": [{"topic","job_id"}]}` (body `{"brief": "..."}`).
    - `GET /jobs/{job_id}` (any role) → `{"id","status","kind","article_id","error","log"}`.
    - `POST /jobs/{job_id}/cancel` (admin/editor, csrf) → `202`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_jobs_api.py`:

```python
import paw.services.jobs as jobs_svc
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password

import pytest
from httpx import ASGITransport, AsyncClient

from paw.main import create_app


@pytest.fixture
async def client(db_session, wired_settings, monkeypatch):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()

    async def fake_enqueue(redis, **kwargs):
        return None  # do not actually enqueue in API tests

    monkeypatch.setattr(jobs_svc, "enqueue_ingest", fake_enqueue)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login",
                     json={"email": "admin@example.com", "password": "pw12345"})
        yield c


async def test_start_ingest_returns_job_id(client, db_session):
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nbody", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources",
                             files=files, headers=h)).json()
    r = await client.post(f"/api/v1/domains/{dom['id']}/ingest",
                          json={"source_id": src["id"]}, headers=h)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    g = await client.get(f"/api/v1/jobs/{job_id}")
    assert g.status_code == 200
    assert g.json()["status"] == "queued"


async def test_cancel_sets_flag(client):
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nbody", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources",
                             files=files, headers=h)).json()
    job_id = (await client.post(f"/api/v1/domains/{dom['id']}/ingest",
                                json={"source_id": src["id"]}, headers=h)).json()["job_id"]
    r = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=h)
    assert r.status_code == 202
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_jobs_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.services.jobs'`

- [ ] **Step 3: Write the enqueue seam**

Create `src/paw/jobs/queue.py`:

```python
from __future__ import annotations

import uuid
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from paw.config import get_settings


async def enqueue_ingest(
    redis: Any | None = None,
    *,
    job_id: uuid.UUID,
    domain_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    topic: str | None = None,
) -> None:
    pool = redis or await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await pool.enqueue_job(
        "ingest_domain",
        str(job_id),
        str(domain_id),
        str(source_id) if source_id else None,
        topic,
    )
```

- [ ] **Step 4: Write the job service**

Create `src/paw/services/jobs.py`:

```python
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import Job
from paw.db.repos.jobs import JobRepo
from paw.harness.ops.init import build_structure_plan
from paw.jobs.queue import enqueue_ingest
from paw.providers.config import WikiConfig
from paw.security.secrets import SecretBox
from paw.config import get_settings


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = JobRepo(session)

    async def start_ingest(self, *, domain_id: uuid.UUID, source_id: uuid.UUID) -> Job:
        job = await self._repo.create(domain_id=domain_id, kind="ingest")
        await self._s.commit()
        await enqueue_ingest(None, job_id=job.id, domain_id=domain_id, source_id=source_id)
        return job

    async def init_domain(
        self, *, domain_id: uuid.UUID, brief: str, redis: Any
    ) -> list[tuple[str, uuid.UUID]]:
        from paw.providers.factory import build_chat_provider
        from paw.services.provider_settings import ProviderSettingsService

        box = SecretBox(get_settings().fernet_key)
        psvc = ProviderSettingsService(self._s, box=box)
        pc = await psvc.get_provider()
        wiki = await psvc.get_wiki() if pc else WikiConfig()
        if pc is None:
            raise RuntimeError("provider not configured")
        chat = build_chat_provider(pc, box)
        topics = await build_structure_plan(
            domain_name=str(domain_id), brief=brief, chat=chat, cfg=wiki
        )
        out: list[tuple[str, uuid.UUID]] = []
        for topic in topics:
            job = await self._repo.create(domain_id=domain_id, kind="ingest")
            await self._s.commit()
            await enqueue_ingest(redis, job_id=job.id, domain_id=domain_id, topic=topic)
            out.append((topic, job.id))
        return out

    async def cancel(self, job_id: uuid.UUID) -> None:
        await self._repo.request_cancel(job_id)
        await self._s.commit()
```

- [ ] **Step 5: Write the routers + mount**

Create `src/paw/api/routers/jobs.py`:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.deps import current_user, db, require_csrf, require_role
from paw.api.errors import ProblemError
from paw.db.models import User
from paw.db.repos.jobs import JobRepo
from paw.services.jobs import JobService

router = APIRouter(tags=["jobs"])


class IngestRequest(BaseModel):
    source_id: uuid.UUID


class InitRequest(BaseModel):
    brief: str = ""


@router.post("/domains/{domain_id}/ingest", status_code=202,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def start_ingest(domain_id: uuid.UUID, body: IngestRequest,
                       session: AsyncSession = Depends(db)) -> dict[str, str]:
    job = await JobService(session).start_ingest(domain_id=domain_id, source_id=body.source_id)
    return {"job_id": str(job.id)}


@router.post("/domains/{domain_id}/init",
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def init_domain(domain_id: uuid.UUID, body: InitRequest, request_=None,
                      session: AsyncSession = Depends(db)) -> dict[str, list[dict[str, str]]]:
    from paw.api.deps import get_redis

    pairs = await JobService(session).init_domain(
        domain_id=domain_id, brief=body.brief, redis=get_redis()
    )
    return {"topics": [{"topic": t, "job_id": str(j)} for t, j in pairs]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(db),
                  _: User = Depends(require_role("admin", "editor", "viewer"))) -> dict[str, object]:
    job = await JobRepo(session).get(job_id)
    if job is None:
        raise ProblemError(status=404, title="Job not found")
    return {
        "id": str(job.id), "status": job.status, "kind": job.kind,
        "article_id": str(job.article_id) if job.article_id else None,
        "error": job.error, "log": job.log,
    }


@router.post("/jobs/{job_id}/cancel", status_code=202,
             dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))])
async def cancel_job(job_id: uuid.UUID, _: User = Depends(current_user),
                     session: AsyncSession = Depends(db)) -> dict[str, str]:
    await JobService(session).cancel(job_id)
    return {"status": "cancelling"}
```

In `src/paw/main.py`, import `jobs` router and include it:

```python
from paw.api.routers import jobs as jobs_router
# ... inside the for-loop tuple, add jobs_router:
for r in (auth_router, domains_router, sources_router, articles_router,
          setup_router, settings_router, users_router, jobs_router):
    app.include_router(r.router, prefix="/api/v1")
```

- [ ] **Step 6: Run tests + commit**

Run: `uv run pytest tests/api/test_jobs_api.py -v`
Expected: PASS (2 tests).

```bash
git add src/paw/jobs/queue.py src/paw/services/jobs.py src/paw/api/routers/jobs.py src/paw/main.py tests/api/test_jobs_api.py
git commit -m "feat(api): ingest/init/jobs/cancel endpoints + arq enqueue seam"
```

---

### Task 6: SSE endpoint + setup-wizard dim + Web ingest drawer + settings + E2E

**Files:**
- Modify: `src/paw/api/routers/jobs.py` (add SSE `/jobs/{id}/events`)
- Modify: `src/paw/services/setup.py` (capture provider connection + dim, run managed migration)
- Modify: `src/paw/services/settings.py` or `provider_settings.py` (already covers connection write in 2A)
- Modify: `src/paw/api/web/routes.py` + templates (`domain.html` ingest drawer, `settings.html` connection form, `setup.html` dim field)
- Test: `tests/api/test_jobs_sse.py`, `tests/api/test_setup.py` (append dim), `tests/e2e/test_ingest_e2e.py`

**Interfaces:**
- Produces:
  - `GET /jobs/{job_id}/events` (any role; **no CSRF** — GET) → `StreamingResponse(media_type="text/event-stream")` driven by `sse_events(...)`.
  - `SetupService.complete(...)` extended signature: also accepts `base_url`, `api_key`, `chat_model`, `embedding_model`, `embedding_dim`, `vision_model=None`; after creating the admin it calls `ProviderSettingsService.set_provider(...)` and `ensure_embedding_column(session, embedding_dim)` then commits.

- [ ] **Step 1: Write the failing SSE test**

Create `tests/api/test_jobs_sse.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.domains import DomainRepo
from paw.db.repos.jobs import JobRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


@pytest.fixture
async def client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post("/api/v1/auth/login",
                     json={"email": "admin@example.com", "password": "pw12345"})
        yield c


async def test_sse_streams_replayed_log(client, db_session):
    # a terminal job with log entries -> SSE replays then closes
    dom = await DomainRepo(db_session).create(name="net", source_prefix="s", wiki_prefix="w")
    repo = JobRepo(db_session)
    job = await repo.create(domain_id=dom.id, kind="ingest")
    await repo.append_log(job.id, {"step": "extract"})
    await repo.append_log(job.id, {"step": "done", "status": "succeeded"})
    await repo.set_status(job.id, "succeeded")
    await db_session.commit()
    r = await client.get(f"/api/v1/jobs/{job.id}/events")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "extract" in r.text
    assert "succeeded" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_jobs_sse.py -v`
Expected: FAIL — 404 (no `/events` route yet).

- [ ] **Step 3: Add the SSE route**

Append to `src/paw/api/routers/jobs.py`:

```python
from fastapi.responses import StreamingResponse

from paw.api.deps import get_redis
from paw.jobs.progress import sse_events


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: uuid.UUID, session: AsyncSession = Depends(db),
                     _: User = Depends(require_role("admin", "editor", "viewer"))) -> StreamingResponse:
    repo = JobRepo(session)
    return StreamingResponse(
        sse_events(get_redis(), repo, job_id), media_type="text/event-stream"
    )
```

- [ ] **Step 4: Run the SSE test**

Run: `uv run pytest tests/api/test_jobs_sse.py -v`
Expected: PASS.

- [ ] **Step 5: Extend the setup wizard for connection + dim**

Modify `src/paw/services/setup.py` `complete(...)`:

```python
    async def complete(
        self,
        *,
        email: str,
        password: str,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        vision_model: str | None = None,
    ) -> User:
        if not await self.needs_setup():
            raise ProblemError(status=409, title="Already initialized")
        admin = await self._users.create(
            email=email, pw_hash=hash_password(password), role="admin"
        )
        await self._settings.upsert({})
        from paw.db.managed import ensure_embedding_column
        from paw.services.provider_settings import ProviderSettingsService

        psvc = ProviderSettingsService(self._s)
        await psvc.set_provider(
            base_url=base_url, chat_model=chat_model, embedding_model=embedding_model,
            embedding_dim=embedding_dim, api_key=api_key, vision_model=vision_model,
        )
        await ensure_embedding_column(self._s, embedding_dim)
        await self._s.commit()
        return admin
```

Update the setup API router (`src/paw/api/routers/setup.py`) request model + handler to accept the new fields (`base_url`, `api_key`, `chat_model`, `embedding_model`, `embedding_dim`, optional `vision_model`) and pass them through to `SetupService.complete(...)`.

Append a setup test to `tests/api/test_setup.py`:

```python
async def test_setup_captures_dim_and_creates_vector_column(client, db_session):
    r = await client.post("/api/v1/setup", json={
        "email": "admin@example.com", "password": "pw12345",
        "base_url": "https://api.example/v1", "api_key": "sk-x",
        "chat_model": "gpt-x", "embedding_model": "emb-x", "embedding_dim": 8,
    })
    assert r.status_code == 201
    from paw.db.managed import embedding_dim
    assert await embedding_dim(db_session) == 8
```

- [ ] **Step 6: Web UI — ingest drawer + connection settings**

In `src/paw/api/web/templates/domain.html`, add an Ingest action + a job drawer that connects to SSE and a cancel button. Add to the content block:

```html
<div class="content-header">
  <form hx-post="/api/v1/domains/{{ domain.id }}/ingest" hx-ext="json-enc"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer" hx-swap="innerHTML">
    <input type="hidden" name="source_id" value="{{ latest_source_id | default('') }}">
    <button type="submit" {% if not latest_source_id %}disabled{% endif %}>Ingest latest source</button>
  </form>
</div>
<aside id="job-drawer" class="drawer"></aside>
```

Create `src/paw/api/web/templates/_job_drawer.html` (returned by a small web route that renders after ingest starts; or render client-side). Minimal SSE-wired drawer:

```html
<div class="job" hx-ext="sse" sse-connect="/api/v1/jobs/{{ job_id }}/events">
  <progress max="6" id="job-progress"></progress>
  <ul id="job-log" sse-swap="message" hx-swap="beforeend"></ul>
  <button hx-post="/api/v1/jobs/{{ job_id }}/cancel"
          hx-headers='{"x-csrf-token": "{{ csrf }}"}'>Cancel</button>
</div>
```

In `src/paw/api/web/templates/settings.html`, add a Connection section card (base_url, chat/embedding/vision models, embedding dim) posting to a settings endpoint, and show the dim-change warning text: `Changing the embedding dimension requires an ALTER + HNSW rebuild + reindex.`

Add a web test to `tests/api/test_web_pages.py`:

```python
async def test_domain_page_has_ingest_action(client, db_session):
    # seed admin + login handled by the existing web client fixture pattern;
    # assert the domain page renders the Ingest control + job drawer mount.
    ...  # follow the existing test_web_pages.py login+domain-create pattern, then:
    # assert 'hx-post="/api/v1/domains/' in page.text and 'id="job-drawer"' in page.text
```

(Implementer: fill the web test body following the existing `tests/api/test_web_pages.py` login/seed pattern; assert the Ingest form action and `id="job-drawer"` are present, and that `/settings` shows the dim-change warning string.)

- [ ] **Step 7: E2E — fixture source → ingest → article + chunks + embeddings**

Create `tests/e2e/test_ingest_e2e.py`:

```python
import paw.jobs.tasks as tasks_mod
import pytest
from httpx import ASGITransport, AsyncClient

from paw.db.repos.jobs import JobRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.providers.config import WikiConfig
from paw.security.passwords import hash_password

from tests.stubs import StubChatProvider, StubEmbeddingProvider


def _chat() -> StubChatProvider:
    return StubChatProvider([
        StubChatProvider.tool("emit_result", {"entities": ["QUIC"], "key_points": ["fast"]}),
        StubChatProvider.tool("emit_result", {
            "slug": "quic", "title": "QUIC", "summary": "QUIC is fast.",
            "markdown": "## Overview\n\nQUIC over UDP. It is fast. Low latency.",
            "entities": ["QUIC"], "citations": [{"quote": "QUIC over UDP", "locator": "p1"}]}),
    ])


@pytest.fixture
async def client(db_session, wired_settings):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c


async def test_ingest_e2e(client, db_session, redis_client, monkeypatch):
    # setup wizard (captures dim 8, creates vector column)
    await client.post("/api/v1/setup", json={
        "email": "admin@example.com", "password": "pw12345",
        "base_url": "https://api.example/v1", "api_key": "sk-x",
        "chat_model": "gpt-x", "embedding_model": "emb-x", "embedding_dim": 8})
    await client.post("/api/v1/auth/login",
                      json={"email": "admin@example.com", "password": "pw12345"})
    csrf = client.cookies.get("paw_csrf")
    h = {"x-csrf-token": csrf}
    dom = (await client.post("/api/v1/domains", json={"name": "net"}, headers=h)).json()
    files = {"file": ("q.md", b"# QUIC\n\nQUIC over UDP.", "text/markdown")}
    src = (await client.post(f"/api/v1/domains/{dom['id']}/sources",
                             files=files, headers=h)).json()
    job_id = (await client.post(f"/api/v1/domains/{dom['id']}/ingest",
                                json={"source_id": src["id"]}, headers=h)).json()["job_id"]

    # run the worker task inline with stub providers
    async def fake_build(session, box):
        return _chat(), StubEmbeddingProvider(dim=8), WikiConfig(chunk_target_size=60), 8

    monkeypatch.setattr(tasks_mod, "_build_providers", fake_build)
    out = await tasks_mod.ingest_domain(
        {"redis": redis_client}, job_id, dom["id"], source_id=src["id"])
    assert out == "succeeded"

    got = await JobRepo(db_session).get(__import__("uuid").UUID(job_id))
    assert got is not None and got.article_id is not None
    page = await client.get(f"/api/v1/articles/{got.article_id}")
    assert page.status_code == 200
    assert "QUIC" in page.json()["html"]
    from sqlalchemy import text
    n = await db_session.execute(
        text("SELECT count(*) FROM chunks WHERE article_id=:a AND embedding IS NOT NULL"),
        {"a": str(got.article_id)})
    assert n.scalar_one() >= 1
```

- [ ] **Step 8: Final gate + commit**

Run the full quality gate:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Expected: all green across Plans 2A–2D.

```bash
git add src/paw/api/routers/jobs.py src/paw/services/setup.py src/paw/api/routers/setup.py src/paw/api/web tests/api/test_jobs_sse.py tests/api/test_setup.py tests/api/test_web_pages.py tests/e2e/test_ingest_e2e.py
git commit -m "feat(ui+api): SSE job events, setup-wizard dim, ingest drawer, settings, e2e"
```

---

## Self-Review

**Spec coverage (against §In scope · Jobs/worker, API, Web UI; §Key flows; §Security; §Acceptance criteria):**
- Upload guard extended to pdf/docx/html (magic-byte + size) → Task 1 (AC-2 input). ✅
- `jobs/{tasks,progress}.py`; lifecycle queued→running→terminal; progress via Redis pub/sub `job:{id}` + replay from `jobs.log`; cooperative `cancel_requested`; heartbeat + startup reconciler (stuck→failed); domain job-lock; model-lock; arq queue → Tasks 2,3,4. ✅ (AC-4, AC-5.)
- API `POST /domains/{id}/ingest` → `job_id`; `POST /domains/{id}/init` (sync plan→topics→per-topic jobs); `POST /domains/{id}/sources` extended; `GET /jobs/{id}`, `/events` (SSE), `POST /jobs/{id}/cancel` → Tasks 5,6. ✅
- Web UI: Ingest action → job drawer (progress bar + live log via SSE + cancel); `/settings` Connection + models + dim with ALTER+reindex warning → Task 6. ✅
- Setup wizard saves connection + models + dim; managed migration creates `vector(dim)` + HNSW; api/worker pick up the encrypted key → Task 6 (AC-1). ✅
- Cancel mid-run leaves no partial article (two-session rollback) → Task 4 (AC-4). ✅
- Empty/garbage source → job fails cleanly with error in `jobs.error`, no article → Task 4 (`empty source` guard + loader `ValueError` → failed branch; AC-7). ✅
- Structured-output repair / JSON fallback proven in Plan 2A; exercised end-to-end via stub-LLM here (AC-6). ✅
- Co-occurrence + typed links proven in Plan 2C; the API path triggers them via `run_ingest` (AC-3). ✅

**Placeholder scan:** Task 6 Step 6 (web templates) and the `test_web_pages.py` assertion body are intentionally directed to follow the existing template/test patterns rather than re-deriving the full Jinja/login boilerplate already documented in the repo — the exact controls to assert (`hx-post=".../ingest"`, `id="job-drawer"`, dim-warning string) and the SSE/cancel HTMX wiring are given verbatim. All backend code (locks, progress, task, services, routers, SSE) is complete. Reviewers: the two HTML fill-ins are the only non-literal steps and are bounded to known patterns.

**Type consistency:** `enqueue_ingest` signature matches both `JobService.start_ingest`/`init_domain` call-sites and the `ingest_domain(ctx, job_id, domain_id, source_id, topic)` task signature; `JobService` monkeypatch seam (`paw.services.jobs.enqueue_ingest`) and the task `_build_providers` seam are the two test injection points; SSE `sse_events` consumes the `JobRepo` + `jobs.log` shape from Plan 2B; `embedding_dim` (Plan 2B) is asserted in the setup + e2e tests; terminal status strings (`succeeded`/`failed`/`cancelled`) match `JOB_STATUS` (Plan 2B).

**Cross-plan dependency order:** 2A → 2B → 2C → 2D. This plan's tests assume `tests/stubs.py` (2A), the migration + repos + managed dim (2B), and `run_ingest`/`build_structure_plan`/loaders (2C) are already implemented.
