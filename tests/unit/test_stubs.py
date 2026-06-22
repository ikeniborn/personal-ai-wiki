from tests.stubs import StubChatProvider, StubEmbeddingProvider

from paw.providers.base import Message


async def test_stub_chat_scripted():
    chat = StubChatProvider([StubChatProvider.text("hi"), StubChatProvider.tool("emit", {"a": 1})])
    r1 = await chat.chat([Message(role="user", content="x")])
    r2 = await chat.chat([Message(role="user", content="y")])
    assert r1.content == "hi"
    assert r2.tool_calls[0].arguments == {"a": 1}
    assert len(chat.calls) == 2


async def test_stub_chat_responder():
    chat = StubChatProvider(responder=lambda msgs, tools: StubChatProvider.text(str(len(msgs))))
    r = await chat.chat([Message(role="user", content="x")])
    assert r.content == "1"


async def test_stub_embeddings_deterministic_and_dim():
    emb = StubEmbeddingProvider(dim=16)
    v1 = await emb.embed(["quic"])
    v2 = await emb.embed(["quic"])
    v3 = await emb.embed(["tcp"])
    assert len(v1[0]) == 16
    assert v1 == v2  # deterministic
    assert v1 != v3
