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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await safe_get("https://example.com/p", max_bytes=1024, allowlist=[], client=client)
    assert body == b"hello"
    assert seen["url_host"] == "93.184.216.34"
    assert seen["host_header"] == "example.com"
    assert seen["sni"] == "example.com"


async def test_safe_get_host_header_includes_non_default_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 8443))],
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_header"] = request.headers["host"]
        seen["url"] = str(request.url)
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"hello")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await safe_get(
            "https://example.com:8443/p",
            max_bytes=1024,
            allowlist=[],
            client=client,
        )
    assert body == b"hello"
    assert seen["url"] == "https://93.184.216.34:8443/p"
    assert seen["host_header"] == "example.com:8443"
    assert seen["sni"] == "example.com"


async def test_safe_get_host_header_preserves_ipv6_literal_brackets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip = "2606:4700:4700::1111"
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(10, 1, 6, "", (ip, 443))],
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_header"] = request.headers["host"]
        seen["url_host"] = request.url.host
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"hello")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await safe_get(
            f"https://[{ip}]/p",
            max_bytes=1024,
            allowlist=[],
            client=client,
        )
    assert body == b"hello"
    assert seen["url_host"] == ip
    assert seen["host_header"] == f"[{ip}]"
    assert seen["sni"] == ip


async def test_safe_get_host_header_preserves_ipv6_literal_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip = "2606:4700:4700::1111"
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(10, 1, 6, "", (ip, 8443))],
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host_header"] = request.headers["host"]
        seen["url"] = str(request.url)
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"hello")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await safe_get(
            f"https://[{ip}]:8443/p",
            max_bytes=1024,
            allowlist=[],
            client=client,
        )
    assert body == b"hello"
    assert seen["url"] == f"https://[{ip}]:8443/p"
    assert seen["host_header"] == f"[{ip}]:8443"
    assert seen["sni"] == ip


async def test_safe_get_default_client_disables_keepalive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    original_client = httpx.AsyncClient
    seen: dict[str, httpx.Limits | bool | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello")

    class FakeClient(original_client):
        def __init__(self, *args, **kwargs) -> None:
            seen["limits"] = kwargs.get("limits")
            seen["follow_redirects"] = kwargs.get("follow_redirects")
            super().__init__(
                *args,
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

    monkeypatch.setattr(ssrf.httpx, "AsyncClient", FakeClient)

    body = await safe_get("https://example.com/p", max_bytes=1024, allowlist=[])

    assert body == b"hello"
    assert isinstance(seen["limits"], httpx.Limits)
    assert seen["limits"].max_keepalive_connections == 0
    assert seen["follow_redirects"] is False
