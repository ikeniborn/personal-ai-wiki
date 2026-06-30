from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from paw.providers.base import ChatProvider, Message, ToolSpec


class StructuredError(Exception):
    """Raised when the model cannot produce schema-valid output within retries."""


def schema_tool(model: type[BaseModel], name: str = "emit_result") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"Return the result as a {model.__name__} object.",
        parameters=model.model_json_schema(),
    )


def _extract_payload(result: object, tool_name: str, *, use_tools: bool) -> dict[str, object]:
    from paw.providers.base import ChatResult

    if not isinstance(result, ChatResult):
        raise TypeError("expected ChatResult from provider")
    if use_tools:
        for tc in result.tool_calls:
            if tc.name == tool_name:
                return tc.arguments
        raise ValueError("model did not call the emit_result tool")
    if not result.content:
        raise ValueError("model returned empty content")
    parsed = json.loads(result.content)
    if not isinstance(parsed, dict):
        raise ValueError("json content is not an object")
    return parsed


async def coerce_structured[M: BaseModel](
    chat: ChatProvider,
    messages: list[Message],
    model_cls: type[M],
    *,
    model: str | None = None,
    retries: int = 2,
    use_tools: bool = True,
) -> M:
    tool = schema_tool(model_cls)
    convo = list(messages)
    last_err = ""
    for _ in range(retries + 1):
        result = await chat.chat(
            convo,
            tools=[tool] if use_tools else None,
            model=model,
            json_mode=not use_tools,
        )
        try:
            payload = _extract_payload(result, tool.name, use_tools=use_tools)
            return model_cls.model_validate(payload)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            convo.append(
                Message(
                    role="user",
                    content=(
                        "Your previous output failed validation against the required "
                        f"schema. Error:\n{last_err}\nReturn a corrected object that "
                        "matches the schema exactly."
                    ),
                )
            )
    raise StructuredError(f"structured output failed after {retries} retries: {last_err}")
