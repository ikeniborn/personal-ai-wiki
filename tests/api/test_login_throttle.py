from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.main import create_app


async def test_login_throttled_after_limit(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit", 3, raising=False)
    monkeypatch.setattr(get_settings(), "login_rate_window_seconds", 60, raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        last = None
        for _ in range(5):
            last = await c.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrongpassword1"},
            )
        assert last is not None and last.status_code == 429
    finally:
        await c.aclose()
