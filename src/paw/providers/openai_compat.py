from __future__ import annotations

import json
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


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        supports_tools: bool = True,
        client: Any | None = None,
    ) -> None:
        self.chat_model = chat_model
        self.embedding_model = embedding_model
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

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        resp = await self._client.embeddings.create(
            model=model or self.embedding_model, input=texts
        )
        return [list(d.embedding) for d in resp.data]

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
