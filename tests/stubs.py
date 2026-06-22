from __future__ import annotations

import hashlib
from collections.abc import Callable

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec


class StubChatProvider:
    def __init__(
        self,
        script: list[ChatResult] | None = None,
        *,
        responder: Callable[[list[Message], list[ToolSpec] | None], ChatResult] | None = None,
    ) -> None:
        self._script = list(script or [])
        self._responder = responder
        self.calls: list[list[Message]] = []

    @staticmethod
    def text(content: str) -> ChatResult:
        return ChatResult(content=content, finish_reason="stop")

    @staticmethod
    def tool(name: str, args: dict[str, object]) -> ChatResult:
        return ChatResult(
            content=None,
            tool_calls=[ToolCall(id="stub", name=name, arguments=args)],
            finish_reason="tool_calls",
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if self._responder is not None:
            return self._responder(messages, tools)
        return self._script.pop(0)


class StubEmbeddingProvider:
    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
            for b in digest:
                out.append(b / 256.0)
                if len(out) == self.dim:
                    break
            counter += 1
        return out

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        return [self._vec(t) for t in texts]
