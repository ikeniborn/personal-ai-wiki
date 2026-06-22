from paw.security.csrf import issue_token, verify_token


def test_valid_token_verifies():
    secret = "s" * 32
    t = issue_token(secret)
    assert verify_token(secret, t, t) is True


def test_mismatched_cookie_and_header_fails():
    secret = "s" * 32
    a = issue_token(secret)
    b = issue_token(secret)
    assert verify_token(secret, a, b) is False


def test_tampered_token_fails():
    secret = "s" * 32
    t = issue_token(secret)
    assert verify_token(secret, t, t + "x") is False
