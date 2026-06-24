from paw.security.api_keys import (
    KEY_PREFIX,
    generate_key,
    hash_secret,
    parse_bearer,
    verify_secret,
)


def test_generate_key_shape():
    prefix, secret, token = generate_key()
    assert token == f"{KEY_PREFIX}{prefix}.{secret}"
    assert "." not in prefix and "." not in secret


def test_parse_bearer_roundtrip():
    prefix, secret, token = generate_key()
    assert parse_bearer(f"Bearer {token}") == (prefix, secret)
    assert parse_bearer(f"bearer {token}") == (prefix, secret)  # scheme is case-insensitive


def test_parse_bearer_rejects_bad_inputs():
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer nope") is None              # missing paw_ prefix
    assert parse_bearer("Bearer paw_onlyprefix") is None    # missing '.secret'
    assert parse_bearer("Bearer paw_.secret") is None       # empty prefix


def test_verify_secret():
    _, secret, _ = generate_key()
    h = hash_secret(secret)
    assert verify_secret(secret, h) is True
    assert verify_secret("wrong-secret", h) is False
