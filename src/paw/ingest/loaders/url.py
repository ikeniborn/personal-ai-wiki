from __future__ import annotations

from paw.ingest.loaders.html import load
from paw.security.ssrf import safe_get


async def load_url(url: str, *, allowlist: list[str], max_bytes: int) -> str:
    html_bytes = await safe_get(url, max_bytes=max_bytes, allowlist=allowlist)
    out = load(html_bytes).strip()
    if not out:
        raise ValueError("url produced no extractable text")
    return out
