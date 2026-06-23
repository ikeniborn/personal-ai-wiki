from types import SimpleNamespace

from tests.stubs import StubChatProvider

from paw.providers.base import Message
from paw.providers.openai_compat import OpenAICompatProvider


async def test_stub_stream_yields_tokens():
    stub = StubChatProvider(stream_tokens=["Hel", "lo", " world"])
    out = [tok async for tok in stub.stream([Message(role="user", content="hi")])]
    assert out == ["Hel", "lo", " world"]


class _FakeStream:
    def __init__(self, deltas):
        self._deltas = deltas

    def __aiter__(self):
        async def gen():
            for d in self._deltas:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=d))]
                )
        return gen()


class _FakeClient:
    def __init__(self, deltas):
        async def create(**kwargs):
            assert kwargs["stream"] is True
            return _FakeStream(deltas)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


async def test_openai_compat_stream_parses_deltas():
    client = _FakeClient(["a", "b", None, "c"])
    p = OpenAICompatProvider(
        base_url="x", api_key="x", chat_model="m", embedding_model="e", client=client
    )
    out = [tok async for tok in p.stream([Message(role="user", content="q")])]
    assert out == ["a", "b", "c"]  # None delta skipped
