import httpx
from httpx import ASGITransport

from paw.main import create_app


async def test_csp_header_finalized():
    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/health")

    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "object-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
