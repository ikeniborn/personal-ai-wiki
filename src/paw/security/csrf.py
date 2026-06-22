import hashlib
import hmac
import secrets


def issue_token(secret: str) -> str:
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{nonce}.{sig}"


def _valid(secret: str, token: str) -> bool:
    nonce, _, sig = token.partition(".")
    if not nonce or not sig:
        return False
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig)


def verify_token(secret: str, cookie_token: str, header_token: str) -> bool:
    # double-submit: cookie and submitted token must match AND be authentic
    if not cookie_token or not header_token:
        return False
    if not hmac.compare_digest(cookie_token, header_token):
        return False
    return _valid(secret, cookie_token)
