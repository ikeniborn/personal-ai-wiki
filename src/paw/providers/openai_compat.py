from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec
from paw.providers.structured import coerce_structured


def _message_to_dict(m: Message) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role}
    if m.content is not None:
        d["content"] = m.content
    if m.name is not None:
        d["name"] = m.name
    if m.tool_call_id is not None:
        d["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in m.tool_calls
        ]
    return d


def _tool_to_dict(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
    }


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        vision_model: str | None = None,
        supports_tools: bool = True,
        client: Any | None = None,
    ) -> None:
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.vision_model = vision_model
        self.supports_tools = supports_tools
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._client = client

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "model": model or self.chat_model,
            "messages": [_message_to_dict(m) for m in messages],
        }
        if tools:
            kwargs["tools"] = [_tool_to_dict(t) for t in tools]
            kwargs["tool_choice"] = "required"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
            )
        usage = resp.usage.model_dump() if resp.usage is not None else {}
        return ChatResult(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage={k: int(v) for k, v in usage.items() if isinstance(v, int)},
        )

    async def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": model or self.chat_model,
            "messages": [_message_to_dict(m) for m in messages],
            "stream": True,
        }
        resp = await self._client.chat.completions.create(**kwargs)
        async for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        resp = await self._client.embeddings.create(
            model=model or self.embedding_model, input=texts
        )
        return [list(d.embedding) for d in resp.data]

    async def describe(self, image: bytes, *, prompt: str, model: str | None = None) -> str:
        b64 = base64.b64encode(image).decode("ascii")
        mime = _image_mime(image)
        resp = await self._client.chat.completions.create(
            model=model or self.vision_model or self.chat_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        return resp.choices[0].message.content or ""

    async def structured[M: BaseModel](
        self,
        messages: list[Message],
        schema: type[M],
        *,
        model: str | None = None,
        retries: int = 2,
    ) -> M:
        return await coerce_structured(
            self,
            messages,
            schema,
            model=model,
            retries=retries,
            use_tools=self.supports_tools,
        )
