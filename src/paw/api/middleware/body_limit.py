from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_TOO_LARGE = b'{"title":"Payload too large","status":413}'


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` at the ASGI layer.

    Checks Content-Length up front, then counts streamed bytes so chunked
    uploads cannot bypass the cap. Runs before any handler reads the body.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._has_oversized_or_ambiguous_content_length(scope):
            await self._reject(send)
            return

        replay_messages: list[Message] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                replay_messages.append(message)
                break

            body = message.get("body", b"")
            received += len(body)
            if received > self.max_bytes:
                await self._reject(send)
                return

            replay_messages.append(message)
            if not message.get("more_body", False):
                break

        replay = iter(replay_messages)

        async def replay_receive() -> Message:
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        await self.app(scope, replay_receive, send)

    def _has_oversized_or_ambiguous_content_length(self, scope: Scope) -> bool:
        content_lengths = [
            value
            for name, value in scope.get("headers") or []
            if name.lower() == b"content-length"
        ]
        if not content_lengths:
            return False

        parsed_values: list[int] = []
        for value in content_lengths:
            if not value.isdigit():
                return True
            parsed_values.append(int(value))

        if any(value > self.max_bytes for value in parsed_values):
            return True
        return len(set(parsed_values)) > 1

    async def _reject(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/problem+json")],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE})
