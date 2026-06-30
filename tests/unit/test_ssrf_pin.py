import httpx
import pytest

from paw.security import ssrf
from paw.security.ssrf import SsrfRejected, safe_get, validate_url


def test_validate_url_returns_host_and_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    host, ip = validate_url("https://example.com/x", allowlist=[])
    assert host == "example.com"
    assert ip == "93.184.216.34"


def test_validate_url_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(SsrfRejected):
        validate_url("https://internal.example.com/x", allowlist=[])


async def test_safe_get_connects_to_pinned_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_header"] = request.headers["host"]
        seen["url_host"] = request.url.host
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"hello")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    body = await safe_get("https://example.com/p", max_bytes=1024, allowlist=[], client=client)
    assert body == b"hello"
    assert seen["url_host"] == "93.184.216.34"
    assert seen["host_header"] == "example.com"
    assert seen["sni"] == "example.com"
