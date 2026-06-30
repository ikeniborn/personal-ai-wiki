from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.main import create_app


async def test_setup_throttled_after_limit(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit", 2, raising=False)
    monkeypatch.setattr(get_settings(), "login_rate_window_seconds", 60, raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        payload = {
            "email": "x@example.com",
            "password": "pw12345678901",
            "base_url": "https://api.example.com",
            "api_key": "k",
            "chat_model": "m",
            "embedding_model": "e",
            "embedding_dim": 8,
        }
        first = await c.post("/api/v1/setup", json=payload)
        second = await c.post("/api/v1/setup", json=payload)
        third = await c.post("/api/v1/setup", json=payload)
        assert first.status_code == 201
        assert second.status_code == 409
        assert third.status_code == 429
    finally:
        await c.aclose()
