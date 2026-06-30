from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from paw.config import get_settings
from paw.main import create_app


async def _get_metrics(authorization: str | None = None) -> tuple[int, bytes]:
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    try:
        headers = {"Authorization": authorization} if authorization is not None else {}
        response = await c.get("/metrics", headers=headers)
        return response.status_code, response.content
    finally:
        await c.aclose()


async def test_metrics_disabled_when_token_unset(wired_settings: None) -> None:
    status, _ = await _get_metrics()
    assert status == 404


async def test_metrics_requires_bearer_token(
    wired_settings: None, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)

    for authorization in (None, "Bearer wrong", "Basic s3cret"):
        status, _ = await _get_metrics(authorization)
        assert status == 401

    status, content = await _get_metrics("Bearer s3cret")
    assert status == 200
    assert b"paw_http_requests_total" in content


async def test_metrics_accepts_case_insensitive_bearer_scheme(
    wired_settings: None, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)

    status, content = await _get_metrics("bearer s3cret")

    assert status == 200
    assert b"paw_http_requests_total" in content
