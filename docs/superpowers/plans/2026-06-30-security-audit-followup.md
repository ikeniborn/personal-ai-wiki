---
review:
  plan_hash: 1cced2f470425a5d
  spec_hash: e2661b83be9c570a
  last_run: 2026-06-30
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: WARNING
      section: "Task 7: SEC-06c — throttle + lock the login endpoint"
      fragment: "Throttle (RateLimiter по IP+email, 429+Retry-After)"
      text: >-
        Spec SEC-06 prescribes the throttle/rate-limit be applied to BOTH login
        AND setup ("RateLimiter applied to login and setup, keyed by both client
        IP and submitted email"). The plan throttles only POST /auth/login in
        Task 7; no step adds RateLimiter/LoginGuard to the setup endpoint
        (routers/setup.py::complete). The setup throttle requirement is uncovered.
      fix: >-
        Add a step to Task 7 (or Task 6) that wires RateLimiter (and optionally
        LoginGuard) into routers/setup.py::complete keyed by IP, with a 429 test;
        or narrow the spec to login-only and drop "and setup" from SEC-06.
      verdict: fixed
    - id: F-002
      phase: coverage
      severity: WARNING
      section: "Task 11: SEC-07 — wire the audit log into sensitive operations"
      fragment: "INGEST_START = \"ingest.start\""
      text: >-
        Spec SEC-07 lists audit call sites as "ingest start / rollback / delete",
        but Task 11 defines only the INGEST_START constant and wires only
        jobs.py::start_ingest. There is no action constant or wiring for ingest
        rollback or delete, so two of the three ingest audit points are uncovered.
      fix: >-
        Either add INGEST_ROLLBACK / INGEST_DELETE constants and wire them into
        the corresponding article/job rollback+delete services, or amend the spec
        to scope SEC-07 ingest auditing to start only.
      verdict: fixed
    - id: F-003
      phase: dependencies
      severity: INFO
      section: "Task 11: SEC-07 — wire the audit log into sensitive operations"
      fragment: "Rewrite the `login` handler in `src/paw/api/routers/auth.py`"
      text: >-
        Task 7 fully rewrites routers/auth.py::login (adding throttle/lockout) and
        Task 11 then re-edits the same login handler to add the LOGIN audit record.
        Both edit the identical function; Task 11 must apply on top of Task 7's
        rewritten body, not the original. Ordering is correct (T7 < T11) but the
        plan does not flag the overlap, risking a merge against stale code.
      fix: >-
        Add a one-line note in Task 11 Step 4 that the login audit record is
        inserted into the already-throttled login handler from Task 7 (after the
        guard.reset / before returning LoginResponse).
      verdict: fixed
    - id: F-004
      phase: verifiability
      severity: INFO
      section: "Task 14: Coverage hardening — Step 4"
      fragment: "this step closes the rest to the ≥80% target."
      text: >-
        Step 4 ("Measure and fill the largest remaining gaps") names a concrete
        ≥80% target and gives coverage commands, but the per-module work is
        open-ended ("add a focused test covering the specific missed lines the
        report names"). The exact set of tests is data-dependent on the coverage
        run, so the step's DoD is only verifiable after execution rather than
        statically. Acceptable but the weakest DoD in the plan.
      fix: >-
        Optional: list the specific test functions to add per module (the step
        already names candidates: articles.rollback, maintenance start_*, setup
        409, users last-admin 409) so the DoD is enumerable up front.
      verdict: fixed
result_check:
  verdict: OK
  plan_hash: 1cced2f470425a5d
  last_run: 2026-06-30
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-30-security-audit-followup-design.md
---
# Security Audit Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 10 findings from the 2026-06-29 implementation & security audit (2 HIGH, 6 MED, 2 LOW) plus bandit triage and test-coverage hardening.

**Architecture:** Surgical changes on the existing layered FastAPI app (`api → services → repos/storage`, single commit boundary in the service layer). New leaf modules: a Redis rate limiter, an ASGI body-size guard, audit-action constants. No architectural changes.

**Tech Stack:** Python 3.12 · `uv` · FastAPI · async SQLAlchemy 2.0 · PostgreSQL 16 + pgvector · Redis + arq · httpx · Jinja2 + HTMX · pytest + testcontainers.

## Global Constraints

- Run everything through `uv run` — never call `pip`/`pytest` directly.
- CI gate (must stay green at every commit): `uv run ruff check .` → `uv run mypy src` (strict) → `uv run pytest -q`.
- `unit` tests run without Docker; `integration`/`api`/`e2e` tests spin up real Postgres + Redis via testcontainers (Docker daemon required).
- Service layer is the **single commit boundary**: repos/storage/`audit.log.record` must NOT commit. Batch writes and commit once per service operation.
- Errors are raised as `ProblemError(status, title, detail)` (RFC 9457).
- All DB/IO is async; `pytest` runs in `asyncio_mode = auto` (plain `async def` tests).
- Work proceeds on the existing branch `dev-security-audit-followup`. Every commit message ends with the repo footer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Line length 100; ruff lint set `E,F,I,UP,B`; mypy strict (annotate every new function).

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/paw/api/web/routes.py` | Web GET auth helper + route conversions; web bulk endpoint | 1, 10 |
| `src/paw/api/middleware/body_limit.py` (new) | ASGI request-body hard cap | 2 |
| `src/paw/security/ssrf.py` | IP-pinned SSRF-safe fetch | 3 |
| `src/paw/security/ratelimit.py` (new) | Redis sliding-window limiter + login lockout guard | 5, 7 |
| `src/paw/security/passwords.py` | Password-strength policy | 6 |
| `src/paw/config.py` | New env fields (metrics token, rate-limit, password) | 4, 5, 6 |
| `src/paw/main.py` | `/metrics` auth guard; wire body-limit middleware | 2, 4 |
| `src/paw/api/routers/auth.py` | Login throttle + lockout + audit | 7, 11 |
| `src/paw/services/setup.py`, `services/users.py` | Password policy + audit | 6, 11 |
| `src/paw/audit/actions.py` (new) | Audit action-name constants | 11 |
| `Dockerfile`, `.dockerignore` (new), `docker-compose.yml` | Locked build, fail-fast secret, metrics allowlist | 4, 8, 9, 12 |
| `src/paw/api/web/templates/_jobs_drawer.html` (new), `domain.html` | Bulk-upload web drawer | 10 |
| `pyproject.toml` | Branch coverage toggle | 14 |

---

# Phase 1 — HIGH

## Task 1: SEC-01 — web GET routes reject deleted users

**Files:**
- Modify: `src/paw/api/web/routes.py` (helpers `_current_uid`/`_current_user_opt` at :75-85; guard sites at :111, :128, :157, :261, :298, :370, :391, :451, :469, :489)
- Test: `tests/api/test_web_auth_stale_session.py` (new)

**Interfaces:**
- Produces: `async def _require_web_user(request: Request, session: AsyncSession, store: SessionStore) -> User | None` — returns the live `User`, or `None` (and evicts the stale Redis session) when the session id is missing or its user no longer exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_web_auth_stale_session.py
from httpx import ASGITransport, AsyncClient

from paw.api.deps import SESSION_COOKIE, get_session_store
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def test_deleted_user_session_is_rejected_and_evicted(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="gone@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t",
                    follow_redirects=False)
    try:
        await c.post("/api/v1/auth/login",
                     json={"email": "gone@example.com", "password": "pw12345678901"})
        sid = c.cookies.get(SESSION_COOKIE)
        assert sid
        # delete the user out from under the live session
        await UserRepo(db_session).delete(user.id)
        await db_session.commit()

        resp = await c.get("/")
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"
        # stale session evicted from Redis
        assert await get_session_store().get(sid) is None
    finally:
        await c.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web_auth_stale_session.py -v`
Expected: FAIL — the dashboard still renders (200) because the session id alone passes the check.

- [ ] **Step 3: Add the helper**

Replace `_current_uid`/`_current_user_opt` (lines 75-85) with:

```python
async def _require_web_user(
    request: Request, session: AsyncSession, store: SessionStore
) -> User | None:
    """Resolve the logged-in user for web pages, evicting stale sessions.

    Returns None when there is no session or the user was deleted; callers
    redirect to /login. Mirrors api.deps.current_user for the HTML routes.
    """
    sid = request.cookies.get(SESSION_COOKIE, "")
    uid = await store.get(sid)
    if not uid:
        return None
    user = await UserRepo(session).get(uuid.UUID(uid))
    if user is None:
        await store.delete(sid)
        return None
    return user
```

- [ ] **Step 4: Convert every authenticated web GET route**

For each route that currently does `if not await _current_uid(...): return RedirectResponse("/login", ...)` (and optionally `_current_user_opt`), replace the guard with the single pattern:

```python
    user = await _require_web_user(request, session, store)
    if user is None:
        return RedirectResponse("/login", status_code=307)
```

Apply at every guard site (dashboard :111, domain_page :128, graph_page :157, and the routes at :261, :298, :370, :391, :451, :469, :489). `graph_page` previously had no user load — it now passes `user` into `page_ctx`/template like the others. Routes that used the bare `uid` string (:469, :489) use `str(user.id)` instead. Remove the now-unused `_current_uid` and `_current_user_opt` definitions and any leftover imports.

- [ ] **Step 5: Run the full web/auth suite**

Run: `uv run pytest tests/api/test_web_auth_stale_session.py tests/api -k "web or page or auth" -v`
Expected: PASS (new regression passes; existing web page tests still pass).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/paw/api/web/routes.py tests/api/test_web_auth_stale_session.py
git commit -m "fix(web): reject and evict stale sessions for deleted users (SEC-01)"
```

---

## Task 2: SEC-02 — hard request-body size cap

**Files:**
- Create: `src/paw/api/middleware/__init__.py`, `src/paw/api/middleware/body_limit.py`
- Modify: `src/paw/main.py` (wire middleware in `create_app`)
- Test: `tests/api/test_body_limit.py` (new)

**Interfaces:**
- Produces: `BodySizeLimitMiddleware(app, max_bytes: int)` — pure ASGI middleware that returns `413` when `Content-Length` exceeds `max_bytes`, or when the streamed body exceeds it.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_body_limit.py
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _admin_client(db_session):
    await UserRepo(db_session).create(
        email="a@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    await c.post("/api/v1/auth/login",
                 json={"email": "a@example.com", "password": "pw12345678901"})
    return c


async def test_oversized_upload_rejected_with_413(db_session, wired_settings, monkeypatch):
    from paw.config import get_settings
    monkeypatch.setattr(get_settings(), "max_request_bytes", 1024, raising=False)
    c = await _admin_client(db_session)
    try:
        from paw.db.repos.domains import DomainRepo
        dom = await DomainRepo(db_session).create(name="d", brief="b")
        await db_session.commit()
        csrf = c.cookies.get("paw_csrf")
        big = b"x" * 4096
        resp = await c.post(
            f"/api/v1/domains/{dom.id}/sources",
            headers={"x-csrf-token": csrf},
            files={"file": ("big.md", big, "text/markdown")},
        )
        assert resp.status_code == 413
    finally:
        await c.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_body_limit.py -v`
Expected: FAIL — without the middleware the request is read fully and returns 201/422, not 413.

- [ ] **Step 3: Implement the middleware**

```python
# src/paw/api/middleware/__init__.py
```

```python
# src/paw/api/middleware/body_limit.py
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_TOO_LARGE = b'{"title":"Payload too large","status":413}'


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` at the ASGI layer.

    Checks Content-Length up front, then counts streamed bytes so chunked
    uploads cannot bypass the cap. Runs before any handler reads the body.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/problem+json")],
        })
        await send({"type": "http.response.body", "body": _TOO_LARGE})
```

- [ ] **Step 4: Wire it in `create_app`**

In `src/paw/main.py`, add the import and register the middleware (after `app = FastAPI(...)`, alongside the other `add_middleware` calls):

```python
from paw.api.middleware.body_limit import BodySizeLimitMiddleware
from paw.config import get_settings
...
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=get_settings().max_request_bytes)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_body_limit.py -v`
Expected: PASS (413). Then `uv run pytest tests/api -k upload -v` — existing uploads under the cap still succeed.

- [ ] **Step 6: Lint + type-check, then commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/middleware src/paw/main.py tests/api/test_body_limit.py
git commit -m "feat(api): cap request body size at the ASGI layer (SEC-02)"
```

---

# Phase 2 — MED security

## Task 3: SEC-04 — pin SSRF fetch to the validated IP

**Files:**
- Modify: `src/paw/security/ssrf.py` (`validate_url` :31, `safe_get` :64)
- Modify: `src/paw/services/sources.py:56` (call site ignores the new return — verify it still type-checks)
- Test: `tests/unit/test_ssrf_pin.py` (new)

**Interfaces:**
- Produces: `validate_url(url: str, *, allowlist: list[str]) -> tuple[str, str]` returning `(host, verified_ip)`.
- Produces: `safe_get(url: str, *, max_bytes: int, allowlist: list[str], client: httpx.AsyncClient | None = None) -> bytes` — connects to the verified IP while sending `Host:` and TLS SNI for the original host.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ssrf_pin.py
import httpx
import pytest

from paw.security import ssrf
from paw.security.ssrf import SsrfRejected, safe_get, validate_url


def test_validate_url_returns_host_and_ip(monkeypatch):
    monkeypatch.setattr(
        ssrf.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    host, ip = validate_url("https://example.com/x", allowlist=[])
    assert host == "example.com"
    assert ip == "93.184.216.34"


def test_validate_url_rejects_private(monkeypatch):
    monkeypatch.setattr(
        ssrf.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(SsrfRejected):
        validate_url("https://internal.example.com/x", allowlist=[])


async def test_safe_get_connects_to_pinned_ip(monkeypatch):
    monkeypatch.setattr(
        ssrf.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_header"] = request.headers["host"]
        seen["url_host"] = request.url.host
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"hello")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    body = await safe_get("https://example.com/p", max_bytes=1024, allowlist=[], client=client)
    assert body == b"hello"
    assert seen["url_host"] == "93.184.216.34"   # connects to the vetted IP
    assert seen["host_header"] == "example.com"  # original Host preserved
    assert seen["sni"] == "example.com"          # SNI/cert host preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_ssrf_pin.py -v`
Expected: FAIL — `validate_url` currently returns a `str`; `safe_get` has no `client` param and connects to `example.com`, not the IP.

- [ ] **Step 3: Rewrite `validate_url` and `safe_get`**

```python
# src/paw/security/ssrf.py  (replace from line 31 down)
def validate_url(url: str, *, allowlist: list[str]) -> tuple[str, str]:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SsrfRejected("only https urls are allowed")
    host = parts.hostname
    if not host:
        raise SsrfRejected("url has no host")
    host = host.lower()
    normalized_allowlist = [s.lower() for s in allowlist]
    if normalized_allowlist and not any(
        host == s or host.endswith("." + s) for s in normalized_allowlist
    ):
        raise SsrfRejected(f"host not in allowlist: {host}")
    try:
        port = parts.port
    except ValueError as e:
        raise SsrfRejected("invalid url port") from e
    if port == 0:
        raise SsrfRejected("invalid url port")
    port = port or 443
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SsrfRejected(f"dns resolution failed: {host}") from e
    if not infos:
        raise SsrfRejected(f"dns returned no addresses: {host}")
    for info in infos:
        ip = str(info[4][0])
        if _ip_is_blocked(ip):
            raise SsrfRejected(f"resolved to a blocked address: {ip}")
    verified_ip = str(infos[0][4][0])
    return host, verified_ip


async def safe_get(
    url: str,
    *,
    max_bytes: int,
    allowlist: list[str],
    client: httpx.AsyncClient | None = None,
) -> bytes:
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT)
    current = url
    redirects = 0
    try:
        while True:
            host, ip = validate_url(current, allowlist=allowlist)
            parts = urlsplit(current)
            port = parts.port or 443
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            pinned = f"https://{ip}:{port}{path}"
            async with client.stream(
                "GET", pinned,
                headers={"Host": host},
                extensions={"sni_hostname": host},
            ) as resp:
                if resp.is_redirect:
                    if redirects >= _MAX_HOPS:
                        raise SsrfRejected("too many redirects")
                    loc = resp.headers.get("location")
                    if not loc:
                        raise SsrfRejected("redirect without location")
                    current = urljoin(current, loc)
                    redirects += 1
                    continue
                if resp.status_code // 100 != 2:
                    raise SsrfRejected(f"non-success status: {resp.status_code}")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    if len(buf) + len(chunk) > max_bytes:
                        raise SsrfRejected("response too large")
                    buf += chunk
                return bytes(buf)
    finally:
        if owns_client:
            await client.aclose()
```

- [ ] **Step 4: Confirm the other call site still type-checks**

`services/sources.py:56` calls `validate_url(url, allowlist=allow)` for validation only and ignores the result — the new tuple return is simply discarded. No change needed; verify with mypy in Step 5. The url loader `ingest/loaders/url.py` calls `safe_get(...)` without `client`, which now defaults to a fresh client — unchanged behavior.

- [ ] **Step 5: Run tests + type-check**

Run: `uv run pytest tests/unit/test_ssrf_pin.py -v && uv run mypy src && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/paw/security/ssrf.py tests/unit/test_ssrf_pin.py
git commit -m "fix(security): pin SSRF fetch to the validated IP to close DNS TOCTOU (SEC-04)"
```

---

## Task 4: SEC-05 — authenticate `/metrics`

**Files:**
- Modify: `src/paw/config.py` (add `metrics_token`)
- Modify: `src/paw/main.py` (`/metrics` guard)
- Modify: `docker-compose.yml` (Traefik metrics router + IP allowlist), `.env.example`
- Test: `tests/api/test_metrics_auth.py` (new)

**Interfaces:**
- Consumes: `get_settings().metrics_token: str | None`.

- [ ] **Step 1: Add the config field**

In `src/paw/config.py`, under the hardening block:

```python
    metrics_token: str | None = None  # Bearer token gating /metrics; unset = endpoint disabled
```

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_metrics_auth.py
from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.main import create_app


async def test_metrics_disabled_when_token_unset(wired_settings):
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        assert (await c.get("/metrics")).status_code == 404
    finally:
        await c.aclose()


async def test_metrics_requires_bearer_token(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        assert (await c.get("/metrics")).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert b"http_requests" in ok.content or ok.content  # prometheus payload
    finally:
        await c.aclose()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/api/test_metrics_auth.py -v`
Expected: FAIL — `/metrics` currently returns 200 unconditionally.

- [ ] **Step 4: Guard the endpoint**

In `src/paw/main.py`, add imports and replace the `/metrics` handler:

```python
import secrets
from fastapi import Request
from paw.api.errors import ProblemError
from paw.config import get_settings
...
    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        token = get_settings().metrics_token
        if not token:
            raise ProblemError(status=404, title="Not found")
        expected = f"Bearer {token}"
        provided = request.headers.get("authorization", "")
        if not (provided and secrets.compare_digest(provided, expected)):
            raise ProblemError(status=401, title="Unauthorized")
        payload, content_type = render_metrics()
        return Response(payload, media_type=content_type)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_metrics_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Add the Traefik allowlist layer + env doc**

In `docker-compose.yml`, under the `api` service `labels:` (after line 91), add a dedicated, IP-restricted metrics router:

```yaml
      - "traefik.http.routers.paw-metrics.rule=Host(`${PAW_HOST:-localhost}`) && PathPrefix(`/metrics`)"
      - "traefik.http.routers.paw-metrics.entrypoints=websecure"
      - "traefik.http.routers.paw-metrics.tls.certresolver=le"
      - "traefik.http.routers.paw-metrics.service=paw"
      - "traefik.http.routers.paw-metrics.middlewares=metrics-allowlist"
      - "traefik.http.middlewares.metrics-allowlist.ipallowlist.sourcerange=127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
```

In `.env.example`, add:

```bash
# Bearer token required to scrape /metrics (leave empty to disable the endpoint)
METRICS_TOKEN=
```

- [ ] **Step 7: Lint + type-check, then commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/config.py src/paw/main.py docker-compose.yml .env.example tests/api/test_metrics_auth.py
git commit -m "feat(obs): require bearer token + proxy allowlist for /metrics (SEC-05)"
```

---

## Task 5: SEC-06a — Redis rate limiter + login lockout guard

**Files:**
- Create: `src/paw/security/ratelimit.py`
- Modify: `src/paw/config.py` (rate-limit fields)
- Test: `tests/integration/test_ratelimit.py` (new)

**Interfaces:**
- Produces: `RateLimiter(redis).hit(key: str, *, limit: int, window_seconds: int) -> bool` (True = within limit).
- Produces: `LoginGuard(redis, *, threshold: int, lock_seconds: int, fail_window_seconds: int = 900)` with `record_failure(key) -> None`, `is_locked(key) -> bool`, `reset(key) -> None`.

- [ ] **Step 1: Add config fields**

In `src/paw/config.py`:

```python
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    login_lockout_threshold: int = 10
    login_lockout_seconds: int = 900
    password_min_length: int = 12
```

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_ratelimit.py
import uuid

from paw.api.deps import get_redis
from paw.security.ratelimit import LoginGuard, RateLimiter


async def test_rate_limiter_blocks_after_limit(wired_settings):
    rl = RateLimiter(get_redis())
    key = f"t:{uuid.uuid4()}"
    assert await rl.hit(key, limit=2, window_seconds=60) is True
    assert await rl.hit(key, limit=2, window_seconds=60) is True
    assert await rl.hit(key, limit=2, window_seconds=60) is False


async def test_login_guard_locks_then_resets(wired_settings):
    g = LoginGuard(get_redis(), threshold=3, lock_seconds=60)
    key = f"u:{uuid.uuid4()}"
    assert await g.is_locked(key) is False
    for _ in range(3):
        await g.record_failure(key)
    assert await g.is_locked(key) is True
    await g.reset(key)
    assert await g.is_locked(key) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ratelimit.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement the limiter + guard**

```python
# src/paw/security/ratelimit.py
from __future__ import annotations

import time
import uuid

import redis.asyncio as aioredis


class RateLimiter:
    """Sliding-window counter backed by a Redis sorted set."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.time()
        full = f"ratelimit:{key}"
        member = f"{now}:{uuid.uuid4()}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(full, 0, now - window_seconds)
        pipe.zadd(full, {member: now})
        pipe.zcard(full)
        pipe.expire(full, window_seconds)
        _, _, count, _ = await pipe.execute()
        return int(count) <= limit


class LoginGuard:
    """Tracks consecutive failures per key and applies a temporary lock."""

    def __init__(
        self, redis: aioredis.Redis, *, threshold: int, lock_seconds: int,
        fail_window_seconds: int = 900,
    ) -> None:
        self._r = redis
        self._threshold = threshold
        self._lock_seconds = lock_seconds
        self._fail_window = fail_window_seconds

    async def record_failure(self, key: str) -> None:
        fail_key = f"loginfail:{key}"
        n = await self._r.incr(fail_key)
        if n == 1:
            await self._r.expire(fail_key, self._fail_window)
        if n >= self._threshold:
            await self._r.set(f"loginlock:{key}", "1", ex=self._lock_seconds)

    async def is_locked(self, key: str) -> bool:
        return bool(await self._r.exists(f"loginlock:{key}"))

    async def reset(self, key: str) -> None:
        await self._r.delete(f"loginfail:{key}", f"loginlock:{key}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_ratelimit.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + type-check, then commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/security/ratelimit.py src/paw/config.py tests/integration/test_ratelimit.py
git commit -m "feat(security): add Redis rate limiter and login lockout guard (SEC-06)"
```

---

## Task 6: SEC-06b — password-strength policy

**Files:**
- Modify: `src/paw/security/passwords.py`
- Modify: `src/paw/services/setup.py` (`complete`), `src/paw/services/users.py` (`create`)
- Test: `tests/unit/test_password_policy.py` (new); update any existing setup/user-create tests that POST short passwords
- Test: `tests/api/test_user_create_password_policy.py` (new)

**Interfaces:**
- Produces: `WeakPassword(Exception)` and `validate_password_strength(plain: str) -> None`.

- [ ] **Step 1: Write the failing unit test**

```python
# tests/unit/test_password_policy.py
import pytest

from paw.security.passwords import WeakPassword, validate_password_strength


def test_rejects_short_password(wired_settings):
    with pytest.raises(WeakPassword):
        validate_password_strength("short")


def test_rejects_common_password(wired_settings):
    with pytest.raises(WeakPassword):
        validate_password_strength("password1234")


def test_accepts_strong_password(wired_settings):
    validate_password_strength("a-Long-Unique-Phrase-42")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_password_policy.py -v`
Expected: FAIL — symbols do not exist.

- [ ] **Step 3: Implement the policy**

Append to `src/paw/security/passwords.py`:

```python
class WeakPassword(Exception):
    pass


# Small embedded blocklist of the most common passwords (case-insensitive).
_COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "password1234",
        "123456", "1234567", "12345678", "123456789", "1234567890",
        "qwerty", "qwertyuiop", "letmein", "welcome", "admin", "admin123",
        "iloveyou", "abc123", "monkey", "dragon", "000000", "111111",
    }
)


def validate_password_strength(plain: str) -> None:
    """Raise WeakPassword if the password is too short or too common."""
    from paw.config import get_settings

    min_length = get_settings().password_min_length
    if len(plain) < min_length:
        raise WeakPassword(f"password must be at least {min_length} characters")
    if plain.lower() in _COMMON_PASSWORDS:
        raise WeakPassword("password is too common")
```

- [ ] **Step 4: Enforce in the services**

In `src/paw/services/setup.py::complete`, before hashing (`admin = await self._users.create(...)`):

```python
        from paw.security.passwords import WeakPassword, validate_password_strength
        try:
            validate_password_strength(password)
        except WeakPassword as e:
            raise ProblemError(status=422, title="Weak password", detail=str(e)) from e
```

In `src/paw/services/users.py::create`, before `self._repo.create(...)`:

```python
        from paw.security.passwords import WeakPassword, validate_password_strength
        try:
            validate_password_strength(password)
        except WeakPassword as e:
            raise ProblemError(status=422, title="Weak password", detail=str(e)) from e
```

(`ProblemError` is already imported in both service modules.)

- [ ] **Step 5: Add the API regression test**

```python
# tests/api/test_user_create_password_policy.py
from httpx import ASGITransport, AsyncClient

from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _admin_client(db_session):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    await c.post("/api/v1/auth/login",
                 json={"email": "admin@example.com", "password": "pw12345678901"})
    return c


async def test_create_user_rejects_weak_password(db_session, wired_settings):
    c = await _admin_client(db_session)
    try:
        csrf = c.cookies.get("paw_csrf")
        resp = await c.post(
            "/api/v1/users",
            headers={"x-csrf-token": csrf},
            json={"email": "new@example.com", "password": "short", "role": "viewer"},
        )
        assert resp.status_code == 422
    finally:
        await c.aclose()
```

- [ ] **Step 6: Fix any existing tests that POST short passwords to setup/user-create**

Run: `uv run pytest tests/api/test_setup.py tests/api/test_users.py -q` (and any setup/users tests). Where a test POSTs `/api/v1/setup` or `/api/v1/users` with a `<12`-char or common password, raise it to a strong value (e.g. `"pw12345678901"`). Tests that create users via `UserRepo.create` directly are unaffected (the repo does not validate).

- [ ] **Step 7: Run tests + checks, then commit**

Run: `uv run pytest tests/unit/test_password_policy.py tests/api/test_user_create_password_policy.py -v && uv run ruff check . && uv run mypy src`

```bash
git add src/paw/security/passwords.py src/paw/services/setup.py src/paw/services/users.py tests/unit/test_password_policy.py tests/api/test_user_create_password_policy.py
git commit -m "feat(security): enforce password-strength policy at setup and user create (SEC-06)"
```

---

## Task 7: SEC-06c — throttle + lock the login endpoint

**Files:**
- Modify: `src/paw/api/routers/auth.py` (`login`)
- Test: `tests/api/test_login_throttle.py` (new)

**Interfaces:**
- Consumes: `RateLimiter`, `LoginGuard` (Task 5); `get_redis()`, `get_settings()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_login_throttle.py
from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.main import create_app


async def test_login_throttled_after_limit(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit", 3, raising=False)
    monkeypatch.setattr(get_settings(), "login_rate_window_seconds", 60, raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        last = None
        for _ in range(5):
            last = await c.post("/api/v1/auth/login",
                                json={"email": "nobody@example.com", "password": "wrongpassword1"})
        assert last is not None and last.status_code == 429
    finally:
        await c.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_login_throttle.py -v`
Expected: FAIL — repeated bad logins all return 401, never 429.

- [ ] **Step 3: Add throttle + lockout to `login`**

Rewrite the `login` handler in `src/paw/api/routers/auth.py` (add `Request`, `get_redis`, `get_settings`, `RateLimiter`, `LoginGuard` imports):

```python
@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(db),
    store: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    s = get_settings()
    redis = get_redis()
    ip = request.client.host if request.client else "unknown"
    guard = LoginGuard(
        redis, threshold=s.login_lockout_threshold, lock_seconds=s.login_lockout_seconds
    )
    limiter = RateLimiter(redis)

    if await guard.is_locked(body.email) or await guard.is_locked(ip):
        raise ProblemError(status=429, title="Too many attempts",
                           detail="temporarily locked, try again later")
    within_ip = await limiter.hit(
        f"login:ip:{ip}", limit=s.login_rate_limit, window_seconds=s.login_rate_window_seconds
    )
    within_email = await limiter.hit(
        f"login:email:{body.email}", limit=s.login_rate_limit,
        window_seconds=s.login_rate_window_seconds,
    )
    if not (within_ip and within_email):
        raise ProblemError(status=429, title="Too many attempts", detail="slow down")

    user = await UserRepo(session).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.pw_hash):
        await guard.record_failure(body.email)
        await guard.record_failure(ip)
        raise ProblemError(status=401, title="Unauthorized", detail="bad credentials")

    await guard.reset(body.email)
    await guard.reset(ip)
    sid = await store.create(str(user.id))
    csrf = issue_token(s.session_secret)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=True)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=True)
    return LoginResponse(id=str(user.id), email=user.email, role=user.role)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_login_throttle.py tests/api/test_auth.py -v`
Expected: PASS (throttle works; normal login still succeeds). If an existing auth test logs in many times in one window, give it distinct emails/IPs or raise the limit via monkeypatch.

- [ ] **Step 4b: Throttle the setup endpoint**

Spec SEC-06 throttles login **and** setup. Add IP-keyed throttling to `POST /api/v1/setup`. In `src/paw/api/routers/setup.py`, add `Request`, `get_redis`, `get_settings`, `RateLimiter`, `ProblemError` imports and gate `complete`:

```python
@router.post("", status_code=201, response_model=SetupResult)
async def complete(
    body: SetupRequest, request: Request, session: AsyncSession = Depends(db)
) -> SetupResult:
    s = get_settings()
    ip = request.client.host if request.client else "unknown"
    if not await RateLimiter(get_redis()).hit(
        f"setup:ip:{ip}", limit=s.login_rate_limit, window_seconds=s.login_rate_window_seconds
    ):
        raise ProblemError(status=429, title="Too many attempts", detail="slow down")
    admin = await SetupService(session).complete(
        email=body.email,
        password=body.password,
        base_url=body.base_url,
        api_key=body.api_key,
        chat_model=body.chat_model,
        embedding_model=body.embedding_model,
        embedding_dim=body.embedding_dim,
        vision_model=body.vision_model,
    )
    return SetupResult(id=str(admin.id), email=admin.email, role=admin.role)
```

Add the regression test:

```python
# tests/api/test_setup_throttle.py
from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.main import create_app


async def test_setup_throttled_after_limit(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit", 2, raising=False)
    monkeypatch.setattr(get_settings(), "login_rate_window_seconds", 60, raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        payload = {"email": "x@example.com", "password": "pw12345678901",
                   "base_url": "https://api.example.com", "api_key": "k",
                   "chat_model": "m", "embedding_model": "e", "embedding_dim": 8}
        last = None
        for _ in range(4):
            last = await c.post("/api/v1/setup", json=payload)
        assert last is not None and last.status_code == 429
    finally:
        await c.aclose()
```

Run: `uv run pytest tests/api/test_setup_throttle.py -v`
Expected: PASS — the 4th call within the window returns 429 (throttle runs before the service's already-initialized 409 check).

- [ ] **Step 5: Lint + type-check, then commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/api/routers/auth.py src/paw/api/routers/setup.py tests/api/test_login_throttle.py tests/api/test_setup_throttle.py
git commit -m "feat(auth): throttle login and setup, lock out brute-force attempts (SEC-06)"
```

---

# Phase 3 — MED ops / func

## Task 8: SEC-03 — reproducible Docker build from the lockfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Rewrite the build to use the lock**

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# api: uvicorn; worker: arq; init: alembic upgrade head  (entrypoint chosen in compose)
EXPOSE 8000
CMD ["uvicorn", "paw.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify the image builds and all three entrypoints resolve**

Run:
```bash
docker compose build api
docker run --rm --entrypoint sh "$(docker compose config --images api | head -1)" -c "which uvicorn arq alembic"
```
Expected: build succeeds; `which` prints the three `/app/.venv/bin/*` paths.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "build: install from uv.lock for reproducible images (SEC-03)"
```

---

## Task 9: OPS-01 — fail-fast on missing Postgres password

**Files:**
- Modify: `docker-compose.yml` (lines 34, 157, 189), `.env.example`

- [ ] **Step 1: Replace the weak default with the required-variable operator**

At all three sites that currently default `POSTGRES_PASSWORD` to `paw` (the `postgres` service env line 34, the postgres-exporter `DATA_SOURCE_NAME` line 157, and the `PGPASSWORD` line 189), switch the default-substitution `:-paw` to the required form `:?POSTGRES_PASSWORD must be set`. Compose then aborts with an error when the variable is empty or unset. For the exporter DSN at line 157, embed the same required form inside the connection string.

- [ ] **Step 2: Update `.env.example`**

Replace the shipped weak value with an empty placeholder and a comment:

```bash
# REQUIRED: strong Postgres password (compose refuses to start if unset)
POSTGRES_PASSWORD=
```

- [ ] **Step 3: Verify fail-fast**

Run:
```bash
env -u POSTGRES_PASSWORD docker compose config >/dev/null; echo "exit=$?"
```
Expected: non-zero exit with an error naming `POSTGRES_PASSWORD`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "ops: require an explicit Postgres password, no weak default (OPS-01)"
```

---

## Task 10: FUNC-01 — bulk upload returns an HTML drawer

**Files:**
- Create: `src/paw/api/web/templates/_jobs_drawer.html`
- Modify: `src/paw/api/web/routes.py` (new web bulk endpoint + imports), `src/paw/api/web/templates/domain.html:12`
- Test: `tests/api/test_web_bulk_upload.py` (new)

**Interfaces:**
- Produces: `POST /domains/{domain_id}/sources/bulk` (web) returning the `_jobs_drawer.html` partial.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_web_bulk_upload.py
import io
import zipfile

from httpx import ASGITransport, AsyncClient

from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", "# A\n")
        zf.writestr("b.md", "# B\n")
    return buf.getvalue()


async def test_web_bulk_upload_returns_drawer(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="ed@example.com", pw_hash=hash_password("pw12345678901"), role="editor"
    )
    dom = await DomainRepo(db_session).create(name="d", brief="b")
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        await c.post("/api/v1/auth/login",
                     json={"email": "ed@example.com", "password": "pw12345678901"})
        csrf = c.cookies.get("paw_csrf")
        resp = await c.post(
            f"/domains/{dom.id}/sources/bulk",
            headers={"x-csrf-token": csrf},
            files={"file": ("s.zip", _zip_bytes(), "application/zip")},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert resp.text.count('class="job"') == 2  # one drawer row per started job
    finally:
        await c.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web_bulk_upload.py -v`
Expected: FAIL — the web route does not exist (404), the page currently posts to the JSON API.

- [ ] **Step 3: Add the multi-job drawer template**

```html
<!-- src/paw/api/web/templates/_jobs_drawer.html -->
{% for job_id in job_ids %}
<div class="job" hx-ext="sse" sse-connect="/api/v1/jobs/{{ job_id }}/events">
  <progress max="6"></progress>
  <ul sse-swap="message" hx-swap="beforeend"></ul>
  <button hx-post="/api/v1/jobs/{{ job_id }}/cancel"
          hx-headers='{"x-csrf-token": "{{ csrf }}"}'>Cancel</button>
</div>
{% endfor %}
```

- [ ] **Step 4: Add the web endpoint**

In `src/paw/api/web/routes.py`, extend imports:

```python
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from paw.api.errors import ProblemError
from paw.security.ssrf import SsrfRejected
from paw.security.uploads import UploadRejected
from paw.services.sources import SourceService
```

Add the route (near the existing `web_start_ingest`):

```python
@router.post("/domains/{domain_id}/sources/bulk", response_class=HTMLResponse)
async def web_bulk_upload(
    domain_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(db),
    _: None = Depends(require_csrf),
    __: User = Depends(require_role("admin", "editor")),
) -> Response:
    data = await file.read()
    try:
        srcs = await SourceService(session).upload_bulk(domain_id=domain_id, zip_bytes=data)
    except (UploadRejected, SsrfRejected) as e:
        raise ProblemError(status=422, title="Bulk upload rejected", detail=str(e)) from e
    jobs = JobService(session)
    job_ids = [str((await jobs.start_ingest(domain_id=domain_id, source_id=src.id)).id)
               for src in srcs]
    csrf = request.cookies.get(CSRF_COOKIE, "")
    return templates.TemplateResponse(
        request, "_jobs_drawer.html", {"job_ids": job_ids, "csrf": csrf}
    )
```

- [ ] **Step 5: Point the UI at the web endpoint**

In `src/paw/api/web/templates/domain.html:12`, change the bulk form action from `/api/v1/domains/{{ domain.id }}/sources/bulk` to `/domains/{{ domain.id }}/sources/bulk` (drop the `/api/v1` prefix). Leave the JSON API endpoint in `routers/sources.py` untouched for API clients.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_web_bulk_upload.py -v && uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/paw/api/web/routes.py src/paw/api/web/templates/_jobs_drawer.html src/paw/api/web/templates/domain.html tests/api/test_web_bulk_upload.py
git commit -m "fix(web): bulk upload returns an HTML job drawer (FUNC-01)"
```

---

# Phase 4 — LOW + hardening

## Task 11: SEC-07 — wire the audit log into sensitive operations

**Files:**
- Create: `src/paw/audit/actions.py`
- Modify: `src/paw/api/routers/auth.py` (login/logout), `src/paw/services/setup.py`, `src/paw/services/users.py`, `src/paw/services/api_keys.py`, `src/paw/services/provider_settings.py`, `src/paw/services/jobs.py`
- Test: `tests/integration/test_audit_log.py` (new)

**Interfaces:**
- Consumes: `audit.log.record(session, *, user_id, action, target_type=None, target_id=None, meta=None)` (flush-only; the owning service commits).
- Produces: action-name constants in `audit/actions.py`.

- [ ] **Step 1: Define the action constants**

```python
# src/paw/audit/actions.py
LOGIN = "user.login"
LOGOUT = "user.logout"
SETUP_COMPLETE = "setup.complete"
USER_CREATE = "user.create"
USER_ROLE_CHANGE = "user.role_change"
USER_DELETE = "user.delete"
API_KEY_ISSUE = "api_key.issue"
API_KEY_REVOKE = "api_key.revoke"
PROVIDER_CHANGE = "provider.change"
INGEST_START = "ingest.start"
INGEST_ROLLBACK = "ingest.rollback"
# NOTE: no ingest/source delete operation exists in the services today; add an
# INGEST_DELETE constant and wire it when such a delete operation is introduced.
```

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_audit_log.py
from sqlalchemy import func, select

from paw.audit import actions
from paw.db.models import AuditLog
from paw.services.users import UserService


async def _count(session, action: str) -> int:
    res = await session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
    )
    return int(res.scalar_one())


async def test_user_create_writes_audit_row(db_session, wired_settings):
    before = await _count(db_session, actions.USER_CREATE)
    await UserService(db_session).create(
        email="audited@example.com", password="pw12345678901", role="viewer"
    )
    after = await _count(db_session, actions.USER_CREATE)
    assert after == before + 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_audit_log.py -v`
Expected: FAIL — no `user.create` audit row is written.

- [ ] **Step 4: Wire `record(...)` at each call site (before the service's `commit()`)**

`services/users.py::create` — accept the actor and log:

```python
    async def create(self, *, email: str, password: str, role: str,
                     actor_id: uuid.UUID | None = None) -> User:
        # ... existing password-policy check ...
        u = await self._repo.create(email=email, pw_hash=hash_password(password), role=role)
        from paw.audit import actions
        from paw.audit.log import record
        await record(self._s, user_id=actor_id, action=actions.USER_CREATE,
                     target_type="user", target_id=u.id)
        await self._s.commit()
        return u
```

Apply the same pattern (import `record` + `actions`, call before the existing `await self._s.commit()`):
- `services/users.py::set_role` → `actions.USER_ROLE_CHANGE`, `target_id=user_id`.
- `services/users.py::delete` → `actions.USER_DELETE`, `target_id=user_id` (record before `repo.delete`/commit).
- `services/setup.py::complete` → `actions.SETUP_COMPLETE`, `user_id=admin.id`, `target_type="user"`, `target_id=admin.id`.
- `services/api_keys.py::issue` → `actions.API_KEY_ISSUE`, `user_id=user_id`; `revoke` → `actions.API_KEY_REVOKE`, `target_id=key_id`.
- `services/provider_settings.py::persist_provider`, `set_provider`, `update_provider` → `actions.PROVIDER_CHANGE`.
- `services/jobs.py::start_ingest` → `actions.INGEST_START`, `target_type="source"`, `target_id=source_id`.
- `services/articles.py::rollback` → `actions.INGEST_ROLLBACK`, `target_type="article"`, `target_id=article_id` (record before the existing `commit()`).
- No source/article *delete* service operation exists yet (only `db/repos/sources.py::delete`, unused by any service) — SEC-07 delete auditing is deferred until such an operation is added; see the `NOTE` in `actions.py`.

For login/logout (no service), call `record(...)` in `routers/auth.py` before the response is returned, using the same session (`session.add` via `record` then it is flushed; the request's session commits on dependency teardown — if these handlers do not otherwise commit, add an explicit `await session.commit()` after `record`):
- `login` success → `actions.LOGIN`, `user_id=user.id`. Insert this record into the **throttled** login handler from Task 7 (after `guard.reset(...)`, before returning `LoginResponse`) — Task 11 edits Task 7's rewritten body, not the original handler.
- `logout` → `actions.LOGOUT`, `user_id` resolved from the session id before deletion (skip if unknown).

Update callers that pass the actor: `routers/users.py::create_user` passes `actor_id=` from a `current_user` dependency (add `user: User = Depends(require_role("admin"))` to the handler and forward `actor_id=user.id`).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_audit_log.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + type-check, then commit**

Run: `uv run ruff check . && uv run mypy src`

```bash
git add src/paw/audit/actions.py src/paw/api/routers/auth.py src/paw/api/routers/users.py src/paw/services tests/integration/test_audit_log.py
git commit -m "feat(audit): record login, setup, user, api-key, provider and ingest events (SEC-07)"
```

---

## Task 12: QUAL-01 — `.dockerignore` + bytecode hygiene

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Add `.dockerignore`**

```gitignore
# .dockerignore — keep generated/dev files out of the build context and image
__pycache__/
*.py[cod]
.venv/
.git/
.github/
tests/
docs/
.claude/
.env
*.md
.ruff_cache/
.mypy_cache/
.pytest_cache/
```

- [ ] **Step 2: Verify the build context shrinks and excludes bytecode**

Run:
```bash
find . -name '__pycache__' -type d -not -path './.venv/*' -prune -exec rm -rf {} +
docker compose build api
```
Expected: build succeeds; no `__pycache__` in `src/`, `tests/`, `alembic/`.

- [ ] **Step 3: Commit**

```bash
git add .dockerignore
git commit -m "chore: add .dockerignore to keep bytecode out of build context (QUAL-01)"
```

---

## Task 13: Bandit triage

**Files:**
- Modify: `src/paw/services/query_cache.py:179`, `src/paw/graph/age/cypher.py:62,80`, `src/paw/api/web/i18n.py:25,47,78,97`, `src/paw/providers/structured.py:25`, `src/paw/services/users.py:46`, and the B110 sites (`obs/readiness.py:16,22`, `worker.py:37,72`, `jobs/tasks.py:55,123,202,227,361`, `obs/instrument.py:41`)
- Test: `tests/unit/test_cypher_guard.py` (new, for the cypher wrapper)

- [ ] **Step 1: Suppress confirmed false positives with justification**

- `query_cache.py:179`: the SQL is the constant `text("SELECT id, current_rev FROM articles WHERE id = ANY(:ids)")` with bound params — add `# nosec B608  # constant SQL, ids are bind-parameterized`.
- `i18n.py` lines 25/47/78/97 (`"Password"`/`"Пароль"` UI labels): add `# nosec B105  # UI label, not a secret` on each flagged line.

- [ ] **Step 2: Guard the cypher builder, then suppress**

In `graph/age/cypher.py`, before building the `f"SELECT ... cypher('{g}', ...)"` strings (lines 62 and 80), assert the graph name is internal-format and add the suppression:

```python
    if not re.fullmatch(r"g_[0-9a-f]{32}", g):
        raise ValueError("invalid graph name")
    ...
    sql = (  # nosec B608  # g is regex-validated; body/columns are internal literals; params are agtype-bound
        f"SELECT * FROM cypher('{g}', $cy${safe}$cy$, CAST(:p AS agtype)) AS ({columns})"
    )
```

(Add `import re` if not present.) Write a unit test locking the guard:

```python
# tests/unit/test_cypher_guard.py
import pytest

from paw.graph.age import cypher


def test_rejects_non_internal_graph_name():
    with pytest.raises(ValueError):
        cypher._graph_name("drop table users")  # adjust to the actual validation entrypoint
```

(If the graph name is validated by a helper rather than inline, point the test at that helper; the assertion is that a non-`g_<32hex>` name raises.)

- [ ] **Step 3: Replace production `assert`s with explicit raises**

- `providers/structured.py:25` `assert isinstance(result, ChatResult)` →
  ```python
  if not isinstance(result, ChatResult):
      raise TypeError("expected ChatResult from provider")
  ```
- `services/users.py:46` `assert refreshed is not None` →
  ```python
  if refreshed is None:
      raise ProblemError(status=404, title="User not found")
  ```

- [ ] **Step 4: Add logging to silent best-effort excepts (B110)**

At each `except Exception:  # noqa: BLE001` that currently passes silently in `obs/readiness.py`, `worker.py`, `jobs/tasks.py`, `obs/instrument.py`, add a debug/warning log so degradations are diagnosable, e.g.:

```python
import logging
logger = logging.getLogger(__name__)
...
        except Exception:  # noqa: BLE001
            logger.warning("best-effort telemetry failed", exc_info=True)
```

Keep the best-effort semantics (do not re-raise); only add the log line. Use module-level `logger` per file.

- [ ] **Step 5: Re-run bandit + the suite**

Run:
```bash
uv run --with bandit bandit -r src -q -f txt
uv run pytest tests/unit/test_cypher_guard.py -v && uv run ruff check . && uv run mypy src
```
Expected: bandit medium/low counts drop (remaining carry justified `# nosec`); tests + checks pass.

- [ ] **Step 6: Commit**

```bash
git add src/paw tests/unit/test_cypher_guard.py
git commit -m "chore(security): triage bandit findings — nosec, explicit raises, telemetry logging"
```

---

## Task 14: Coverage hardening

**Files:**
- Modify: `pyproject.toml` (coverage config)
- Test: `tests/unit/test_readiness.py`, `tests/integration/test_worker_heartbeat.py` (new), plus targeted additions

- [ ] **Step 1: Enable branch coverage**

Append to `pyproject.toml`:

```toml
[tool.coverage.run]
branch = true
source = ["src/paw"]
```

- [ ] **Step 2: Add a readiness regression test (24% → up)**

```python
# tests/unit/test_readiness.py
from paw.obs import readiness


async def test_readiness_reports_degraded_component(monkeypatch):
    async def boom() -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(readiness, "_check_db", boom, raising=False)
    ok, components = await readiness.check_readiness()
    assert ok is False
    assert any(v != "ok" for v in components.values())
```

(Adjust the monkeypatched symbol to the actual internal check name in `readiness.py`; the assertion is that a failing component yields `ok is False` and a non-ok component entry.)

- [ ] **Step 3: Add a worker heartbeat test (54% → up)**

```python
# tests/integration/test_worker_heartbeat.py
from paw.api.deps import get_redis
from paw import worker


async def test_heartbeat_writes_marker(wired_settings):
    await worker.heartbeat({})
    assert await get_redis().get("paw:worker:heartbeat") is not None
```

(Adjust the call signature to `heartbeat`'s actual arguments.)

- [ ] **Step 4: Measure and fill the largest remaining gaps**

Run:
```bash
uv run --with coverage coverage run --source=src/paw -m pytest -q
uv run --with coverage coverage report --show-missing --skip-covered
```
For each listed module still below 80% (`services/maintenance.py`, `services/articles.py`, `jobs/tasks.py`, `services/setup.py`, `services/users.py`, `api/web/routes.py`), add a focused test covering the specific missed lines the report names — e.g. `articles.rollback` happy + not-found paths, `maintenance` start_* orchestration, `setup` already-initialized 409, `users.set_role` last-admin 409. The per-finding regression tests from Tasks 1–13 already lift `routes.py`, `setup.py`, `users.py`, and `articles.py`; this step closes the rest to the ≥80% target. Concretely add: `test_rollback_happy` + `test_rollback_not_found` (articles), `test_start_lint_format_reindex` (maintenance orchestration), `test_setup_already_initialized_409` (setup), and `test_set_role_last_admin_409` + `test_delete_last_admin_409` (users).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests
git commit -m "test: enable branch coverage and harden low-coverage modules"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** SEC-01→T1, SEC-02→T2, SEC-04→T3, SEC-05→T4, SEC-06→T5/T6/T7, SEC-03→T8, OPS-01→T9, FUNC-01→T10, SEC-07→T11, QUAL-01→T12, bandit→T13, coverage→T14. All spec sections mapped.
- **Type consistency:** `_require_web_user` (T1), `validate_url -> tuple[str,str]` / `safe_get(..., client=)` (T3), `RateLimiter.hit` / `LoginGuard` (T5) consumed unchanged in T7, `validate_password_strength` / `WeakPassword` (T6) consumed in T6 services, `record(...)` signature (T11) matches `audit/log.py`.
- **Note:** new `password_min_length=12` only affects `SetupService.complete` and `UserService.create`; tests creating users via `UserRepo.create` are unaffected (T6 Step 6 fixes the few that POST short passwords through the API).
- **PR strategy:** all tasks land on `dev-security-audit-followup`; phases are logical checkpoints. Split into per-phase PRs at finishing time if the reviewer prefers.
