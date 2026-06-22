from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec


def test_message_defaults():
    m = Message(role="user", content="hi")
    assert m.role == "user"
    assert m.content == "hi"
    assert m.tool_calls is None


def test_toolspec_holds_json_schema():
    spec = ToolSpec(
        name="emit",
        description="emit result",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
    )
    assert spec.parameters["type"] == "object"


def test_chatresult_groups_tool_calls():
    tc = ToolCall(id="c1", name="emit", arguments={"x": 1})
    res = ChatResult(content=None, tool_calls=[tc], finish_reason="tool_calls", usage={})
    assert res.tool_calls[0].arguments == {"x": 1}
    assert res.content is None
