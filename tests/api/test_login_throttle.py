from httpx import ASGITransport, AsyncClient

from paw.api.deps import get_redis
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


async def test_login_email_throttle_key_is_case_insensitive(wired_settings, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit", 2, raising=False)
    monkeypatch.setattr(get_settings(), "login_rate_window_seconds", 60, raising=False)
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        first = await c.post(
            "/api/v1/auth/login",
            json={"email": "Admin@example.com", "password": "wrongpassword1"},
        )
        second = await c.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "wrongpassword1"},
        )
        third = await c.post(
            "/api/v1/auth/login",
            json={"email": "ADMIN@example.com", "password": "wrongpassword1"},
        )
        assert first.status_code == 401
        assert second.status_code == 401
        assert third.status_code == 429
        redis = get_redis()
        assert await redis.zcard("ratelimit:login:email:admin@example.com") == 3
        assert await redis.exists("ratelimit:login:email:Admin@example.com") == 0
    finally:
        await c.aclose()
