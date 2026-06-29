# Security

## Overview
Security spans Redis-backed server-side sessions (opaque `paw_session` cookie), `require_role` RBAC and `require_csrf` double-submit CSRF (exempt for GET/HEAD/OPTIONS), argon2 password hashing, Fernet-encrypted secrets at rest via `SecretBox`, upload validation by extension/magic-bytes/UTF-8, a metadata-only anti-zip-bomb / path-traversal guard, an SSRF-guarded URL fetcher, nh3 HTML sanitization of rendered markdown, a CSP/security-headers middleware, and Bearer api-key auth for the MCP endpoint. Enforcement is wired through FastAPI dependencies in [[api#Dependency helpers (deps.py)]].

## Sessions
`SessionStore` (`security/sessions.py`) keeps sessions server-side in Redis under the `session:` prefix; the `paw_session` cookie holds only an opaque `secrets.token_urlsafe(32)` id. `create` writes `user_id` with a TTL, `get` reads it, `delete` revokes it. The cookie is `SameSite=Lax`.

- `current_user` (`api/deps.py`) reads the `paw_session` cookie, calls `store.get(sid)`, then loads the row via `UserRepo`; a missing session or user raises 401 — see [[api#Errors (problem+json)]].
- The store is a lazy singleton (`get_session_store` over `get_redis`), TTL from `session_ttl_seconds`. Wiring lives in [[architecture#create_app() wiring]].

## RBAC
`require_role(*roles)` (`api/deps.py`) is a dependency factory: it depends on `current_user`, then checks `user.role` is in the allowed set, raising `ProblemError(403, "Forbidden")` otherwise. Routers attach it to gate admin-only operations.

```python
def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    async def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise ProblemError(status=403, title="Forbidden", ...)
        return user
    return _dep
```

See how routers consume these dependencies in [[api#Dependency helpers (deps.py)]].

## CSRF
CSRF uses a double-submit token (`security/csrf.py`): `issue_token` makes `nonce.sig` where `sig` is an HMAC-SHA256 of the nonce under `session_secret`. `verify_token` requires the `paw_csrf` cookie and `x-csrf-token` header to be equal **and** authentic, compared with `hmac.compare_digest`.

- `require_csrf` (`api/deps.py`) returns early for `GET`/`HEAD`/`OPTIONS`; other methods must pass `verify_token` or get `ProblemError(403, "CSRF validation failed")`.
- Cookie `paw_csrf`, header `x-csrf-token`; secret is `settings.session_secret`. See [[api#Errors (problem+json)]].

## Passwords
Passwords are hashed with argon2 (`security/passwords.py`) via a module-level `PasswordHasher`. `hash_password(plain)` returns the encoded hash; `verify_password(plain, hashed)` returns `True`/`False`, swallowing `VerifyMismatchError` and `VerificationError` rather than raising.

- No plaintext password is ever stored; only the argon2 hash on the user row in [[db#Models and tables]].

## Secrets
Provider API keys and other secrets are encrypted at rest with Fernet via `SecretBox` (`security/secrets.py`), constructed from the 44-char `fernet_key` env setting. `SecretBox.encrypt(plain)` produces a Fernet token stored as `api_key_enc`; `SecretBox.decrypt(token)` recovers it when building a provider.

- Encrypted on write in `provider_settings` (`api_key_enc=box.encrypt(api_key)`), decrypted in the provider factory. See [[providers#Secrets]].
- `fernet_key` is infra config, supplied via env — see [[architecture#Layered dependencies (no cycles)]].

## Uploads
`security/uploads.py` validates uploads before they are stored. `validate_text_upload` enforces an extension allow-list (`.md/.txt/.markdown`), a `max_bytes` cap, and UTF-8 decodability. `validate_source_upload` covers more types, returning a `kind`, and raises `UploadRejected` on any failure.

- Text/HTML extensions must decode as UTF-8; `.pdf` must start with `%PDF-`; `.docx` / `.epub` must start with the ZIP magic `PK\x03\x04` (and then pass the zip guard below).
- Images (`.jpg/.jpeg/.png/.webp`) are checked by magic bytes (`_IMAGE_MAGIC`: JFIF/PNG signatures, `RIFF…WEBP` container) and return `kind="image"`, routed to the vision path rather than a text loader.
- Empty or oversized files are rejected. The returned `kind` (`md`/`pdf`/`docx`/`html`/`epub`/`image`) drives the loader choice in [[ingest#Loaders]].

## Zip guard
`inspect_zip(data, *, max_total, max_entries, max_ratio)` is a **metadata-only** anti-zip-bomb and path-traversal guard: it reads only the central-directory entries via `zipfile.infolist()` and **never decompresses**. It runs on every zip-backed upload (`.docx`, `.epub`, and bulk `.zip`) through the `_guard_zip` helper, which pulls the `max_unzip_bytes` / `max_unzip_entries` / `max_compression_ratio` caps from [[architecture#Config layering (env ⊕ DB)]].

- Rejects archives over `max_entries`, whose summed `file_size` exceeds `max_total`, or whose per-entry `file_size / compress_size` ratio exceeds `max_ratio` (decompression-bomb signal).
- Rejects absolute paths (leading `/` or a Windows drive prefix), `..` path-traversal segments, and **nested archives** (`.zip/.docx/.epub` members).
- Bulk uploads call `inspect_zip` once up front, then re-open the archive to register each member as a source — see [[services#SourceService]].

## Sanitize
`security/sanitize.py` renders user markdown and then strips dangerous HTML. `render_markdown` runs mistune (tables + strikethrough) and passes the result through `nh3.clean` with a strict tag/attribute allow-list (`_ALLOWED_TAGS`, `_ALLOWED_ATTRS` — only `href/title` on `a`, `src/alt/title` on `img`).

- `[[slug]]` / `[[slug|label]]` wikilinks are extracted (`extract_wikilink_targets`) and resolved to article links (`resolve_wikilinks`) **before** rendering; unknown slugs degrade to plain text. See [[services#ArticleService]].

## API keys

`security/api_keys.py` provides the crypto primitives for MCP Bearer tokens. A key has the shape `paw_<prefix>.<secret>` where `prefix` is 8 hex chars (`secrets.token_hex(4)`) and `secret` is a urlsafe-base64 string (`secrets.token_urlsafe(32)`). Only the SHA-256 hash of the secret is stored — the prefix is the non-secret lookup handle. `verify_secret` uses `hmac.compare_digest` for constant-time comparison; importantly the verification runs before the revocation check, so timing cannot distinguish wrong-secret from revoked. The only current scope is `"read"` (`MCP_REQUIRED_SCOPE`).

- `generate_key()` → `(prefix, secret, full_token)` — caller stores `hash_secret(secret)`, shows `full_token` to the user once.
- `parse_bearer(authorization)` → `(prefix, secret) | None` — strips the `Bearer paw_` prefix and splits on `.`.
- `ApiKeyService` (`services/api_keys.py`) is the commit boundary: `issue` and `revoke` each call `session.commit()` once; `authenticate` also commits after `touch_last_used`. `list` is read-only.
- `MCPAuthMiddleware` gates all `/mcp` requests: 401 if authentication fails, 403 if `"read"` scope is absent. See [[mcp#Auth & mount]] and [[api#Api-keys router]].

## SSRF guard
`security/ssrf.py` makes server-side URL fetches safe for the `url` source type ([[ingest#Loaders]], [[services#SourceService]]). `validate_url(url, *, allowlist)` enforces **https-only**, an optional host-suffix allowlist (`host == s or host.endswith("." + s)`), then resolves the host with `socket.getaddrinfo` and rejects any address that `_ip_is_blocked` flags — non-global, private, loopback, link-local, reserved, multicast or unspecified. It returns the validated host or raises `SsrfRejected`.

- `safe_get(url, *, max_bytes, allowlist)` streams the body with `httpx` and `follow_redirects=False`: it **re-validates every hop** (max `_MAX_HOPS=5`), rejects non-2xx, and aborts once the streamed size would exceed `max_bytes` — so a redirect cannot bounce the fetch to an internal address and a large body cannot exhaust memory.
- Caps come from the env layer: `url_allowlist` (parsed by `config.parse_allowlist`) and `max_url_bytes`. `SsrfRejected` surfaces at the router as a 422 — see [[api#Sources router]].

## Headers
A CSP / security-headers middleware is wired in `main.py::create_app()` alongside the routers and `/health`, so every response carries a Content-Security-Policy and related hardening headers. The finalized policy is `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; object-src 'none'` — clickjacking-proof (`frame-ancestors 'none'`), forms pinned to same-origin (`form-action 'self'`), and plugins disabled (`object-src 'none'`). See [[architecture#create_app() wiring]] for where the middleware sits in the stack and [[api#Dependency helpers (deps.py)]] for the per-route guards above.

## Cypher injection
The optional Apache AGE graph engine (see [[graph#AGE graph engine]]) executes Cypher against per-domain graphs, so it adds a query-injection surface that `graph/age/cypher.py` closes by construction. The Cypher body is always a fixed dollar-quoted literal; every user-derived value (article titles, entity names, seed ids) is passed through AGE's `parameters` agtype argument (`agtype_params` → `json.dumps` → `CAST(:p AS agtype)`), never string-interpolated. A malicious title such as `$$ ) MATCH (x) DETACH DELETE x //` is therefore stored and returned as inert data — proven by test.

- The only code-interpolated values are the graph name (validated by `naming.py::assert_graph_name` against `^g_[0-9a-f]{32}$` before any f-string) and the integer `expand_depth` bound; both are derived, never user input.
- One AGE graph per domain means there is no `domain_id` filter to forget — a domain's Cypher cannot reach another domain's nodes, closing cross-domain leakage by construction.
- Graph DDL (`create_graph`, labels) is committed separately at domain creation / rebuild, never mid-write, avoiding AGE's non-autocommit visibility pitfall. Any AGE failure degrades to the CTE retrieval path — the API never surfaces a graph-engine error.
