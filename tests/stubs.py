from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec


class StubChatProvider:
    def __init__(
        self,
        script: list[ChatResult] | None = None,
        *,
        responder: Callable[[list[Message], list[ToolSpec] | None], ChatResult] | None = None,
        stream_tokens: list[str] | None = None,
    ) -> None:
        self._script = list(script or [])
        self._responder = responder
        self._stream_tokens = list(stream_tokens or [])
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

    async def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        for tok in self._stream_tokens:
            yield tok

    async def structured[M: BaseModel](
        self,
        messages: list[Message],
        schema: type[M],
        *,
        model: str | None = None,
        retries: int = 2,
    ) -> M:
        from paw.providers.structured import coerce_structured

        return await coerce_structured(self, messages, schema, model=model, retries=retries)


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


class StubVisionProvider:
    def __init__(self, text: str = "described") -> None:
        self._text = text
        self.prompts: list[str] = []

    async def describe(
        self, image: bytes, *, prompt: str, model: str | None = None
    ) -> str:
        self.prompts.append(prompt)
        return self._text
