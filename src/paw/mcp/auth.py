from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from paw.db.session import get_sessionmaker
from paw.security.api_keys import MCP_REQUIRED_SCOPE
from paw.services.api_keys import ApiKeyService

_GUARD_PREFIX = "/mcp"


class MCPAuthMiddleware:
    """Pure-ASGI guard for /mcp: Bearer api-key auth + required-scope check.

    Streaming-safe: it never wraps the downstream response (unlike
    BaseHTTPMiddleware), so Streamable-HTTP/SSE bodies stream untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(_GUARD_PREFIX):
            await self.app(scope, receive, send)
            return

        authorization: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                authorization = value.decode("latin-1")
                break

        async with get_sessionmaker()() as session:
            authed = await ApiKeyService(session).authenticate(authorization)

        if authed is None:
            await _problem(send, 401, "Unauthorized")
            return
        if MCP_REQUIRED_SCOPE not in authed.scopes:
            await _problem(send, 403, "Forbidden")
            return
        await self.app(scope, receive, send)


async def _problem(send: Send, status: int, title: str) -> None:
    body = json.dumps({"type": "about:blank", "title": title, "status": status}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
