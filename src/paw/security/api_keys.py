from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "paw_"
# Allowlisted scopes a key may carry (Phase 8 is read-only).
API_KEY_SCOPES: tuple[str, ...] = ("read",)
# Scope every MCP request must present.
MCP_REQUIRED_SCOPE = "read"


def generate_key() -> tuple[str, str, str]:
    """Return (prefix, secret, full_token). Token is shown to the user once."""
    prefix = secrets.token_hex(4)  # 8 hex chars, no '.'
    secret = secrets.token_urlsafe(32)  # urlsafe base64, no '.'
    return prefix, secret, f"{KEY_PREFIX}{prefix}.{secret}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def parse_bearer(authorization: str | None) -> tuple[str, str] | None:
    """Parse 'Bearer paw_<prefix>.<secret>' -> (prefix, secret), else None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.startswith(KEY_PREFIX):
        return None
    body = value[len(KEY_PREFIX):]
    prefix, sep, secret = body.partition(".")
    if not sep or not prefix or not secret:
        return None
    return prefix, secret
