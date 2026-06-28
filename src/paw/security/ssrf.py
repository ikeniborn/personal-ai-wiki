from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class SsrfRejected(Exception):
    pass


_MAX_HOPS = 5
_TIMEOUT = httpx.Timeout(5.0, connect=5.0)


def _ip_is_blocked(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        not addr.is_global
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str, *, allowlist: list[str]) -> str:
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
    return host


async def safe_get(url: str, *, max_bytes: int, allowlist: list[str]) -> bytes:
    current = url
    redirects = 0
    async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT) as client:
        while True:
            validate_url(current, allowlist=allowlist)
            async with client.stream("GET", current) as resp:
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
