---
title: "Phase 9b — Security hardening + remaining loaders + bulk upload"
phase: 9
sub_plan: 9b
state: reviewed
review:
  plan_hash: 057294251e79e473
  spec_hash: 25f8d2e8b94c05a4
  last_run: 2026-06-26
  phases:
    structure: { status: passed }
    coverage: { status: passed }
    dependencies: { status: passed }
    verifiability: { status: passed }
    consistency: { status: passed }
  findings:
    - { id: F-001, phase: consistency, severity: CRITICAL, section: "Task 7 Step 4", text: "Plan claimed _source_markdown threading 'ripples to exactly one caller' and proposed widening _build_providers' return tuple, but _build_providers has 4 callers (ingest_domain/fix_issues/format_articles/reindex_domain) each unpacking a 4-tuple — widening it breaks the other three.", fix: "Fetch pc with one local line inside ingest_domain only; do not change _build_providers arity.", verdict: fixed, verdict_at: 2026-06-26 }
    - { id: F-002, phase: consistency, severity: CRITICAL, section: "Task 4 Step 3", text: "Adding inspect_zip to the .docx branch breaks the pre-existing test_validate_source_accepts_docx_zip_magic, which passes truncated b'PK\\x03\\x04rest' (not a parseable zip) — failing the pytest CI gate.", fix: "Rewrite that test to use a real synthetic zip; add the edit to Task 4 Step 1.", verdict: fixed, verdict_at: 2026-06-26 }
    - { id: F-003, phase: consistency, severity: WARNING, section: "Task 7 Step 4", text: "pc typed as Any defeats mypy strict verification of build_vision_provider(pc, box).", fix: "Type pc: ProviderConfig and import it in tasks.py.", verdict: fixed, verdict_at: 2026-06-26 }
    - { id: F-004, phase: consistency, severity: WARNING, section: "Task 8 Step 3", text: "Interfaces prose says response key 'jobs' but BulkOut model + API test use 'job_ids' — internal contradiction.", fix: "Make the prose say job_ids to match BulkOut.", verdict: fixed, verdict_at: 2026-06-26 }
    - { id: F-005, phase: verifiability, severity: WARNING, section: "Task 8 Step 1", text: "test_bulk_rejects_zip_bomb asserts only pytest.raises(UploadRejected) without pinning which guard fired.", fix: "Add match='ratio' to pin the compression-ratio guard.", verdict: fixed, verdict_at: 2026-06-26 }
    - { id: F-006, phase: consistency, severity: WARNING, section: "Task 7 Step 1", text: "validate_url monkeypatch target relies on upload_url using a function-local import; fragile if changed.", fix: "Note that the local-import form must be kept.", verdict: fixed, verdict_at: 2026-06-26 }
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-9-ops-hardening-design.md
---

# Phase 9b — Security hardening + remaining loaders + bulk upload

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Implement tasks in order; each ends green (lint + type-check + the task's tests) and is committed independently.

**Goal:** Close the security baseline for ingest and add the last three loaders plus bulk upload. Specifically: (a) anti-zip-bomb + path-traversal guards on every ZIP-based upload, (b) an SSRF-safe URL fetcher for the url loader, (c) a finalized CSP, (d) `epub`, `url`, and `image` (Vision/OCR) loaders, with `VisionProvider.describe` implemented and wired through the factory, and (e) a bulk-zip upload endpoint that safely unpacks → registers many sources → enqueues one ingest job per source.

**Architecture:** New pure security modules — anti-zip-bomb lives in `security/uploads.py` (`inspect_zip`), SSRF lives in a dedicated `security/ssrf.py` (`validate_url` + `safe_get`). The sync bytes→markdown loader dispatch (`ingest/loaders/load_source`) gains `epub` and `image` keys; `url` is intentionally **not** part of the sync dispatch because it needs network I/O — it is handled at the source level via an async `load_url`. The ingest worker (`jobs/tasks.py::_source_markdown`) branches on `src.type == "url"` (fetch via `safe_get` from `src.url`) and `src.type == "image"` (OCR via the configured `VisionProvider`); all other types keep the existing `load_source(bytes, type)` path. `VisionProvider.describe` is implemented on `OpenAICompatProvider` (OpenAI chat-completions image input) and a `build_vision_provider` factory wires `pc.vision_model`. Bulk upload is a new `SourceService.upload_bulk` + `POST …/sources/bulk` endpoint that uses `inspect_zip` for safety and enqueues per-source ingest via the existing `JobService.start_ingest` seam.

**Tech Stack:** Python 3.12 · `uv` · FastAPI (async) · async SQLAlchemy 2.0 · PostgreSQL 16 + `pgvector` · `arq` worker · `httpx` (prod) · `ebooklib` · existing `trafilatura`/`markdownify`/`pymupdf`/`mammoth`/`nh3`/`openai` · `pytest` + `testcontainers`.

## Global Constraints

- **Dependency management is `uv`** — never call `pip`/`pytest` directly; go through `uv run`. Add deps with `uv add`.
- **CI gate (all three must pass):** `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`. Mirrors `.github/workflows/ci.yml`.
- **Service is the single commit boundary.** Repos and storage must never `commit()`; a service batches writes and commits once. `upload_bulk` registers all sources in one transaction and commits once, then enqueues jobs **after** the commit (jobs reference committed rows).
- **Errors:** raise `ProblemError(status, title, detail)` (RFC 9457 `application/problem+json`). Upload/SSRF rejections surface as `422` via the router mapping `UploadRejected`/`SsrfRejected` → `ProblemError(422)`.
- **Async everywhere** (`asyncpg`, `redis.asyncio`, `httpx.AsyncClient`); `pytest` runs `asyncio_mode = auto` so tests are plain `async def`.
- **Layering (no cycles):** `api`/`web` → `services` → `db.repos`, `storage` → `db`, `config`. `security/*` and `ingest/loaders/*` are leaf-ish: `security/ssrf.py` and `security/uploads.py` import only stdlib + `config`/`httpx`; loaders import only their parser libs + (for image) the `VisionProvider` Protocol. **Loaders must NOT import `paw.api` or `paw.services`.**
- **Pure, sync, bytes-based loader contract:** each bytes-based loader module exposes `def load(data: bytes) -> str`. The `url` loader is the documented exception: it exposes `async def load_url(...)` and is invoked from the worker, not from `load_source`.
- **Tests need Docker** for `integration`/`api`/`e2e` layers (real Postgres + Redis via testcontainers). The new pure guards (`inspect_zip`, `validate_url`, loaders with synthetic bytes, `VisionProvider.describe` against a fake OpenAI client) run as **unit** tests with no Docker.
- **No new core tables.** `Source.url` and `Source.type` already exist; `Source.type` is a free-text `Text` column (not an enum), so `"epub"/"url"/"image"` need no migration. New config knobs are env-only (`config.py`).
- **Branch workflow:** all work on a `dev-paw-9b-hardening` branch off up-to-date `master`; merge via PR. Never commit to `master`. Per-task commits as specified. Ask whether to create a `wk-dev-paw-9b-hardening` worktree before branching.
- **Docs are English; conversation is Russian.** After functional changes, update `docs/wiki/` via iwiki (final task).

## Reused building blocks (already in the codebase — do not reimplement)

- `paw.security.uploads`: `UploadRejected`, `validate_source_upload(filename, data, *, max_bytes) -> str` (returns a `kind`), `validate_text_upload`, magic constants `_PDF_MAGIC=b"%PDF-"`, `_ZIP_MAGIC=b"PK\x03\x04"`. We extend this module with `inspect_zip` + epub/image extensions.
- `paw.security.sanitize.render_markdown(text) -> str` (mistune + `nh3.clean`) — already applied at render time; loaders return **markdown**, not HTML. The `html` loader path (`trafilatura.extract(..., output_format="markdown")` + `markdownify` fallback) is reused by the `epub` and `url` loaders.
- `paw.ingest.loaders.load_source(data, source_type) -> str` — sync dispatch; raises `UnsupportedSource` for unknown types and `ValueError("source produced no extractable text")` on empty output. We add `epub`/`image` branches; `image` requires a Vision provider so it is **not** routed through `load_source` (which is bytes-only) — see Task 6.
- `paw.services.sources.SourceService(session)`: `upload_text`, `upload(*, domain_id, filename, data, content_type) -> Source` (validate → sha256 → `PostgresStorage.put(data, content_type=, large=len>256KB)` → `SourceRepo.create` → commit once), `list`. We add `upload_url` and `upload_bulk`.
- `paw.db.repos.sources.SourceRepo(session)`: `create(*, domain_id, storage_ref, filename, type, checksum) -> Source` (flush, no commit). **Note:** `create` does not currently accept `url`; we add an optional `url` param (Task 7).
- `paw.storage.postgres.PostgresStorage(session)`: `put(data, *, content_type=None, large=False) -> str`, `get(ref) -> bytes`. Used unchanged.
- `paw.services.jobs.JobService(session).start_ingest(*, domain_id, source_id) -> Job` — creates a `Job(kind="ingest")`, commits, then `enqueue_ingest`. Bulk upload reuses this exact seam (one job per source).
- `paw.jobs.queue.enqueue_ingest(...)` / `paw.jobs.tasks.ingest_domain` / `paw.jobs.tasks._source_markdown(session, source_id) -> str` — the worker materializes a source to markdown via `load_source(data, src.type)`. We branch `_source_markdown` for `url`/`image`.
- `paw.providers.base.VisionProvider` Protocol: `async def describe(self, image, *, prompt, model=None) -> str` (exists, unimplemented). `paw.providers.openai_compat.OpenAICompatProvider` (implements chat/stream/embed/structured). `paw.providers.config.ProviderConfig.vision_model: str | None`. `paw.providers.factory.build_chat_provider(pc, box)` / `build_embedding_provider`.
- `paw.providers.config.WikiConfig` — carries `reasoning_language` (used to phrase the OCR/description prompt) and `request_timeout_s`.
- `paw.config.get_settings()` (lru_cache singleton; reset by the `wired_settings` test fixture). `paw.security.secrets.SecretBox(fernet_key)`.
- `paw.api.deps`: `db`, `require_csrf`, `require_role`. `paw.api.errors.ProblemError`.
- Test stubs: `tests/stubs.py` (`StubChatProvider`, `StubEmbeddingProvider`). We add `StubVisionProvider`. CSRF test helper pattern (login → read `paw_csrf` cookie → send as `x-csrf-token` header on writes) — see `tests/api/`.

## File Structure

**Create:**
- `src/paw/security/ssrf.py` — `SsrfRejected`, `validate_url(url, *, allowlist) -> None`, `async safe_get(url, *, max_bytes, allowlist) -> bytes`. Pure (stdlib `ipaddress`/`socket` + `httpx`).
- `src/paw/ingest/loaders/epub.py` — `def load(data: bytes) -> str` (ebooklib → spine HTML → markdown).
- `src/paw/ingest/loaders/url.py` — `async def load_url(url, *, allowlist, max_bytes) -> str` (SSRF-guarded fetch → trafilatura → markdown).
- `src/paw/ingest/loaders/image.py` — `async def describe_image(data, vision, *, prompt) -> str`.
- `tests/unit/test_ssrf.py`, `tests/unit/test_zipbomb.py`, `tests/unit/test_vision_provider.py`.
- `tests/unit/test_loaders_extra.py` (epub + image unit; url uses a patched `safe_get`).
- `tests/integration/test_bulk_upload.py` (bulk zip → multiple sources + jobs).
- `tests/api/test_csp.py` (finalized CSP header on a real response).
- `tests/api/test_sources_bulk.py` (endpoint auth + rejection of a zip bomb / oversize).

**Modify:**
- `pyproject.toml` — add `ebooklib` (prod); move `httpx` to prod deps; add mypy override for `ebooklib.*`.
- `src/paw/config.py` — add `url_allowlist`, `max_url_bytes`, `max_unzip_bytes`, `max_unzip_entries`, `max_compression_ratio` + a parsed-allowlist helper.
- `src/paw/security/uploads.py` — `inspect_zip(...)`; add `.epub`/image extensions to `validate_source_upload`; call `inspect_zip` on docx/epub.
- `src/paw/ingest/loaders/__init__.py` — add `epub`/`image` dispatch keys (image raises a clear error in the sync path; see Task 6).
- `src/paw/providers/base.py` — (no change to the Protocol; already present) — confirm signature.
- `src/paw/providers/openai_compat.py` — implement `describe`.
- `src/paw/providers/factory.py` — add `build_vision_provider(pc, box) -> OpenAICompatProvider | None`.
- `src/paw/db/repos/sources.py` — `create(... , url: str | None = None)`.
- `src/paw/services/sources.py` — `upload_url(...)`, `upload_bulk(...)`.
- `src/paw/jobs/tasks.py` — `_source_markdown` branches for `url` and `image`.
- `src/paw/api/routers/sources.py` — `POST …/sources/bulk`; map `SsrfRejected` too.
- `src/paw/main.py` — finalize `_CSP`.
- `src/paw/api/web/templates/domain.html` — bulk-upload form (no inline JS).
- `tests/stubs.py` — `StubVisionProvider`.
- `docs/wiki/*` — refreshed via iwiki (final task).

---

### Task 1: Dependencies + config knobs

**Files:**
- Modify: `pyproject.toml`, `src/paw/config.py`
- Test: `tests/unit/test_config.py` (extend existing; if absent, create with just the new assertions)

**Interfaces:**
- Produces on `Settings`: `url_allowlist: str = ""`, `max_url_bytes: int = 5*1024*1024`, `max_unzip_bytes: int = 100*1024*1024`, `max_unzip_entries: int = 2000`, `max_compression_ratio: float = 100.0`.
- Produces helper `paw.config.parse_allowlist(raw: str) -> list[str]` — split on commas, strip, drop empties, lowercase host suffixes.

- [ ] **Step 1: Add deps**

Run:
```bash
uv add ebooklib
uv add httpx        # promotes httpx to [project.dependencies] (was dev-only)
```
Expected: `pyproject.toml` `[project].dependencies` gains `ebooklib` and `httpx`; `uv.lock` updates. (`httpx` may remain listed under dev too — that is harmless; the prod entry is what matters.)

- [ ] **Step 2: Silence mypy on the untyped ebooklib import**

Append to `pyproject.toml` after the existing `[[tool.mypy.overrides]]` block:
```toml
[[tool.mypy.overrides]]
module = ["ebooklib.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Write the failing config test**

Add to `tests/unit/test_config.py` (create the file if it does not exist; import `Settings` directly and set env via `monkeypatch`):
```python
from paw.config import Settings, parse_allowlist


def _base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("REDIS_URL", "redis://x")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("FERNET_KEY", "k" * 43 + "=")


def test_hardening_defaults(monkeypatch):
    _base_env(monkeypatch)
    s = Settings()
    assert s.url_allowlist == ""
    assert s.max_url_bytes == 5 * 1024 * 1024
    assert s.max_unzip_bytes == 100 * 1024 * 1024
    assert s.max_unzip_entries == 2000
    assert s.max_compression_ratio == 100.0


def test_url_allowlist_override(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("URL_ALLOWLIST", "example.com, Docs.RS ,")
    assert parse_allowlist(Settings().url_allowlist) == ["example.com", "docs.rs"]
```

- [ ] **Step 4: Run to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_allowlist'` (and/or attribute errors).

- [ ] **Step 5: Implement**

In `src/paw/config.py`, add the fields to `Settings` (after the existing limits block) and the helper at module level:
```python
    # hardening (env layer; LLD §11)
    url_allowlist: str = ""  # comma-separated host suffixes; "" = any public host
    max_url_bytes: int = 5 * 1024 * 1024
    max_unzip_bytes: int = 100 * 1024 * 1024
    max_unzip_entries: int = 2000
    max_compression_ratio: float = 100.0
```
```python
def parse_allowlist(raw: str) -> list[str]:
    """Split a comma-separated host-suffix allowlist into a normalized list."""
    return [p.strip().lower() for p in raw.split(",") if p.strip()]
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: PASS.

- [ ] **Step 7: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/paw/config.py tests/unit/test_config.py
git commit -m "feat(9b): add ebooklib/httpx deps and hardening config knobs"
```

---

### Task 2: Anti-zip-bomb guard (`inspect_zip`)

**Files:**
- Modify: `src/paw/security/uploads.py`
- Test: `tests/unit/test_zipbomb.py`

**Interfaces:**
- Produces `inspect_zip(data: bytes, *, max_total: int, max_entries: int, max_ratio: float) -> None` in `paw.security.uploads`. Raises `UploadRejected` on: not a valid zip; entry count > `max_entries`; cumulative `file_size` > `max_total`; any entry whose `file_size / max(compress_size, 1) > max_ratio` (decompression-ratio bomb); any entry name that is absolute (`startswith("/")` or has a Windows drive), contains a `..` path component, or is itself a nested zip/docx/epub (name ends `.zip`/`.docx`/`.epub`). Returns `None` when safe.
- **Design:** inspect metadata only via `zipfile.ZipFile(...).infolist()` — never call `.read()`/`.extractall()` (reading is what a bomb exploits). `file_size`/`compress_size` come from the central directory headers.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zipbomb.py`:
```python
import io
import zipfile

import pytest

from paw.security.uploads import UploadRejected, inspect_zip

_LIMITS = {"max_total": 10_000, "max_entries": 5, "max_ratio": 50.0}


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_accepts_small_safe_zip():
    inspect_zip(_zip({"a.txt": b"hello", "b.txt": b"world"}), **_LIMITS)  # no raise


def test_rejects_non_zip():
    with pytest.raises(UploadRejected):
        inspect_zip(b"not a zip at all", **_LIMITS)


def test_rejects_too_many_entries():
    many = {f"f{i}.txt": b"x" for i in range(6)}
    with pytest.raises(UploadRejected):
        inspect_zip(_zip(many), **_LIMITS)


def test_rejects_total_uncompressed_over_cap():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"big.txt": b"x" * 20_000}), **_LIMITS)


def test_rejects_high_compression_ratio():
    # ~1MB of zeros compresses to a few hundred bytes -> ratio >> 50, under the
    # 10_000-byte total cap is impossible, so raise total cap for this case.
    bomb = _zip({"z.bin": b"\x00" * 1_000_000})
    with pytest.raises(UploadRejected):
        inspect_zip(bomb, max_total=10_000_000, max_entries=5, max_ratio=50.0)


def test_rejects_path_traversal():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"../escape.txt": b"x"}), **_LIMITS)


def test_rejects_absolute_path():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"/etc/passwd": b"x"}), **_LIMITS)


def test_rejects_nested_zip():
    with pytest.raises(UploadRejected):
        inspect_zip(_zip({"inner.zip": b"PK\x03\x04"}), **_LIMITS)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_zipbomb.py -q`
Expected: FAIL — `ImportError: cannot import name 'inspect_zip'`.

- [ ] **Step 3: Implement**

In `src/paw/security/uploads.py`, add (after the magic constants):
```python
import zipfile

_NESTED_ARCHIVE_SUFFIXES = (".zip", ".docx", ".epub")


def inspect_zip(data: bytes, *, max_total: int, max_entries: int, max_ratio: float) -> None:
    """Metadata-only anti-zip-bomb / path-traversal guard.

    Never decompresses; reads only central-directory sizes. Raises UploadRejected.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UploadRejected("not a valid zip archive") from e
    infos = zf.infolist()
    if len(infos) > max_entries:
        raise UploadRejected("zip has too many entries")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise UploadRejected(f"absolute path in zip: {name}")
        if ".." in name.replace("\\", "/").split("/"):
            raise UploadRejected(f"path traversal in zip: {name}")
        if name.lower().endswith(_NESTED_ARCHIVE_SUFFIXES):
            raise UploadRejected(f"nested archive in zip: {name}")
        total += info.file_size
        if total > max_total:
            raise UploadRejected("zip uncompressed size over cap")
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > max_ratio:
            raise UploadRejected(f"suspicious compression ratio: {name}")
```
Add `import io` to the top of `uploads.py` if not already present (it is needed by `inspect_zip`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_zipbomb.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/security/uploads.py tests/unit/test_zipbomb.py
git commit -m "feat(9b): metadata-only anti-zip-bomb and path-traversal guard"
```

---

### Task 3: SSRF guard (`security/ssrf.py`)

**Files:**
- Create: `src/paw/security/ssrf.py`
- Test: `tests/unit/test_ssrf.py`

**Interfaces:**
- Produces `SsrfRejected(Exception)`.
- `validate_url(url: str, *, allowlist: list[str]) -> str` — parse with `urllib.parse.urlsplit`; raise unless scheme is `https`; extract host; if `allowlist` is non-empty, require the host to equal or end with `.<suffix>` for some suffix; resolve the host via `socket.getaddrinfo` and require **every** resolved IP to be public (reject `is_private`/`is_loopback`/`is_link_local`/`is_reserved`/`is_multicast`/`is_unspecified`, both IPv4 and IPv6, parsed via `ipaddress.ip_address`). Returns the validated host (the resolved-IP check is reused by `safe_get` per redirect hop).
- `async def safe_get(url: str, *, max_bytes: int, allowlist: list[str]) -> bytes` — `validate_url` the initial URL; fetch with `httpx.AsyncClient(follow_redirects=False)`; on a 3xx with a `Location`, resolve the next URL (join relative), `validate_url` it, and loop (cap hops at 5); stream the body and abort once more than `max_bytes` have been read (`raise SsrfRejected("response too large")`). Sets a short connect/read timeout. Raises `SsrfRejected` for non-2xx terminal responses.
- **DNS-rebinding note (documented limitation):** `validate_url` resolves and checks IPs, but `httpx` re-resolves at connect time — a TOCTOU window exists. For v1 this is the accepted posture (the spec asks for resolve-then-check + per-hop re-validation, which we do); pinning the connection to the validated IP is deferred (noted in Risks).

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_ssrf.py` (validation is tested directly; `safe_get` is tested with a `respx`-free monkeypatch of the client — but simplest is to monkeypatch `socket.getaddrinfo` for `validate_url`, and test `safe_get`'s size cap + redirect re-validation with a fake transport):
```python
import socket

import pytest

from paw.security.ssrf import SsrfRejected, validate_url


def _patch_resolve(monkeypatch, ip: str):
    def fake(host, *a, **k):
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, None, None, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_rejects_non_https(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(SsrfRejected):
        validate_url("http://example.com/x", allowlist=[])


def test_rejects_loopback(monkeypatch):
    _patch_resolve(monkeypatch, "127.0.0.1")
    with pytest.raises(SsrfRejected):
        validate_url("https://localhost.example/x", allowlist=[])


def test_rejects_private_ipv4(monkeypatch):
    _patch_resolve(monkeypatch, "10.0.0.5")
    with pytest.raises(SsrfRejected):
        validate_url("https://intranet.example/x", allowlist=[])


def test_rejects_link_local(monkeypatch):
    _patch_resolve(monkeypatch, "169.254.169.254")  # cloud metadata
    with pytest.raises(SsrfRejected):
        validate_url("https://metadata.example/x", allowlist=[])


def test_rejects_ipv6_ula(monkeypatch):
    _patch_resolve(monkeypatch, "fd00::1")
    with pytest.raises(SsrfRejected):
        validate_url("https://v6.example/x", allowlist=[])


def test_rejects_not_in_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(SsrfRejected):
        validate_url("https://evil.example/x", allowlist=["example.com"])


def test_accepts_public_in_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    assert validate_url("https://docs.example.com/x", allowlist=["example.com"]) == "docs.example.com"


def test_accepts_public_empty_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    assert validate_url("https://example.com/x", allowlist=[]) == "example.com"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_ssrf.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.security.ssrf'`.

- [ ] **Step 3: Implement**

Create `src/paw/security/ssrf.py`:
```python
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class SsrfRejected(Exception):
    pass


_MAX_HOPS = 5
_TIMEOUT = httpx.Timeout(5.0, connect=5.0)


def _ip_is_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str, *, allowlist: list[str]) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SsrfRejected("only https urls are allowed")
    host = parts.hostname
    if not host:
        raise SsrfRejected("url has no host")
    host = host.lower()
    if allowlist and not any(host == s or host.endswith("." + s) for s in allowlist):
        raise SsrfRejected(f"host not in allowlist: {host}")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SsrfRejected(f"dns resolution failed: {host}") from e
    if not infos:
        raise SsrfRejected(f"dns returned no addresses: {host}")
    for info in infos:
        ip = info[4][0]
        if _ip_is_blocked(ip):
            raise SsrfRejected(f"resolved to a blocked address: {ip}")
    return host


async def safe_get(url: str, *, max_bytes: int, allowlist: list[str]) -> bytes:
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT) as client:
        for _ in range(_MAX_HOPS):
            validate_url(current, allowlist=allowlist)
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        raise SsrfRejected("redirect without location")
                    current = urljoin(current, loc)
                    continue
                if resp.status_code // 100 != 2:
                    raise SsrfRejected(f"non-success status: {resp.status_code}")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise SsrfRejected("response too large")
                return bytes(buf)
    raise SsrfRejected("too many redirects")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_ssrf.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/security/ssrf.py tests/unit/test_ssrf.py
git commit -m "feat(9b): SSRF guard (https-only, IP deny-ranges, allowlist, size cap)"
```

---

### Task 4: Wire anti-zip-bomb into upload validation + epub extension

**Files:**
- Modify: `src/paw/security/uploads.py`
- Test: extend `tests/unit/test_uploads.py`

**Interfaces:**
- `validate_source_upload(filename, data, *, max_bytes) -> str` now accepts `.epub` → returns `"epub"` (ZIP magic + `inspect_zip`), and `.jpg/.jpeg/.png/.webp` → returns `"image"` (image magic-byte sniff). `.docx` and `.epub` both run `inspect_zip(data, max_total=..., max_entries=..., max_ratio=...)` using `get_settings()` limits **after** the ZIP magic check.
- Image magic bytes: JPEG `b"\xff\xd8\xff"`, PNG `b"\x89PNG\r\n\x1a\n"`, WEBP `RIFF....WEBP` (`data[:4] == b"RIFF" and data[8:12] == b"WEBP"`).
- **Note:** `validate_source_upload` gains a dependency on `get_settings()` for the zip limits. Import locally inside the function to keep the module import-light and avoid a config import at module load (mirrors how services import settings lazily).

- [ ] **Step 1: Write the failing tests + fix the now-invalid docx test**

First fix the **pre-existing** regression: `validate_source_upload` will now run `inspect_zip` on `.docx`, and the existing `test_validate_source_accepts_docx_zip_magic` passes a truncated `b"PK\x03\x04rest"` that is **not** a parseable zip — `inspect_zip` would reject it. Replace that test's payload with a real (synthetic) zip:
```python
import io
import zipfile


def _real_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_validate_source_accepts_docx_zip_magic():  # replaces the truncated-bytes version
    data = _real_zip({"word/document.xml": b"<w:document/>"})
    assert validate_source_upload("d.docx", data, max_bytes=1_000_000) == "docx"
```
(Delete the old `test_validate_source_accepts_docx_zip_magic` that used `b"PK\x03\x04rest"`.)

Then add the new epub/image tests:
```python
def _epub_bytes(n_entries: int = 2, body: bytes = b"<html></html>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        for i in range(n_entries):
            z.writestr(f"OEBPS/ch{i}.xhtml", body)
    return buf.getvalue()


def test_validate_source_accepts_epub():
    assert validate_source_upload("book.epub", _epub_bytes(), max_bytes=1_000_000) == "epub"


def test_validate_source_rejects_epub_bad_magic():
    with pytest.raises(UploadRejected):
        validate_source_upload("book.epub", b"not a zip", max_bytes=1_000_000)


def test_validate_source_accepts_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert validate_source_upload("scan.png", png, max_bytes=1_000_000) == "image"


def test_validate_source_accepts_jpeg():
    jpg = b"\xff\xd8\xff" + b"\x00" * 32
    assert validate_source_upload("scan.jpg", jpg, max_bytes=1_000_000) == "image"


def test_validate_source_rejects_image_bad_magic():
    with pytest.raises(UploadRejected):
        validate_source_upload("scan.png", b"GIF89a", max_bytes=1_000_000)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_uploads.py -q`
Expected: FAIL — `.epub`/image extensions hit the `extension not allowed` branch (and the rewritten docx test fails until `inspect_zip` accepts a real zip).

- [ ] **Step 3: Implement**

In `validate_source_upload` (before the final `raise`), add image extensions and epub, and run `inspect_zip` on zip-based kinds:
```python
_IMAGE_MAGIC = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (),  # checked specially (RIFF....WEBP)
}
```
```python
    if lower.endswith(".docx"):
        if not data.startswith(_ZIP_MAGIC):
            raise UploadRejected("not a valid DOCX (magic bytes)")
        _guard_zip(data)
        return "docx"
    if lower.endswith(".epub"):
        if not data.startswith(_ZIP_MAGIC):
            raise UploadRejected("not a valid EPUB (magic bytes)")
        _guard_zip(data)
        return "epub"
    for ext, magics in _IMAGE_MAGIC.items():
        if lower.endswith(ext):
            ok = (ext in (".jpg", ".jpeg", ".png") and data.startswith(magics[0])) or (
                ext == ".webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
            )
            if not ok:
                raise UploadRejected(f"not a valid image (magic bytes): {filename}")
            return "image"
```
Add the helper that pulls limits from settings:
```python
def _guard_zip(data: bytes) -> None:
    from paw.config import get_settings

    s = get_settings()
    inspect_zip(
        data,
        max_total=s.max_unzip_bytes,
        max_entries=s.max_unzip_entries,
        max_ratio=s.max_compression_ratio,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_uploads.py -q`
Expected: PASS (all old, the rewritten docx test, + 5 new).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/security/uploads.py tests/unit/test_uploads.py
git commit -m "feat(9b): accept epub/image uploads and zip-bomb-guard docx/epub"
```

---

### Task 5: epub loader

**Files:**
- Create: `src/paw/ingest/loaders/epub.py`
- Modify: `src/paw/ingest/loaders/__init__.py`
- Test: `tests/unit/test_loaders_extra.py` (epub portion)

**Interfaces:**
- Produces `paw.ingest.loaders.epub.load(data: bytes) -> str` — read with `ebooklib.epub.read_epub` from a temp file (ebooklib reads a path; write `data` to a `tempfile.NamedTemporaryFile`), iterate `book.get_items_of_type(ebooklib.ITEM_DOCUMENT)` in spine order, decode each item's HTML, convert to markdown by reusing the `html` loader's path (`trafilatura.extract(..., output_format="markdown")` fallback `markdownify`), and join with blank lines.
- `load_source` dispatch gains `elif t == "epub": from paw.ingest.loaders.epub import load`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loaders_extra.py` (epub portion; build a minimal valid epub inline):
```python
import io
import zipfile

import pytest

from paw.ingest.loaders import load_source


def _minimal_epub(chapter_html: str) -> bytes:
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier>'
        "<dc:title>T</dc:title><dc:language>en</dc:language></metadata>"
        '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"{chapter_html}</body></html>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch1.xhtml", chapter)
    return buf.getvalue()


def test_epub_extracts_spine_text():
    data = _minimal_epub("<h1>QUIC</h1><p>Fast transport protocol.</p>")
    out = load_source(data, "epub")
    assert "QUIC" in out
    assert "Fast transport" in out


def test_epub_empty_raises():
    with pytest.raises(ValueError):
        load_source(_minimal_epub("<body></body>"), "epub")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_loaders_extra.py -q -k epub`
Expected: FAIL — `UnsupportedSource` (dispatch not added) / module missing.

- [ ] **Step 3: Implement**

Create `src/paw/ingest/loaders/epub.py`:
```python
from __future__ import annotations

import tempfile

import ebooklib
from ebooklib import epub

from paw.ingest.loaders.html import load as html_to_md


def load(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
        tmp.write(data)
        tmp.flush()
        book = epub.read_epub(tmp.name)
    parts: list[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html_bytes = item.get_content()
        md = html_to_md(html_bytes).strip()
        if md:
            parts.append(md)
    return "\n\n".join(parts)
```
In `src/paw/ingest/loaders/__init__.py`, add to the dispatch chain:
```python
    elif t == "epub":
        from paw.ingest.loaders.epub import load
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_loaders_extra.py -q -k epub`
Expected: PASS (2 passed). If `read_epub` warns about NCX/opf, that is non-fatal; the text must still extract.

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/paw/ingest/loaders/epub.py src/paw/ingest/loaders/__init__.py tests/unit/test_loaders_extra.py
git commit -m "feat(9b): epub loader (ebooklib spine -> markdown)"
```

---

### Task 6: image loader + VisionProvider.describe + factory wiring

**Files:**
- Modify: `src/paw/providers/openai_compat.py`, `src/paw/providers/factory.py`, `src/paw/ingest/loaders/__init__.py`, `tests/stubs.py`
- Create: `src/paw/ingest/loaders/image.py`
- Test: `tests/unit/test_vision_provider.py`, extend `tests/unit/test_loaders_extra.py` (image portion)

**Interfaces:**
- `OpenAICompatProvider.describe(self, image: bytes, *, prompt: str, model: str | None = None) -> str` — base64-encode `image`, build a single user message with content parts `[{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]`, call `chat.completions.create(model=model or self.vision_model_or_chat, messages=[...])`, return `resp.choices[0].message.content or ""`. Store an optional `vision_model` on the provider (constructor gains `vision_model: str | None = None`); `describe` uses `model` arg → `self.vision_model` → `self.chat_model`.
- `build_vision_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider | None` — returns `None` when `pc.vision_model is None`; otherwise builds a provider carrying `vision_model=pc.vision_model`.
- `paw.ingest.loaders.image.describe_image(data: bytes, vision: VisionProvider, *, prompt: str) -> str` — thin wrapper calling `await vision.describe(data, prompt=prompt)` and returning the text (this is the OCR/description entry point; the worker calls it).
- `load_source` dispatch: `image` is **not** routed here (needs a Vision provider, which `load_source(bytes, type)` cannot supply). Add `elif t == "image": raise UnsupportedSource("image sources require the vision path (see jobs.tasks._source_markdown)")` so a stray sync call fails loudly instead of silently mis-dispatching.
- `tests/stubs.py` gains `StubVisionProvider` returning a fixed string (records the prompt).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_vision_provider.py` (fake OpenAI client capturing the request):
```python
from paw.providers.openai_compat import OpenAICompatProvider


class _FakeMsg:
    def __init__(self, content): self.content = content; self.tool_calls = None


class _FakeChoice:
    def __init__(self, content): self.message = _FakeMsg(content); self.finish_reason = "stop"


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]; self.usage = None


class _FakeCompletions:
    def __init__(self): self.captured = None

    async def create(self, **kwargs):
        self.captured = kwargs
        return _FakeResp("Extracted: hello sign")


class _FakeChat:
    def __init__(self, comp): self.completions = comp


class _FakeClient:
    def __init__(self): self.chat = _FakeChat(_FakeCompletions())


async def test_describe_builds_image_message():
    client = _FakeClient()
    p = OpenAICompatProvider(
        base_url="x", api_key="x", chat_model="c", embedding_model="e",
        vision_model="v", client=client,
    )
    out = await p.describe(b"\x89PNG\r\n\x1a\n imagebytes", prompt="Read the text")
    assert out == "Extracted: hello sign"
    msgs = client.chat.completions.captured["messages"]
    assert client.chat.completions.captured["model"] == "v"
    parts = msgs[0]["content"]
    assert parts[0]["text"] == "Read the text"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
```

Add the image portion to `tests/unit/test_loaders_extra.py`:
```python
from paw.ingest.loaders.image import describe_image
from tests.stubs import StubVisionProvider


async def test_describe_image_calls_vision():
    vis = StubVisionProvider(text="A photo of a server rack.")
    out = await describe_image(b"img", vis, prompt="Describe")
    assert "server rack" in out
    assert vis.prompts == ["Describe"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_vision_provider.py tests/unit/test_loaders_extra.py -q -k "describe or vision or image"`
Expected: FAIL — `describe` not implemented / `paw.ingest.loaders.image` missing / `StubVisionProvider` missing.

- [ ] **Step 3: Implement `describe` + vision_model on the provider**

In `OpenAICompatProvider.__init__`, add `vision_model: str | None = None` param and store `self.vision_model = vision_model`. Add the method:
```python
    async def describe(
        self, image: bytes, *, prompt: str, model: str | None = None
    ) -> str:
        import base64

        b64 = base64.b64encode(image).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ]
        resp = await self._client.chat.completions.create(
            model=model or self.vision_model or self.chat_model,
            messages=messages,
        )
        return resp.choices[0].message.content or ""
```

- [ ] **Step 4: Implement the factory + loader + stub**

In `src/paw/providers/factory.py`:
```python
def build_vision_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider | None:
    if pc.vision_model is None:
        return None
    return OpenAICompatProvider(
        base_url=pc.base_url,
        api_key=box.decrypt(pc.api_key_enc),
        chat_model=pc.chat_model,
        embedding_model=pc.embedding_model,
        vision_model=pc.vision_model,
    )
```
Create `src/paw/ingest/loaders/image.py`:
```python
from __future__ import annotations

from paw.providers.base import VisionProvider


async def describe_image(data: bytes, vision: VisionProvider, *, prompt: str) -> str:
    return await vision.describe(data, prompt=prompt)
```
In `src/paw/ingest/loaders/__init__.py`, add a loud guard for the sync path:
```python
    elif t == "image":
        raise UnsupportedSource(
            "image sources require the vision path (see jobs.tasks._source_markdown)"
        )
```
In `tests/stubs.py`:
```python
class StubVisionProvider:
    def __init__(self, text: str = "described") -> None:
        self._text = text
        self.prompts: list[str] = []

    async def describe(
        self, image: bytes, *, prompt: str, model: str | None = None
    ) -> str:
        self.prompts.append(prompt)
        return self._text
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_vision_provider.py tests/unit/test_loaders_extra.py -q`
Expected: PASS (vision + image + epub).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors. (Mypy must accept `OpenAICompatProvider` as a `VisionProvider` structurally — it now satisfies the Protocol.)

- [ ] **Step 7: Commit**

```bash
git add src/paw/providers/openai_compat.py src/paw/providers/factory.py \
        src/paw/ingest/loaders/image.py src/paw/ingest/loaders/__init__.py \
        tests/stubs.py tests/unit/test_vision_provider.py tests/unit/test_loaders_extra.py
git commit -m "feat(9b): VisionProvider.describe + image loader + vision factory wiring"
```

---

### Task 7: url loader + SourceRepo.url + SourceService.upload_url + worker branches

**Files:**
- Create: `src/paw/ingest/loaders/url.py`
- Modify: `src/paw/db/repos/sources.py`, `src/paw/services/sources.py`, `src/paw/jobs/tasks.py`
- Test: `tests/unit/test_loaders_extra.py` (url portion, patched `safe_get`); `tests/integration/test_sources_url.py`

**Interfaces:**
- `paw.ingest.loaders.url.load_url(url: str, *, allowlist: list[str], max_bytes: int) -> str` — `html_bytes = await safe_get(url, max_bytes=max_bytes, allowlist=allowlist)`; convert via the `html` loader path (decode + trafilatura/markdownify); raise `ValueError("url produced no extractable text")` if empty (mirrors `load_source`'s empty-guard so the worker fails the job cleanly).
- `SourceRepo.create(..., url: str | None = None)` — pass through to the `Source(url=url)` kwarg.
- `SourceService.upload_url(*, domain_id: uuid.UUID, url: str) -> Source` — `validate_url(url, allowlist=parse_allowlist(get_settings().url_allowlist))` up front (reject bad URLs before persisting); checksum the URL string (`sha256(url.encode())`) so the per-domain unique-checksum constraint dedupes the same URL; store a tiny placeholder blob (`put(url.encode(), content_type="text/uri-list")`) so `storage_ref` is non-null; `SourceRepo.create(..., filename=url, type="url", url=url, checksum=...)`; commit once.
- `_source_markdown(session, source_id)` in `jobs/tasks.py` branches:
  - `src.type == "url"` → `await load_url(src.url, allowlist=parse_allowlist(get_settings().url_allowlist), max_bytes=get_settings().max_url_bytes)` (raise if `src.url is None`).
  - `src.type == "image"` → build a Vision provider via `build_vision_provider(pc, box)` (raise a clear RuntimeError if `None` — vision_model not configured); `data = await PostgresStorage(session).get(src.storage_ref)`; `await describe_image(data, vision, prompt=...)` where the prompt is a fixed OCR/description instruction phrased in `wiki.reasoning_language`.
  - else → existing `load_source(data, src.type)`.
  - **Provider plumbing:** `_source_markdown` currently takes only `(session, source_id)`. Extend it to `_source_markdown(session, source_id, *, box, pc, wiki)` and pass the already-built `pc`/`wiki` from `ingest_domain` (it already calls `_build_providers`, which loads `pc` via `ProviderSettingsService.get_provider()`; thread `pc` and `wiki` through). Keep the non-url/image path unchanged.

- [ ] **Step 1: Write the failing tests**

Add the url portion to `tests/unit/test_loaders_extra.py` (patch `safe_get` so no network is hit):
```python
import paw.ingest.loaders.url as url_mod


async def test_load_url_extracts_markdown(monkeypatch):
    async def fake_get(u, *, max_bytes, allowlist):
        return b"<html><body><article><h1>QUIC</h1><p>Fast.</p></article></body></html>"
    monkeypatch.setattr(url_mod, "safe_get", fake_get)
    out = await url_mod.load_url("https://x.example/p", allowlist=[], max_bytes=1000)
    assert "QUIC" in out


async def test_load_url_empty_raises(monkeypatch):
    async def fake_get(u, *, max_bytes, allowlist):
        return b"<html><body></body></html>"
    monkeypatch.setattr(url_mod, "safe_get", fake_get)
    with pytest.raises(ValueError):
        await url_mod.load_url("https://x.example/p", allowlist=[], max_bytes=1000)
```

Create `tests/integration/test_sources_url.py`:
```python
import uuid

import pytest

from paw.db.repos.domains import DomainRepo
from paw.security.ssrf import SsrfRejected
from paw.services.sources import SourceService


async def _domain(db_session):
    return await DomainRepo(db_session).create(
        name=f"d-{uuid.uuid4().hex[:8]}", source_prefix="s", wiki_prefix="w"
    )


async def test_upload_url_rejects_http(db_session):
    dom = await _domain(db_session)
    await db_session.commit()
    with pytest.raises(SsrfRejected):
        await SourceService(db_session).upload_url(domain_id=dom.id, url="http://example.com/x")


async def test_upload_url_registers_source(db_session, monkeypatch):
    import paw.security.ssrf as ssrf
    monkeypatch.setattr(ssrf, "validate_url", lambda u, *, allowlist: "example.com")
    dom = await _domain(db_session)
    await db_session.commit()
    src = await SourceService(db_session).upload_url(
        domain_id=dom.id, url="https://example.com/page"
    )
    assert src.type == "url"
    assert src.url == "https://example.com/page"
```
(Confirm `DomainRepo.create` signature against the codebase when writing — adjust kwargs if it differs. The `validate_url` monkeypatch works because `upload_url` imports it function-locally via `from paw.security.ssrf import validate_url`, so patching `ssrf.validate_url` is re-read at call time — keep that local-import form.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_loaders_extra.py -q -k url`
Expected: FAIL — `paw.ingest.loaders.url` missing.

- [ ] **Step 3: Implement the loader + repo + service**

Create `src/paw/ingest/loaders/url.py`:
```python
from __future__ import annotations

from paw.ingest.loaders.html import load as html_to_md
from paw.security.ssrf import safe_get


async def load_url(url: str, *, allowlist: list[str], max_bytes: int) -> str:
    html_bytes = await safe_get(url, max_bytes=max_bytes, allowlist=allowlist)
    out = html_to_md(html_bytes).strip()
    if not out:
        raise ValueError("url produced no extractable text")
    return out
```
In `src/paw/db/repos/sources.py::create`, add `url: str | None = None` to the signature and `url=url` to the `Source(...)` constructor.

In `src/paw/services/sources.py`, add:
```python
    async def upload_url(self, *, domain_id: uuid.UUID, url: str) -> Source:
        from paw.config import parse_allowlist
        from paw.security.ssrf import validate_url

        allow = parse_allowlist(get_settings().url_allowlist)
        validate_url(url, allowlist=allow)  # reject before persisting
        checksum = hashlib.sha256(url.encode()).hexdigest()
        ref = await self._store.put(url.encode(), content_type="text/uri-list")
        src = await self._repo.create(
            domain_id=domain_id, storage_ref=ref, filename=url,
            type="url", url=url, checksum=checksum,
        )
        await self._s.commit()
        return src
```

- [ ] **Step 4: Branch the worker materializer**

In `src/paw/jobs/tasks.py`, change `_source_markdown` to accept `pc`/`wiki`/`box` and branch:
```python
async def _source_markdown(
    session: Any, source_id: str, *, box: SecretBox, pc: ProviderConfig, wiki: WikiConfig
) -> str:
    src = await SourceRepo(session).get(uuid.UUID(source_id))
    if src is None:
        raise RuntimeError("source not found")
    if src.type == "url":
        from paw.config import parse_allowlist
        from paw.ingest.loaders.url import load_url

        if not src.url:
            raise RuntimeError("url source missing url")
        s = get_settings()
        return await load_url(
            src.url, allowlist=parse_allowlist(s.url_allowlist), max_bytes=s.max_url_bytes
        )
    data = await PostgresStorage(session).get(src.storage_ref)
    if src.type == "image":
        from paw.ingest.loaders.image import describe_image
        from paw.providers.factory import build_vision_provider

        vision = build_vision_provider(pc, box)
        if vision is None:
            raise RuntimeError("vision_model not configured; cannot OCR image source")
        prompt = (
            "Transcribe all text in this image and briefly describe any diagrams. "
            f"Respond in {wiki.reasoning_language}."
        )
        return await describe_image(data, vision, prompt=prompt)
    return load_source(data, src.type)
```
Update the call site in `ingest_domain` to pass `pc`/`wiki`/`box`. **Important — do NOT change `_build_providers`'s return arity:** `_build_providers` has **four** callers in `tasks.py` (`ingest_domain` line ~95, `fix_issues` ~211, `format_articles` ~277, `reindex_domain` ~355), each unpacking a 4-tuple `(chat, embedder, wiki, dim)`. Adding a 5th element would break the other three with a too-many-values-to-unpack error (and a mypy arity error). Instead, fetch `pc` **locally in `ingest_domain` only**: it already builds a `ProviderSettingsService` indirectly via `_build_providers`, so add one line in `ingest_domain` after `_build_providers` returns —
```python
pc = await ProviderSettingsService(data_s, box=box).get_provider()
```
— (import `ProviderSettingsService` is already present in `tasks.py`) and pass `pc` into `_source_markdown(..., box=box, pc=pc, wiki=wiki)`. Add `from paw.providers.config import ProviderConfig` to the `tasks.py` imports for the new type annotation. Keep the non-url/image branch byte-for-byte identical.

- [ ] **Step 5: Run unit + integration**

Run: `uv run pytest tests/unit/test_loaders_extra.py -q -k url`
Expected: PASS.
Run: `uv run pytest tests/integration/test_sources_url.py -q`
Expected: PASS (needs Docker).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/paw/ingest/loaders/url.py src/paw/db/repos/sources.py \
        src/paw/services/sources.py src/paw/jobs/tasks.py \
        tests/unit/test_loaders_extra.py tests/integration/test_sources_url.py
git commit -m "feat(9b): url loader (SSRF-guarded fetch) + url source registration + worker branches"
```

---

### Task 8: Bulk upload (service + endpoint + UI)

**Files:**
- Modify: `src/paw/services/sources.py`, `src/paw/api/routers/sources.py`, `src/paw/api/web/templates/domain.html`
- Test: `tests/integration/test_bulk_upload.py`, `tests/api/test_sources_bulk.py`

**Interfaces:**
- `SourceService.upload_bulk(*, domain_id: uuid.UUID, zip_bytes: bytes) -> list[Source]` — (1) `inspect_zip(zip_bytes, max_total=…, max_entries=…, max_ratio=…)` (raises `UploadRejected`); (2) open `zipfile.ZipFile`, iterate entries skipping directories and the epub `mimetype`/`META-INF` housekeeping; for each entry read bytes, call `validate_source_upload(name, bytes, max_bytes=get_settings().max_upload_bytes)` (skip entries that raise `UploadRejected` — collect names of skipped, but a bomb already failed in step 1); checksum + `store.put(..., large=len>256KB)` + `repo.create(...)`; (3) **one** `self._s.commit()` after the loop. Returns the created `Source` rows. De-dup within the zip by checksum so the unique constraint never fires mid-loop (catch `IntegrityError`? no — pre-filter by tracking seen checksums in the batch).
- Endpoint `POST /api/v1/domains/{domain_id}/sources/bulk` (`require_csrf` + `require_role("admin","editor")`) — reads the uploaded zip `UploadFile`, calls `upload_bulk`, then for **each** created source calls `JobService(session).start_ingest(domain_id=domain_id, source_id=src.id)` (reuses the per-source ingest seam; each `start_ingest` commits its own `Job` and enqueues). Returns `{"sources": [...], "job_ids": [...]}` (201) via a `BulkOut` model. Maps `UploadRejected`/`SsrfRejected` → `ProblemError(422)`.
- **Atomicity note:** sources are registered in one transaction (`upload_bulk` commits once). Job creation/enqueue happens **after** that commit, one job per source, so a mid-enqueue failure leaves sources registered (re-ingestable) — acceptable and consistent with the existing single-source flow.
- UI: add a second form to `domain.html` posting `multipart/form-data` to `…/sources/bulk` with a file input (`name="file"`), HTMX `hx-post` + `hx-target="#job-drawer"`, CSRF via `hx-headers` (no inline `<script>` — matches the finalized CSP). The endpoint, for the web path, may return the `_job_drawer.html` partial wired to the **first** job (or a simple count message); keep it minimal — a small partial listing the enqueued job count is enough.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_bulk_upload.py`:
```python
import io
import uuid
import zipfile

import pytest

from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.security.uploads import UploadRejected
from paw.services.sources import SourceService


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


async def _domain(db_session):
    return await DomainRepo(db_session).create(
        name=f"d-{uuid.uuid4().hex[:8]}", source_prefix="s", wiki_prefix="w"
    )


async def test_bulk_registers_multiple_sources(db_session):
    dom = await _domain(db_session)
    await db_session.commit()
    z = _zip({"a.md": b"# A\n\nbody a", "b.md": b"# B\n\nbody b", "skip.exe": b"MZ"})
    srcs = await SourceService(db_session).upload_bulk(domain_id=dom.id, zip_bytes=z)
    assert {s.filename for s in srcs} == {"a.md", "b.md"}  # .exe skipped
    rows = await SourceRepo(db_session).list_by_domain(dom.id)
    assert len(rows) == 2


async def test_bulk_rejects_zip_bomb(db_session):
    dom = await _domain(db_session)
    await db_session.commit()
    bomb = _zip({"z.bin": b"\x00" * 5_000_000})  # ~5 MB zeros -> DEFLATE ratio >> 100
    with pytest.raises(UploadRejected, match="ratio"):  # pin the ratio guard specifically
        await SourceService(db_session).upload_bulk(domain_id=dom.id, zip_bytes=bomb)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_bulk_upload.py -q`
Expected: FAIL — `upload_bulk` missing.

- [ ] **Step 3: Implement `upload_bulk`**

In `src/paw/services/sources.py`:
```python
    async def upload_bulk(
        self, *, domain_id: uuid.UUID, zip_bytes: bytes
    ) -> list[Source]:
        import io
        import zipfile

        from paw.security.uploads import inspect_zip, validate_source_upload

        s = get_settings()
        inspect_zip(
            zip_bytes,
            max_total=s.max_unzip_bytes,
            max_entries=s.max_unzip_entries,
            max_ratio=s.max_compression_ratio,
        )
        created: list[Source] = []
        seen: set[str] = set()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name in ("mimetype",) or name.startswith("META-INF/"):
                    continue
                body = zf.read(info)  # safe: inspect_zip already bounded sizes
                try:
                    kind = validate_source_upload(name, body, max_bytes=s.max_upload_bytes)
                except UploadRejected:
                    continue  # skip unsupported entries silently
                checksum = hashlib.sha256(body).hexdigest()
                if checksum in seen:
                    continue
                seen.add(checksum)
                ref = await self._store.put(body, large=len(body) > 256 * 1024)
                src = await self._repo.create(
                    domain_id=domain_id, storage_ref=ref, filename=name,
                    type=kind, checksum=checksum,
                )
                created.append(src)
        await self._s.commit()
        return created
```
Add `from paw.security.uploads import UploadRejected` at module top if not already importable (it imports `validate_source_upload`/`validate_text_upload`; extend that import to include `UploadRejected` or import locally as shown).

- [ ] **Step 4: Implement the endpoint**

In `src/paw/api/routers/sources.py`, add (import `SsrfRejected`, `JobService`):
```python
class BulkOut(BaseModel):
    sources: list[SourceOut]
    job_ids: list[str]


@router.post(
    "/bulk",
    status_code=201,
    response_model=BulkOut,
    dependencies=[Depends(require_csrf), Depends(require_role("admin", "editor"))],
)
async def upload_bulk(
    domain_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
) -> BulkOut:
    data = await file.read()
    try:
        srcs = await SourceService(session).upload_bulk(domain_id=domain_id, zip_bytes=data)
    except (UploadRejected, SsrfRejected) as e:
        raise ProblemError(status=422, title="Bulk upload rejected", detail=str(e)) from e
    job_ids: list[str] = []
    for src in srcs:
        job = await JobService(session).start_ingest(domain_id=domain_id, source_id=src.id)
        job_ids.append(str(job.id))
    return BulkOut(
        sources=[SourceOut(id=str(s.id), filename=s.filename, type=s.type) for s in srcs],
        job_ids=job_ids,
    )
```

- [ ] **Step 5: Write the failing API test**

Create `tests/api/test_sources_bulk.py` (login → CSRF → post a zip; assert 201 + source count; post a bomb → 422). Reuse the existing API-test login/CSRF helper pattern in `tests/api/`. Assert that a `.exe` entry is skipped and that the response `job_ids` length equals the source count.

- [ ] **Step 6: Add the UI form (no inline JS)**

In `src/paw/api/web/templates/domain.html`, add inside `.content-header` after the ingest form:
```html
  <form hx-post="/api/v1/domains/{{ domain.id }}/sources/bulk"
        hx-headers='{"x-csrf-token": "{{ csrf }}"}' hx-target="#job-drawer"
        hx-swap="innerHTML" hx-encoding="multipart/form-data">
    <input type="file" name="file" accept=".zip">
    <button type="submit">⬆ Bulk upload (zip)</button>
  </form>
```
(The endpoint returns JSON; for the HTMX path the swap shows the raw JSON or you may add a tiny `_bulk_result.html` partial. Keep it minimal — the acceptance criterion is server-side behavior; a JSON swap is acceptable for v1.)

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/integration/test_bulk_upload.py tests/api/test_sources_bulk.py -q`
Expected: PASS (needs Docker).

- [ ] **Step 8: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/paw/services/sources.py src/paw/api/routers/sources.py \
        src/paw/api/web/templates/domain.html \
        tests/integration/test_bulk_upload.py tests/api/test_sources_bulk.py
git commit -m "feat(9b): bulk zip upload -> batch source registration + per-source ingest"
```

---

### Task 9: Finalize CSP + map SsrfRejected on the single-upload router

**Files:**
- Modify: `src/paw/main.py`, `src/paw/api/routers/sources.py`
- Test: `tests/api/test_csp.py`

**Interfaces:**
- `_CSP` becomes: `"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; object-src 'none'"`.
- The single-source `upload_source` handler also maps `SsrfRejected` → 422 (defense-in-depth; harmless for non-url uploads).

- [ ] **Step 1: Verify the HTMX UI has no inline handlers (manual grep)**

Run:
```bash
grep -rn "onclick\|onsubmit\|<script" src/paw/api/web/templates/ || echo "no inline JS"
```
Expected: only `<script src="…">` external references (if any) — **no** inline `on*=` attributes and no inline `<script>…</script>` bodies. HTMX attributes (`hx-*`) are HTML attributes, not inline scripts, and are unaffected by `script-src 'self'`. If any inline handler is found, it must be moved to an external static JS file before finalizing the CSP (record as a finding; do not silently weaken the CSP).

- [ ] **Step 2: Write the failing CSP test**

Create `tests/api/test_csp.py`:
```python
import httpx
import pytest
from httpx import ASGITransport


@pytest.fixture
def app(wired_settings):
    from paw.main import create_app
    return create_app()


async def test_csp_header_finalized(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/health")
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "object-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
```
(Confirm the app-construction/fixture pattern against an existing `tests/api/` file; reuse whatever fixture those tests use to build the app under `wired_settings`.)

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/api/test_csp.py -q`
Expected: FAIL — `frame-ancestors`/`form-action`/`object-src` absent.

- [ ] **Step 4: Implement**

In `src/paw/main.py`:
```python
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; "
    "form-action 'self'; object-src 'none'"
)
```
In `src/paw/api/routers/sources.py::upload_source`, widen the `except` to `(UploadRejected, SsrfRejected)` and import `SsrfRejected`.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/api/test_csp.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/paw/main.py src/paw/api/routers/sources.py tests/api/test_csp.py
git commit -m "feat(9b): finalize CSP (frame-ancestors/form-action/object-src) and map SsrfRejected"
```

---

### Task 10: Full CI + docs refresh + PR

**Files:**
- Modify: `docs/wiki/*` (regenerated via iwiki)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS — entire suite green (all 9b unit/integration/api tests included). On a machine without Docker, run at least `uv run pytest tests/unit -q` and note the integration/api/e2e layers must be verified where Docker is available.

- [ ] **Step 2: Run the complete CI gate**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -q`
Expected: all three pass (mirrors `.github/workflows/ci.yml`).

- [ ] **Step 3: Regenerate wiki pages for changed sources**

```
iwiki:iwiki-ingest src/paw/security/ssrf.py
iwiki:iwiki-ingest src/paw/security/uploads.py
iwiki:iwiki-ingest src/paw/ingest/loaders
iwiki:iwiki-ingest src/paw/providers/openai_compat.py
iwiki:iwiki-ingest src/paw/services/sources.py
iwiki:iwiki-ingest src/paw/jobs/tasks.py
```
Then run `/iwiki-lint`.
Expected: refreshed `security.md` / `ingest.md` (or equivalent) pages documenting SSRF, anti-zip-bomb, the three new loaders, Vision, and bulk upload; no broken `[[refs]]`, no orphans/stale pages.

- [ ] **Step 4: Commit docs**

```bash
git add docs/wiki
git commit -m "docs(wiki): document 9b hardening (SSRF/zip-bomb/CSP) + epub/url/image loaders + bulk upload"
```

- [ ] **Step 5: Open the PR**

Use **@skill:git-workflow** to push `dev-paw-9b-hardening` and open a PR into `master`. Summarize: anti-zip-bomb + path-traversal guard on docx/epub/bulk; SSRF-guarded url loader (https-only, IP deny-ranges, allowlist, size cap, per-hop redirect re-validation); finalized CSP; epub/url/image(OCR) loaders with `VisionProvider.describe` wired through the factory; bulk-zip upload registering many sources and enqueuing per-source ingest. After the PR is created, remove the worktree if one was used.

---

## Acceptance Criteria → Coverage Map

- **#5 URL loader rejects private/link-local/non-https targets + oversize bodies.** → Task 3 (`tests/unit/test_ssrf.py`: non-https, loopback, private v4, link-local `169.254.169.254`, IPv6 ULA, allowlist; `safe_get` size cap by design), Task 7 (`upload_url` validates before persisting; worker fetches via `safe_get`).
- **#5 A zip bomb is rejected.** → Task 2 (`tests/unit/test_zipbomb.py`: ratio/total/entries/traversal/nested), Task 4 (docx/epub run `inspect_zip`), Task 8 (`test_bulk_rejects_zip_bomb`).
- **#5 Non-allowlisted upload types refused.** → Task 4 (image/epub magic-byte sniff + `validate_source_upload` still rejects unknown extensions), existing `test_validate_source_rejects_unknown_ext`.
- **#6 epub, url, image (OCR) sources ingest into articles.** → Task 5 (epub `load_source`), Task 6 (`describe` + image loader + Vision wiring), Task 7 (url loader + worker `_source_markdown` branches feeding `run_ingest`); integration `test_sources_url.py`.
- **#6 bulk zip registers + ingests multiple sources.** → Task 8 (`upload_bulk` registers ≥2 sources; endpoint enqueues one ingest job per source via `JobService.start_ingest`).
- **Spec: finalize CSP (no inline-script, `frame-ancestors 'none'`, `form-action 'self'`, `object-src 'none'`).** → Task 9 (header assertions + grep verifying no inline handlers).

## Tests → Spec Map

- **Unit:** `test_ssrf.py` (allowlist/block decisions, https-only, IPv4+IPv6 ranges), `test_zipbomb.py` (anti-zip-bomb + traversal + nested), `test_uploads.py` (magic-byte sniff for epub/image), `test_vision_provider.py` (image-message shaping + model selection), `test_loaders_extra.py` (epub/url/image extraction), `test_config.py` (knobs + `parse_allowlist`). ✔ spec "SSRF allowlist/block decisions; anti-zip-bomb guard; magic-byte sniff".
- **Integration (testcontainers + stubs):** `test_sources_url.py` (url source registration + https rejection), `test_bulk_upload.py` (bulk registers multiple, rejects bomb). ✔ spec "epub/url/image loaders (stub Vision/httpx); bulk-zip ingest".
- **API (httpx):** `test_csp.py` (finalized CSP header), `test_sources_bulk.py` (endpoint auth + 422 on bomb/oversize + per-source jobs). ✔ spec "i18n/api-keys are 9c; CSP/upload surface here".

## Risks / Notes

- **SSRF TOCTOU (DNS rebinding):** `validate_url` resolves + checks IPs and `safe_get` re-validates each redirect hop, but `httpx` re-resolves at connect time, so a rebinding attacker has a narrow window. The spec requires resolve-then-check + per-hop re-validation (delivered); pinning the socket to the validated IP is deferred to backlog. Documented, not silently ignored.
- **`load_source` stays sync + bytes-only.** `url` is intentionally outside the dispatch (needs network) and `image` is guarded to raise loudly in the sync path (needs a Vision provider). Both are handled in the worker's `_source_markdown`, which is the one place that already has the DB session, `pc`, and `box`. This keeps the loader contract pure and avoids leaking network/provider deps into the dispatch.
- **`_source_markdown` signature change** ripples to exactly one caller (`ingest_domain`) — but `_build_providers` is shared by **four** worker tasks, so do NOT widen its return tuple (that would break `fix_issues`/`format_articles`/`reindex_domain`). Fetch `pc` with a single extra line inside `ingest_domain` instead. Keep the non-url/image branch byte-for-byte identical to avoid regressions in the existing pdf/docx/md/html path.
- **Bulk atomicity:** sources commit once; jobs enqueue after. A failure between source-commit and full enqueue leaves registered-but-not-ingested sources, which are safely re-ingestable. This mirrors the existing single-source flow (upload, then a separate "Ingest" action) and is acceptable for v1.
- **`inspect_zip` reads metadata only** (`infolist()` sizes from the central directory) — it never decompresses, so the guard itself cannot be turned into a bomb. `upload_bulk` only calls `zf.read()` **after** `inspect_zip` has bounded per-entry and total sizes.
- **Vision provider reuse:** `OpenAICompatProvider` now structurally satisfies the `VisionProvider` Protocol, so the same provider class serves chat/embed/vision. `build_vision_provider` returns `None` when `vision_model` is unset; the worker raises a clear error rather than silently producing empty OCR text.
- **CSP and HTMX:** HTMX uses `hx-*` HTML attributes, not inline JS, so `script-src 'self'` (no `'unsafe-inline'`) does not break it. The Task 9 grep is the gate that confirms no inline `on*=`/`<script>` bodies exist before tightening the header.
- **Tests need Docker** for the integration/api layers (real Postgres + Redis). Only the unit layer (the bulk of the adversarial security tests) runs without Docker, so the highest-risk logic is verifiable on any machine.
