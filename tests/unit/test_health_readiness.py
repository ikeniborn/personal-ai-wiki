from httpx import ASGITransport, AsyncClient

import paw.obs.readiness as readiness
from paw.main import create_app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_liveness_is_trivial():
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_ok(monkeypatch):
    async def fake_check():
        return True, {"db": "ok", "redis": "ok"}

    monkeypatch.setattr(readiness, "check_readiness", fake_check)
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health", params={"ready": "1"})
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


async def test_readiness_degraded_returns_503(monkeypatch):
    async def fake_check():
        return False, {"db": "ok", "redis": "error: down"}

    monkeypatch.setattr(readiness, "check_readiness", fake_check)
    app = create_app()
    async with _client(app) as c:
        resp = await c.get("/health", params={"ready": "1"})
    assert resp.status_code == 503
    assert resp.json()["ready"] is False
