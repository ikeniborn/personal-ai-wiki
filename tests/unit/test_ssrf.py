import socket

import httpx
import pytest

from paw.security.ssrf import SsrfRejected, safe_get, validate_url


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
    _patch_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(SsrfRejected):
        validate_url("https://metadata.example/x", allowlist=[])


def test_rejects_shared_non_global_address(monkeypatch):
    _patch_resolve(monkeypatch, "100.64.0.1")
    with pytest.raises(SsrfRejected):
        validate_url("https://shared.example/x", allowlist=[])


def test_rejects_ipv6_ula(monkeypatch):
    _patch_resolve(monkeypatch, "fd00::1")
    with pytest.raises(SsrfRejected):
        validate_url("https://v6.example/x", allowlist=[])


@pytest.mark.parametrize(
    "url",
    ["https://example.com:0/x", "https://example.com:99999/x", "https://example.com:abc/x"],
)
def test_rejects_invalid_port(monkeypatch, url):
    _patch_resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(SsrfRejected):
        validate_url(url, allowlist=[])


def test_rejects_not_in_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    with pytest.raises(SsrfRejected):
        validate_url("https://evil.example/x", allowlist=["example.com"])


def test_accepts_public_in_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    assert (
        validate_url("https://docs.example.com/x", allowlist=["example.com"])
        == ("docs.example.com", "93.184.216.34")
    )


def test_accepts_public_empty_allowlist(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    assert validate_url("https://example.com/x", allowlist=[]) == (
        "example.com",
        "93.184.216.34",
    )


def _patch_client(monkeypatch, handler):
    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("follow_redirects", None)
            kwargs.pop("timeout", None)
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


async def test_safe_get_allows_five_redirect_hops(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    redirects = {
        "/0": "https://example.com/1",
        "/1": "https://example.com/2",
        "/2": "https://example.com/3",
        "/3": "https://example.com/4",
        "/4": "https://example.com/5",
    }
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen_paths.append(path)
        if path in redirects:
            return httpx.Response(302, headers={"location": redirects[path]})
        return httpx.Response(200, content=b"ok")

    _patch_client(monkeypatch, handler)
    assert await safe_get("https://example.com/0", max_bytes=10, allowlist=[]) == b"ok"
    assert seen_paths == ["/0", "/1", "/2", "/3", "/4", "/5"]


async def test_safe_get_rejects_response_over_cap(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, lambda request: httpx.Response(200, content=b"toolarge"))

    with pytest.raises(SsrfRejected, match="response too large"):
        await safe_get("https://example.com/x", max_bytes=3, allowlist=[])


async def test_safe_get_rejects_non_success(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, lambda request: httpx.Response(500, content=b"no"))

    with pytest.raises(SsrfRejected, match="non-success status: 500"):
        await safe_get("https://example.com/x", max_bytes=10, allowlist=[])


async def test_safe_get_rejects_redirect_without_location(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    _patch_client(monkeypatch, lambda request: httpx.Response(302))

    with pytest.raises(SsrfRejected, match="redirect without location"):
        await safe_get("https://example.com/x", max_bytes=10, allowlist=[])


async def test_safe_get_revalidates_redirect_target(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")
    _patch_client(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"location": "http://example.com/x"}),
    )

    with pytest.raises(SsrfRejected, match="only https urls are allowed"):
        await safe_get("https://example.com/x", max_bytes=10, allowlist=[])


async def test_safe_get_rejects_sixth_redirect_hop(monkeypatch):
    _patch_resolve(monkeypatch, "93.184.216.34")

    def handler(request: httpx.Request) -> httpx.Response:
        current = int(str(request.url).rsplit("/", 1)[-1])
        return httpx.Response(302, headers={"location": f"https://example.com/{current + 1}"})

    _patch_client(monkeypatch, handler)

    with pytest.raises(SsrfRejected, match="too many redirects"):
        await safe_get("https://example.com/0", max_bytes=10, allowlist=[])
