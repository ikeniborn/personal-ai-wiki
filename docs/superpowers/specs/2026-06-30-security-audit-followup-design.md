---
review:
  spec_hash: e2661b83be9c570a
  last_run: 2026-06-30
  phases:
    structure:   { status: passed }
    coverage:    { status: passed }
    clarity:     { status: passed }
    consistency: { status: passed }
  findings:
    - id: F-001
      phase: structure
      severity: WARNING
      section: "SEC-02 — Upload body read into memory before limit checks"
      fragment: "Document the Traefik `maxRequestBodyBytes` outer limit in Phase 3 ops notes."
      text: >-
        SEC-02 defers documenting the Traefik maxRequestBodyBytes outer limit to a
        "Phase 3 ops notes" section, but Phase 3 contains only SEC-03, OPS-01 and
        FUNC-01 — there is no "ops notes" subsection, and no requirement documents the
        Traefik body limit anywhere. The forward reference resolves to nothing.
      fix: >-
        Either add an explicit "Ops notes" subsection in Phase 3 that documents the
        Traefik maxRequestBodyBytes limit, or fold that note into SEC-02 itself and
        drop the forward reference.
      verdict: fixed
    - id: F-002
      phase: structure
      severity: WARNING
      section: "OPS-01 — Weak Postgres password default"
      fragment: "replace the fallback with `${POSTGRES_PASSWORD:\"MASKING\" must be set}` at all three sites."
      text: >-
        The OPS-01 design prescribes a literal compose value `${POSTGRES_PASSWORD:"MASKING"
        must be set}` which is not valid Docker Compose / shell interpolation syntax — it
        is a masking artifact carried over from the source audit HTML. The actual default
        in docker-compose.yml is `${POSTGRES_PASSWORD:-paw}`. As written the design is not
        implementable verbatim.
      fix: >-
        Specify the real fail-fast form, e.g. `${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must
        be set}` (the `:?` operator makes compose error out when the var is unset), at
        lines 34, 157 and 189.
      verdict: fixed
    - id: F-003
      phase: clarity
      severity: WARNING
      section: "SEC-06 — No brute-force protection, no password policy"
      fragment: "blocklist. Applied in setup, user create, and password change."
      text: >-
        Password-policy scope lists "password change" as a call site, but neither the
        SEC-06 Problem statement nor the cross-cutting section identifies a password-change
        endpoint, and the SEC-06 Tests only verify "setup and user create". The
        password-change application point has no concrete location or acceptance test.
      fix: >-
        Name the password-change endpoint/service to be guarded (or drop it from scope),
        and add it to the SEC-06 test list so every claimed application point has an
        acceptance check.
      verdict: fixed
    - id: F-004
      phase: clarity
      severity: INFO
      section: "Coverage hardening"
      fragment: "Optionally enable `branch = true` in the coverage config."
      text: >-
        The coverage-hardening requirement uses non-measurable wording: "Optionally enable
        branch = true" and "Coverage on the listed modules measurably increased" without a
        per-module target percentage. "Measurably increased" is not a falsifiable DoD.
      fix: >-
        Either set a concrete numeric target per listed module (or a minimum delta), and
        make the branch-coverage toggle a definite yes/no rather than "optionally".
      verdict: fixed
    - id: F-005
      phase: clarity
      severity: INFO
      section: "SEC-05 — `/metrics` exposed without auth"
      fragment: "If `config.metrics_token` is unset, the endpoint returns `404` (disabled by default — fail closed)."
      text: >-
        Terminology for the disabled-endpoint response is internally consistent (404), but
        the cross-cutting section describes the same field as "unset ⇒ endpoint disabled"
        without stating the 404 status, while the audit recommendation suggested removing
        the endpoint or IP-allowlisting it. Minor wording drift, no contradiction.
      fix: >-
        Optional: align the cross-cutting description of metrics_token with the SEC-05
        body so both state "unset ⇒ 404".
      verdict: fixed
chain:
  intent: null
---
# Security Audit Follow-up — Design Spec

- **Date:** 2026-06-30
- **Source:** `docs/reports/implementation-security-audit-2026-06-29.html`
- **Scope:** all 10 audit findings (2 HIGH, 6 MED, 2 LOW) + bandit triage + test-coverage hardening
- **Rollout:** one spec, four severity-ordered phases, one PR per phase, regression tests written alongside each fix (TDD per finding)

## Context

The 2026-06-29 audit found the project healthy overall (509 tests pass, 88% line
coverage, 0 known CVEs, 0 bandit-high) but flagged concrete weaknesses across web
auth, upload handling, SSRF, observability exposure, brute-force protection, build
reproducibility, ops defaults, a UI/API contract mismatch, an unwired audit log,
and bytecode hygiene. This spec turns those findings into an implementable design.

The layered architecture (api → services → repos/storage; single commit boundary in
the service layer) and existing security primitives (`SessionStore`, `require_role`,
`require_csrf`, Argon2 passwords, Fernet secrets, `nh3` sanitize, SSRF guard) are kept.
Changes are surgical: each touches only the code path the finding names.

## Goals

- Close both HIGH findings (stale web session authz, unbounded upload read).
- Harden the six MED findings (SSRF TOCTOU, open `/metrics`, no brute-force/password
  policy, non-reproducible Docker build, weak Postgres default, bulk-upload contract).
- Resolve both LOW findings (wire the audit log, bytecode hygiene).
- Triage bandit findings (suppress confirmed false positives with justification, fix
  the real smells).
- Raise test coverage on the low-coverage modules the audit listed, with a regression
  test for every finding.

## Non-goals

- No refactor beyond what a finding requires.
- No new product features.
- No change to the layered architecture or commit-boundary rule.
- No object-store storage backend (out of audit scope).

## Cross-cutting additions

New modules / config introduced once and reused across phases:

- `src/paw/security/ratelimit.py` — Redis sliding-window limiter:
  `RateLimiter(redis).hit(key, *, limit, window_seconds) -> bool` (True = allowed).
  Reuses the process-global Redis from `api/deps.py::get_redis`.
- `src/paw/api/middleware/body_limit.py` — ASGI middleware enforcing a hard request
  body cap (details in SEC-02).
- `src/paw/config.py` new fields:
  - `metrics_token: str | None = None` — Bearer token gating `/metrics`; unset ⇒ `/metrics` returns `404` (fail-closed).
  - `login_rate_limit: int = 5` and `login_rate_window_seconds: int = 60` — login/setup throttle.
  - `login_lockout_threshold: int = 10` and `login_lockout_seconds: int = 900` — temporary lockout.
  - `password_min_length: int = 12` — minimum password length.
  - `max_request_bytes` (already present, currently unused) becomes the body-limit cap.
- `src/paw/audit/actions.py` — action-name constants shared by audit-log call sites.

---

## Phase 1 — HIGH

### SEC-01 — Stale web session authorizes deleted users

**Problem.** `api/web/routes.py` web GET handlers gate on `_current_uid` (Redis
session id existence) and only later load the user via `_current_user_opt`. A deleted
user's Redis session keeps serving pages until TTL. `graph_page` (`routes.py:149`) has
no user load at all. The API dependency `api/deps.py::current_user` already loads and
rejects missing users — web routes must match that guarantee.

**Design.** Add one helper in `api/web/routes.py`:

```python
async def _require_web_user(
    request: Request, session: AsyncSession, store: SessionStore
) -> User | None:
    sid = request.cookies.get(SESSION_COOKIE, "")
    uid = await store.get(sid)
    if not uid:
        return None
    user = await UserRepo(session).get(uuid.UUID(uid))
    if user is None:
        await store.delete(sid)  # evict stale session for a deleted user
        return None
    return user
```

Call site in every authenticated web GET (`dashboard`, `domain_page`, `graph_page`,
`article_page`, `query_page`, settings/users/admin pages):

```python
user = await _require_web_user(request, session, store)
if user is None:
    return RedirectResponse("/login", status_code=307)
```

This replaces the `_current_uid` + `_current_user_opt` pair, fixes the missing check
in `graph_page`, and evicts the stale session on the deleted-user path. `SessionStore.delete`
already exists (`security/sessions.py:26`). `_current_uid` / `_current_user_opt` are
removed once all call sites migrate (clean up orphans created by this change).

**Tests.** Create user → log in → delete user → request each web page ⇒ 307 to
`/login` **and** the Redis session is gone.

### SEC-02 — Upload body read into memory before limit checks

**Problem.** `api/routers/sources.py:39` and `:63` call `await file.read()` (full body)
before `validate_source_upload` checks size. `config.max_request_bytes` exists but is unused.

**Design.** `src/paw/api/middleware/body_limit.py` — ASGI middleware:

- Reject early with `413` when the `Content-Length` header exceeds `max_request_bytes`.
- Wrap `receive` to count bytes actually streamed and abort with `413` past the cap
  (covers chunked / absent `Content-Length`).

Wire via `app.add_middleware(BodySizeLimitMiddleware, max_bytes=...)` in
`main.py::create_app`. With the cap enforced at the ASGI layer, the existing
`await file.read()` in the routers is safe; `max_upload_bytes` post-read validation in
`SourceService.upload` stays as the per-file (vs whole-request) check. As a second
layer, an outer `maxRequestBodyBytes` limit is configured on the Traefik proxy via
`docker-compose.yml` labels (self-contained here; no dependency on a later phase).

**Tests.** POST a body over the cap with a large `Content-Length` ⇒ `413` before any
handler work; POST chunked over the cap ⇒ `413`; a normal upload still succeeds.

---

## Phase 2 — MED security

### SEC-04 — SSRF DNS TOCTOU

**Problem.** `security/ssrf.py:52` validates IPs via `getaddrinfo`, then `httpx`
(`:70`) re-resolves at connect time. DNS rebinding between check and use can reach an
internal address.

**Design.** Pin the connection to the validated IP:

- `validate_url` returns `(host, verified_ip)` (the first non-blocked resolved IP).
- `safe_get` issues the request to the verified IP while preserving Host + SNI:
  rewrite the URL host to the IP, set `Host: <original-host>` header, and pass
  `extensions={"sni_hostname": host}` so httpx uses the original host for TLS SNI and
  certificate verification.
- Re-validate **and** re-pin on every redirect hop (the existing per-redirect
  `validate_url` loop stays; it now also re-pins).

This closes the TOCTOU window: the socket connects to an address already vetted in the
same call, not a freshly re-resolved one.

**Tests.** Mock resolver returning a public IP on the first call and a blocked
(private) IP on a second call (rebinding simulation) ⇒ connection still targets the
first vetted IP / request rejected; allowlist + https-only checks unchanged.

### SEC-05 — `/metrics` exposed without auth

**Problem.** `main.py:72` serves `/metrics` to anyone reaching the API; the Traefik
rule routes the whole host to the API with no path restriction.

**Design.** Defense-in-depth (app token + proxy allowlist):

- App: gate `/metrics` on `Authorization: Bearer <metrics_token>`. If
  `config.metrics_token` is unset, the endpoint returns `404` (disabled by default —
  fail closed). Constant-time token compare (`secrets.compare_digest`).
- Traefik: add an IP-allowlist middleware on the `/metrics` path in `docker-compose.yml`.
- `config.py` gains `metrics_token`; `.env.example` documents `METRICS_TOKEN`.

**Tests.** With `metrics_token` set: missing/wrong token ⇒ `401`; correct token ⇒
`200` + metrics payload. With `metrics_token` unset ⇒ `404` for any request.

### SEC-06 — No brute-force protection, no password policy

**Problem.** `api/routers/auth.py:21` (login) and `api/routers/setup.py:86` accept
passwords with no throttling/lockout; `security/passwords.py` hashes strongly but never
checks strength.

**Design (full package).**

- **Throttle.** `RateLimiter` applied to login and setup, keyed by both client IP and
  submitted email. Over `login_rate_limit` per `login_rate_window_seconds` ⇒ `429`
  with `Retry-After`.
- **Lockout.** Track consecutive failed logins per email/IP; past `login_lockout_threshold`
  ⇒ temporary lock for `login_lockout_seconds` (Redis counter with expiry).
- **Password policy.** `security/passwords.py::validate_password_strength(pw)` enforces
  `password_min_length` and rejects entries in a small embedded common-password
  blocklist. Applied at the two password-entry points that exist today — setup
  (`setup.py`) and user create (`users.py`); no self-service password-change endpoint
  exists yet, so it is out of scope.

**Tests.** Exceed login attempts ⇒ `429`; trip lockout ⇒ locked until window passes;
weak/short password at setup and user create ⇒ rejected; strong password accepted.

---

## Phase 3 — MED ops / func

### SEC-03 — Docker build ignores the lockfile

**Problem.** `Dockerfile:5-9` copies only `pyproject.toml` and runs
`uv pip install --system .`; lower-bound version ranges mean the image can drift from
the CI/local `uv.lock`.

**Design.** Reproducible build from the lock:

```dockerfile
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
```

`CMD`/entrypoints resolve from the `.venv` on `PATH`. Build now matches the lock used
by CI (`uv sync --dev`) and local dev.

**Tests.** N/A (build-time). Verified by a green image build in CI / manual
`docker compose build`.

### OPS-01 — Weak Postgres password default

**Problem.** `docker-compose.yml:34/157/189` use a fallback weak `POSTGRES_PASSWORD`;
`.env.example:19` ships the same weak value.

**Design.** Fail-fast on a missing secret: at all three sites
(`docker-compose.yml:34/157/189`) drop the weak fallback default and switch to
Compose's required-variable operator (the `:?` form), which makes `docker compose`
abort with an error when the variable is empty or unset. `.env.example` ships a
placeholder plus a comment to set a strong secret; document in the README run section.

**Tests.** N/A (compose config). Verified by `docker compose config` failing without
the env var set.

### FUNC-01 — Bulk-upload UI/API contract mismatch

**Problem.** `templates/domain.html:12-14` posts HTMX to the JSON API
`/api/v1/.../sources/bulk` with `hx-target="#job-drawer"`, but that endpoint returns
JSON `BulkOut`, not the `_job_drawer.html` partial.

**Design.** Add a web endpoint `POST /domains/{domain_id}/sources/bulk` in
`api/web/routes.py` that performs the bulk upload + job starts and returns a drawer
partial for the batch (a multi-job variant of `_job_drawer.html`, or `_job_drawer.html`
rendered with the list of `job_ids`). Point `domain.html`'s HTMX at the web endpoint.
The JSON API endpoint stays for API clients. Reuses `SourceService.upload_bulk` and
`JobService.start_ingest` (same logic as the API router).

**Tests.** POST a bulk zip to the web endpoint ⇒ HTML drawer partial with one entry per
started job; the JSON API endpoint keeps returning `BulkOut`.

---

## Phase 4 — LOW + hardening + coverage

### SEC-07 — Audit log declared but unwired

**Problem.** `db.models.AuditLog` and `audit/log.py::record` exist but no service/API
calls `record(...)`.

**Design.** Call `audit.log.record(...)` at sensitive operations, inside the owning
service's existing commit boundary (`record` only `flush()`es, so the service's single
`commit()` persists it atomically with the operation): login/logout, setup completion,
user create / role change / delete, API-key issue / revoke, provider & settings
changes, ingest start / rollback / delete. Action names come from
`audit/actions.py` constants.

**Tests.** Each wired operation writes exactly one `AuditLog` row with the right
`action`, `user_id`, and `target` fields, within the same transaction as the operation.

### QUAL-01 — Generated bytecode in the work tree

**Problem.** `__pycache__` / `*.pyc` present under `src/`, `tests/`, `alembic/`; no
`.dockerignore`.

**Design.** Add `.dockerignore` excluding `__pycache__`, `*.pyc`, `.venv`, `tests`,
`.git`, `docs`, and other non-runtime paths (also shrinks the build context and
reinforces SEC-03). Document a one-line `find … -name '__pycache__' -prune` cleanup in
the README; keep the existing gitignore rules.

**Tests.** N/A. Verified by `docker build` context size / no bytecode in the image.

### Bandit triage

- `B608` in `services/query_cache.py` — false positive (constant `_SELECT`, bound
  params): add `# nosec B608` with a justifying comment.
- `B608` in `graph/age/cypher.py` — constrained (graph name regex-validated, params as
  agtype): add a typing wrapper / test asserting `body`/`columns` are internal literals,
  then `# nosec B608` with justification.
- `B105` in i18n — false positive (UI labels "Password"/"Пароль"): `# nosec B105`.
- `B110` silent `except` in jobs/obs/worker — keep best-effort behavior but log at
  debug/warning so degradations are diagnosable.
- `B101` `assert` in production code — replace with an explicit check + raise (asserts
  vanish under `python -O`).

**Tests.** Where a smell is fixed (B110 logging, B101 raise), add a test exercising the
branch; nosec-only changes need no test.

### Coverage hardening

Add negative / branch tests to raise coverage on the audit-listed low files:
`obs/readiness.py` (24%), `worker.py` (54%), `services/maintenance.py` (59%),
`api/web/routes.py` (60%), `services/setup.py` (60%), `services/users.py` (65%),
`jobs/tasks.py` (67%), `services/articles.py` (68%). Focus on degraded/readiness
branches, worker startup/shutdown, maintenance orchestration, setup race/error paths,
deleted/last-admin user edges, job cancel/fail/retry, and article rollback/error paths.
Enable `branch = true` in the coverage config. Target: every listed module reaches
≥ 80% line coverage, and no module drops below its current percentage.

---

## Acceptance criteria

- All four phases merged; `ruff check .`, `mypy src`, `pytest -q` green at each PR.
- Each finding has a regression test that fails before the fix and passes after.
- Both HIGH findings closed; six MED findings hardened; both LOW findings resolved.
- Bandit re-run: real smells fixed, remaining findings carry justified `# nosec`.
- Every listed module reaches ≥ 80% line coverage; branch coverage enabled; no overall coverage regression.

## Risks & mitigations

- **SSRF IP-pin + SNI** (SEC-04) is the trickiest change — httpx extension behavior
  must be verified against real TLS; the rebinding test guards it.
- **Body-limit middleware** (SEC-02) must not break MCP / streaming endpoints — scope
  the cap to request bodies and confirm existing streaming tests still pass.
- **Docker `uv sync` switch** (SEC-03) changes the runtime layout (`.venv` on PATH) —
  verify all three entrypoints (api / worker / init-migrate) still start.
- **Audit-log wiring** (SEC-07) must respect the single-commit-boundary rule — never
  add a stray `commit()` in `record` or call sites.
