from collections.abc import Iterable

from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

from paw.api.middleware.body_limit import BodySizeLimitMiddleware
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password


async def _call_body_limit(
    *,
    max_bytes: int,
    headers: Iterable[tuple[bytes, bytes]] = (),
    request_messages: Iterable[Message] = (),
) -> list[Message]:
    sent: list[Message] = []
    messages = iter(request_messages)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": list(headers),
    }

    async def receive() -> Message:
        return next(messages, {"type": "http.request", "body": b"", "more_body": False})

    async def send(message: Message) -> None:
        sent.append(message)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.body", "body": b"ok"})

    await BodySizeLimitMiddleware(app, max_bytes=max_bytes)(scope, receive, send)
    return sent


async def test_streamed_body_over_limit_is_rejected_before_downstream_response() -> None:
    sent = await _call_body_limit(
        max_bytes=3,
        request_messages=[
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
        ],
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert sent[1]["type"] == "http.response.body"
    assert sent[1]["body"] == b'{"title":"Payload too large","status":413}'


async def test_content_length_over_limit_is_rejected_case_insensitively() -> None:
    sent = await _call_body_limit(
        max_bytes=3,
        headers=[(b"Content-Length", b"4")],
    )

    assert sent[0]["status"] == 413


async def test_conflicting_duplicate_content_length_headers_are_rejected() -> None:
    sent = await _call_body_limit(
        max_bytes=3,
        headers=[(b"Content-Length", b"4"), (b"content-length", b"1")],
    )

    assert sent[0]["status"] == 413


async def test_streamed_body_under_limit_is_replayed_to_downstream_app() -> None:
    sent = await _call_body_limit(
        max_bytes=4,
        request_messages=[
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": False},
        ],
    )

    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"ok"


async def _admin_client(db_session: AsyncSession) -> AsyncClient:
    await UserRepo(db_session).create(
        email="a@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    c = AsyncClient(transport=ASGITransport(app=app), base_url="https://t")
    login_resp = await c.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "pw12345678901"},
    )
    assert login_resp.status_code == 200
    assert c.cookies.get("paw_csrf") is not None
    return c


async def test_oversized_upload_rejected_with_413(
    db_session: AsyncSession, wired_settings: object, monkeypatch: MonkeyPatch
) -> None:
    from paw.config import get_settings

    monkeypatch.setattr(get_settings(), "max_request_bytes", 1024, raising=False)
    c = await _admin_client(db_session)
    try:
        csrf = c.cookies.get("paw_csrf")
        assert csrf is not None
        domain_resp = await c.post(
            "/api/v1/domains",
            json={"name": "d"},
            headers={"x-csrf-token": csrf},
        )
        assert domain_resp.status_code == 201
        dom = domain_resp.json()
        big = b"x" * 4096
        resp = await c.post(
            f"/api/v1/domains/{dom['id']}/sources",
            headers={"x-csrf-token": csrf},
            files={"file": ("big.md", big, "text/markdown")},
        )
        assert resp.status_code == 413
    finally:
        await c.aclose()
