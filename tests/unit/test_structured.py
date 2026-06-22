import pytest
from pydantic import BaseModel

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec
from paw.providers.structured import StructuredError, coerce_structured, schema_tool


class Topic(BaseModel):
    title: str
    score: int


class _ScriptedChat:
    """Returns queued ChatResults in order; records calls for assertions."""

    def __init__(self, results: list[ChatResult]) -> None:
        self._results = list(results)
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        self.calls.append(list(messages))
        return self._results.pop(0)


def _tool_result(args: dict[str, object]) -> ChatResult:
    return ChatResult(
        content=None,
        tool_calls=[ToolCall(id="c", name="emit_result", arguments=args)],
        finish_reason="tool_calls",
    )


def test_schema_tool_embeds_json_schema():
    spec = schema_tool(Topic)
    assert spec.parameters["properties"]["score"]["type"] == "integer"


async def test_valid_first_try():
    chat = _ScriptedChat([_tool_result({"title": "QUIC", "score": 5})])
    out = await coerce_structured(chat, [Message(role="user", content="x")], Topic)
    assert out == Topic(title="QUIC", score=5)
    assert len(chat.calls) == 1


async def test_repairs_one_malformed_response():
    chat = _ScriptedChat(
        [
            _tool_result({"title": "QUIC", "score": "not-an-int"}),  # invalid
            _tool_result({"title": "QUIC", "score": 5}),  # repaired
        ]
    )
    out = await coerce_structured(chat, [Message(role="user", content="x")], Topic, retries=2)
    assert out.score == 5
    assert len(chat.calls) == 2
    # second call must include a repair message referencing the error
    assert any("score" in (m.content or "") for m in chat.calls[1])


async def test_gives_up_after_retries():
    chat = _ScriptedChat([_tool_result({"title": "x", "score": "bad"})] * 3)
    with pytest.raises(StructuredError):
        await coerce_structured(chat, [Message(role="user", content="x")], Topic, retries=2)


async def test_json_mode_fallback_parses_content():
    chat = _ScriptedChat(
        [ChatResult(content='{"title": "QUIC", "score": 7}', finish_reason="stop")]
    )
    out = await coerce_structured(chat, [Message(role="user", content="x")], Topic, use_tools=False)
    assert out.score == 7
