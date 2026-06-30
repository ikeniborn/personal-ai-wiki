from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from paw.config import get_settings
from paw.main import create_app


async def test_metrics_disabled_when_token_unset(wired_settings: None) -> None:
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        assert (await c.get("/metrics")).status_code == 404
    finally:
        await c.aclose()


async def test_metrics_requires_bearer_token(
    wired_settings: None, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        assert (await c.get("/metrics")).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert b"http_requests" in ok.content or ok.content
    finally:
        await c.aclose()
