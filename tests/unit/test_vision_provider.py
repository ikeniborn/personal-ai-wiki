from paw.providers.config import ProviderConfig
from paw.providers.factory import build_vision_provider
from paw.providers.openai_compat import OpenAICompatProvider


class _FakeMsg:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)
        self.finish_reason = "stop"


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeCompletions:
    def __init__(self):
        self.captured = None

    async def create(self, **kwargs):
        self.captured = kwargs
        return _FakeResp("Extracted: hello sign")


class _FakeChat:
    def __init__(self, comp):
        self.completions = comp


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat(_FakeCompletions())


class _FakeSecretBox:
    def decrypt(self, token):
        return f"plain:{token}"


async def test_describe_builds_image_message():
    client = _FakeClient()
    p = OpenAICompatProvider(
        base_url="x",
        api_key="x",
        chat_model="c",
        embedding_model="e",
        vision_model="v",
        client=client,
    )
    out = await p.describe(b"\x89PNG\r\n\x1a\n imagebytes", prompt="Read the text")
    assert out == "Extracted: hello sign"
    msgs = client.chat.completions.captured["messages"]
    assert client.chat.completions.captured["model"] == "v"
    parts = msgs[0]["content"]
    assert parts[0]["text"] == "Read the text"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_describe_uses_image_mime_from_magic():
    client = _FakeClient()
    p = OpenAICompatProvider(
        base_url="x",
        api_key="x",
        chat_model="c",
        embedding_model="e",
        vision_model="v",
        client=client,
    )
    await p.describe(b"\xff\xd8\xff jpegbytes", prompt="Read the text")
    msgs = client.chat.completions.captured["messages"]
    parts = msgs[0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_vision_provider_returns_none_without_vision_model():
    pc = ProviderConfig(
        base_url="https://llm.example",
        api_key_enc="secret",
        chat_model="chat",
        embedding_model="embed",
        vision_model=None,
        embedding_dim=3,
    )
    assert build_vision_provider(pc, _FakeSecretBox()) is None


def test_build_vision_provider_returns_none_with_blank_vision_model():
    pc = ProviderConfig(
        base_url="https://llm.example",
        api_key_enc="secret",
        chat_model="chat",
        embedding_model="embed",
        vision_model="",
        embedding_dim=3,
    )
    assert build_vision_provider(pc, _FakeSecretBox()) is None


def test_build_vision_provider_carries_vision_model():
    pc = ProviderConfig(
        base_url="https://llm.example",
        api_key_enc="secret",
        chat_model="chat",
        embedding_model="embed",
        vision_model="vision",
        embedding_dim=3,
    )
    provider = build_vision_provider(pc, _FakeSecretBox())
    assert provider is not None
    assert provider.vision_model == "vision"
