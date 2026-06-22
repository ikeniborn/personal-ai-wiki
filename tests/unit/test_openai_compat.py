from types import SimpleNamespace

from pydantic import BaseModel

from paw.providers.base import Message
from paw.providers.openai_compat import OpenAICompatProvider


class _FakeCompletions:
    def __init__(self, response: object) -> None:
        self._response = response
        self.last_kwargs: dict[str, object] = {}

    async def create(self, **kwargs: object) -> object:
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, *, chat_response: object = None, embed_response: object = None) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(chat_response))
        self.embeddings = _FakeCompletions(embed_response)


def _chat_response(content: str | None, tool_calls: list[object] | None = None) -> object:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(model_dump=lambda: {"total_tokens": 10, "cost": None})
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_chat_maps_plain_content():
    client = _FakeClient(chat_response=_chat_response("hello"))
    p = OpenAICompatProvider(
        base_url="http://x", api_key="k", chat_model="m", embedding_model="e", client=client
    )
    res = await p.chat([Message(role="user", content="hi")])
    assert res.content == "hello"
    assert res.usage == {"total_tokens": 10}
    assert res.finish_reason == "stop"


async def test_chat_maps_tool_calls():
    fn = SimpleNamespace(name="emit_result", arguments='{"title": "Q", "score": 3}')
    tc = SimpleNamespace(id="c1", function=fn)
    client = _FakeClient(chat_response=_chat_response(None, [tc]))
    p = OpenAICompatProvider(
        base_url="http://x", api_key="k", chat_model="m", embedding_model="e", client=client
    )
    res = await p.chat([Message(role="user", content="hi")])
    assert res.tool_calls[0].name == "emit_result"
    assert res.tool_calls[0].arguments == {"title": "Q", "score": 3}


async def test_embed_maps_vectors():
    data = [SimpleNamespace(embedding=[0.1, 0.2]), SimpleNamespace(embedding=[0.3, 0.4])]
    client = _FakeClient(embed_response=SimpleNamespace(data=data))
    p = OpenAICompatProvider(
        base_url="http://x", api_key="k", chat_model="m", embedding_model="e", client=client
    )
    out = await p.embed(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]


class _Topic(BaseModel):
    title: str
    score: int


async def test_structured_uses_tool_call():
    fn = SimpleNamespace(name="emit_result", arguments='{"title": "Q", "score": 9}')
    tc = SimpleNamespace(id="c1", function=fn)
    client = _FakeClient(chat_response=_chat_response(None, [tc]))
    p = OpenAICompatProvider(
        base_url="http://x", api_key="k", chat_model="m", embedding_model="e", client=client
    )
    out = await p.structured([Message(role="user", content="x")], _Topic)
    assert out.score == 9
