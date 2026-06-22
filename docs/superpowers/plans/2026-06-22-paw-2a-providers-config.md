---
review:
  plan_hash: 9a08e4988db867be
  spec_hash: 5a6ed65bca4c1452
  last_run: 2026-06-22
  phases:
    structure:     { status: passed }
    coverage:      { status: passed }
    dependencies:  { status: passed }
    verifiability: { status: passed }
    consistency:   { status: passed }
  findings:
    - id: F-001
      severity: WARNING
      section: "Task 3 / Self-Review"
      section_hash: 57e941e65a02989b
      verdict: open
      text: >-
        Spec §In scope/Providers states OpenAICompatProvider "implements all three"
        Protocols (incl. VisionProvider), but the plan implements only ChatProvider +
        EmbeddingProvider; VisionProvider stays Protocol-only (Task 1, line 40).
        Consistent with spec's deferral of image-OCR to Phase 9, but the literal
        "all three" claim is not met by this sub-plan. Likely acceptable scoping —
        recommend explicit deferral note.
    - id: F-002
      severity: WARNING
      section: "Task 4 / Task 5 (Config)"
      section_hash: 41a291f748b9fa42
      verdict: open
      text: >-
        Spec §Config (LLD §10) lists "Per-domain overrides via domains.config" as part
        of Config scope. The plan covers app_settings-level provider+wiki config only and
        does not address per-domain overrides, nor does Self-Review's "Out of scope" list
        explicitly defer them. Per-domain override storage plausibly belongs to a later
        plan (depends on domains table), but the deferral is implicit.
chain:
  intent: null
  spec: docs/superpowers/specs/2026-06-22-paw-phase-2-ingest-design.md
result_check:
  verdict: OK
  plan_hash: 9a08e4988db867be
  last_run: 2026-06-22
---

# Phase 2A — Providers + Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the OpenAI-compatible LLM provider layer (chat + embeddings + structured-output with repair/JSON-fallback), the `app_settings`-backed provider/wiki config schema (connection + models + embedding dim, Fernet-encrypted key), and a reusable stub-LLM test double — the foundation every later Phase 2 plan depends on.

**Architecture:** Provider Protocols in `providers/base.py` (data, not I/O); a single `OpenAICompatProvider` implements `ChatProvider` + `EmbeddingProvider` over an injectable `AsyncOpenAI` client. The structured-output repair loop is a pure, injectable helper (`coerce_structured`) so it is unit-testable without network. Connection/model/dim config lives as a typed pydantic model serialized into the existing `app_settings` singleton JSONB row; the provider API key is encrypted at rest with the existing `SecretBox` (Fernet) and decrypted only inside the factory at call-site.

**Tech Stack:** Python 3.12 · `openai` async SDK (pointed at a custom `base_url`) · pydantic v2 · `cryptography` Fernet (`SecretBox`, already present) · SQLAlchemy async (`app_settings` singleton) · pytest (`asyncio_mode=auto`, testcontainers for the settings-roundtrip integration test).

## Global Constraints

- Python `>=3.12`; package `paw`, src-layout under `src/paw/`. Add code under `src/paw/providers/`.
- Lint/type/test gates (must stay green): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest`.
- `mypy` runs in **strict** mode — every function fully typed, no untyped `dict` leakage in public signatures.
- ruff lint selects `E,F,I,UP,B`; line-length **100**.
- Transaction rule (project convention): **repos/helpers `flush()`, services `commit()`**. Never `commit()` in a repo.
- Secrets: provider API key stored **encrypted** (Fernet via `SecretBox(get_settings().fernet_key)`); decrypt only at call-site, never log it, never place it in agent/LLM context.
- New runtime dependency must be added to `pyproject.toml` `[project].dependencies` and locked with `uv lock`.
- Provider config is stored in the DB (`app_settings.settings`), **not** in env (`config.py` env layer is infra/secrets only, per LLD §10).

---

### Task 1: Provider base types + Protocols

**Files:**
- Create: `src/paw/providers/__init__.py`
- Create: `src/paw/providers/base.py`
- Test: `tests/unit/test_provider_base.py`
- Modify: `pyproject.toml` (add `openai` dependency)

**Interfaces:**
- Produces (consumed by Task 2,3 and plans 2C/2D):
  - `Message(role: str, content: str | None = None, tool_calls: list[ToolCall] | None = None, tool_call_id: str | None = None, name: str | None = None)`
  - `ToolSpec(name: str, description: str, parameters: dict[str, Any])` — `parameters` is a JSON-Schema object.
  - `ToolCall(id: str, name: str, arguments: dict[str, Any])`
  - `ChatResult(content: str | None, tool_calls: list[ToolCall], finish_reason: str, usage: dict[str, int])`
  - `class ChatProvider(Protocol)`: `async def chat(self, messages: list[Message], *, tools: list[ToolSpec] | None = None, model: str | None = None, json_mode: bool = False) -> ChatResult: ...`
  - `class EmbeddingProvider(Protocol)`: `async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...`
  - `class VisionProvider(Protocol)`: `async def describe(self, image: bytes, *, prompt: str, model: str | None = None) -> str: ...` (Protocol only — no implementation in Phase 2.)

- [ ] **Step 1: Add the `openai` dependency**

Edit `pyproject.toml`, add to `[project].dependencies` (after `"cryptography>=43.0",`):

```toml
    "openai>=1.55",
```

Then run:

```bash
uv lock && uv sync
```

Expected: lockfile updates, `openai` installed into `.venv`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_provider_base.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.providers'`

- [ ] **Step 4: Write the implementation**

Create `src/paw/providers/__init__.py` (empty):

```python
```

Create `src/paw/providers/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-Schema object


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


class ChatProvider(Protocol):
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> ChatResult: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...


class VisionProvider(Protocol):
    async def describe(
        self, image: bytes, *, prompt: str, model: str | None = None
    ) -> str: ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_provider_base.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/paw/providers tests/unit/test_provider_base.py
git commit -m "feat(providers): add provider base types + Protocols, openai dep"
```

---

### Task 2: Structured-output coercion (repair loop + JSON fallback)

**Files:**
- Create: `src/paw/providers/structured.py`
- Test: `tests/unit/test_structured.py`

**Interfaces:**
- Consumes: `Message`, `ToolSpec`, `ToolCall`, `ChatResult`, `ChatProvider` (Task 1).
- Produces (consumed by Task 3):
  - `class StructuredError(Exception)` — raised when validation still fails after `retries`.
  - `def schema_tool(model: type[BaseModel], name: str = "emit_result") -> ToolSpec` — builds a `ToolSpec` whose `parameters` is `model.model_json_schema()`.
  - `async def coerce_structured(chat: ChatProvider, messages: list[Message], model_cls: type[M], *, model: str | None = None, retries: int = 2, use_tools: bool = True) -> M` — runs the loop: ask the LLM to emit the schema (via tool-call when `use_tools`, else JSON-mode), validate with pydantic, and on failure append the validation error as a `user` message and retry up to `retries` times.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_structured.py`:

```python
import pytest
from pydantic import BaseModel

from paw.providers.base import ChatProvider, ChatResult, Message, ToolCall, ToolSpec
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
    out = await coerce_structured(
        chat, [Message(role="user", content="x")], Topic, use_tools=False
    )
    assert out.score == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_structured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.providers.structured'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/providers/structured.py`:

```python
from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from paw.providers.base import ChatProvider, Message, ToolSpec

M = TypeVar("M", bound=BaseModel)


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

    assert isinstance(result, ChatResult)
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


async def coerce_structured(
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
        result = await chat(
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_structured.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/providers/structured.py tests/unit/test_structured.py
git commit -m "feat(providers): structured-output coercion with repair loop + json fallback"
```

---

### Task 3: OpenAICompatProvider (chat + embed + structured)

**Files:**
- Create: `src/paw/providers/openai_compat.py`
- Test: `tests/unit/test_openai_compat.py`

**Interfaces:**
- Consumes: `Message`, `ToolSpec`, `ToolCall`, `ChatResult` (Task 1); `coerce_structured` (Task 2).
- Produces (consumed by factory Task 5 + plans 2C/2D):
  - `class OpenAICompatProvider` with constructor
    `__init__(self, *, base_url: str, api_key: str, chat_model: str, embedding_model: str, supports_tools: bool = True, client: Any | None = None)`.
    `client` is injectable for tests; when `None`, builds `openai.AsyncOpenAI(base_url=base_url, api_key=api_key)`.
  - `async def chat(self, messages, *, tools=None, model=None, json_mode=False) -> ChatResult`
  - `async def embed(self, texts, *, model=None) -> list[list[float]]`
  - `async def structured(self, messages: list[Message], schema: type[M], *, model: str | None = None, retries: int = 2) -> M` — delegates to `coerce_structured` with `use_tools=self.supports_tools`.

**Note on testing:** the real network path is not exercised in unit tests. Inject a fake `client` exposing `client.chat.completions.create(...)` and `client.embeddings.create(...)` shaped like the openai SDK response objects. The provider's job under test is the **mapping** between SDK shapes and our dataclasses.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_openai_compat.py`:

```python
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
    usage = SimpleNamespace(model_dump=lambda: {"total_tokens": 10})
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_chat_maps_plain_content():
    client = _FakeClient(chat_response=_chat_response("hello"))
    p = OpenAICompatProvider(
        base_url="http://x", api_key="k", chat_model="m", embedding_model="e", client=client
    )
    res = await p.chat([Message(role="user", content="hi")])
    assert res.content == "hello"
    assert res.usage == {"total_tokens": 10}


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_openai_compat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.providers.openai_compat'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/providers/openai_compat.py`:

```python
from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec
from paw.providers.structured import coerce_structured

M = TypeVar("M", bound=BaseModel)


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

    async def structured(
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_openai_compat.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/providers/openai_compat.py tests/unit/test_openai_compat.py
git commit -m "feat(providers): OpenAICompatProvider chat/embed/structured with injectable client"
```

---

### Task 4: Provider + Wiki config models

**Files:**
- Create: `src/paw/providers/config.py`
- Test: `tests/unit/test_provider_config.py`

**Interfaces:**
- Produces (consumed by Task 5 + plans 2B/2C/2D):
  - `class ProviderConfig(BaseModel)`: `base_url: str`, `api_key_enc: str` (Fernet token), `chat_model: str`, `embedding_model: str`, `vision_model: str | None = None`, `embedding_dim: int`.
  - `class WikiConfig(BaseModel)` with defaults: `gen_language="en"`, `reasoning_language="en"`, `chunk_target_size=800`, `chunk_overlap_sentences=1`, `hub_threshold=2`, `max_steps=12`, `token_budget=100_000`, `max_writes=20`, `max_tool_calls=40`, `link_types=["related","prerequisite","part_of","see_also"]`, `request_timeout_s=60`, `max_retries=3`.
  - `PROVIDER_KEY = "provider"`, `WIKI_KEY = "wiki"` — the keys these models occupy inside `app_settings.settings`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_config.py`:

```python
from paw.providers.config import PROVIDER_KEY, WIKI_KEY, ProviderConfig, WikiConfig


def test_wiki_defaults():
    w = WikiConfig()
    assert w.hub_threshold == 2
    assert w.chunk_target_size == 800
    assert "related" in w.link_types
    assert w.max_steps == 12


def test_provider_config_roundtrip():
    pc = ProviderConfig(
        base_url="https://api.example/v1",
        api_key_enc="gAAAA-token",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=1536,
    )
    dumped = pc.model_dump()
    assert ProviderConfig.model_validate(dumped) == pc
    assert pc.vision_model is None


def test_keys_are_stable():
    assert PROVIDER_KEY == "provider"
    assert WIKI_KEY == "wiki"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.providers.config'`

- [ ] **Step 3: Write the implementation**

Create `src/paw/providers/config.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field

PROVIDER_KEY = "provider"
WIKI_KEY = "wiki"


class ProviderConfig(BaseModel):
    base_url: str
    api_key_enc: str  # Fernet token (SecretBox.encrypt output)
    chat_model: str
    embedding_model: str
    vision_model: str | None = None
    embedding_dim: int


class WikiConfig(BaseModel):
    gen_language: str = "en"
    reasoning_language: str = "en"
    chunk_target_size: int = 800
    chunk_overlap_sentences: int = 1
    hub_threshold: int = 2
    max_steps: int = 12
    token_budget: int = 100_000
    max_writes: int = 20
    max_tool_calls: int = 40
    link_types: list[str] = Field(
        default_factory=lambda: ["related", "prerequisite", "part_of", "see_also"]
    )
    request_timeout_s: int = 60
    max_retries: int = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_provider_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paw/providers/config.py tests/unit/test_provider_config.py
git commit -m "feat(providers): ProviderConfig + WikiConfig app_settings models"
```

---

### Task 5: ProviderSettingsService + provider factory

**Files:**
- Create: `src/paw/providers/factory.py`
- Create: `src/paw/services/provider_settings.py`
- Test: `tests/integration/test_provider_settings.py`

**Interfaces:**
- Consumes: `ProviderConfig`, `WikiConfig`, `PROVIDER_KEY`, `WIKI_KEY` (Task 4); `OpenAICompatProvider` (Task 3); `SettingsRepo` (`paw.db.repos.settings`), `SecretBox` (`paw.security.secrets`), `get_settings` (`paw.config`).
- Produces (consumed by plans 2C/2D):
  - `class ProviderSettingsService(session)`:
    - `async def set_provider(self, *, base_url, chat_model, embedding_model, embedding_dim, api_key: str, vision_model: str | None = None) -> ProviderConfig` — encrypts `api_key` via `SecretBox`, merges under `PROVIDER_KEY` into `app_settings.settings`, **commits**.
    - `async def get_provider(self) -> ProviderConfig | None`
    - `async def get_wiki(self) -> WikiConfig` — returns stored config merged over defaults (missing → defaults).
    - `async def set_wiki(self, cfg: WikiConfig) -> WikiConfig` — **commits**.
  - `def build_chat_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider` — decrypts `pc.api_key_enc` at call-site.
  - `def build_embedding_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider` (same instance type; embeddings use `pc.embedding_model`).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_provider_settings.py`:

```python
from cryptography.fernet import Fernet

from paw.providers.config import WikiConfig
from paw.providers.factory import build_chat_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def test_set_and_get_provider_encrypts_key(db_session):
    svc = ProviderSettingsService(db_session)
    pc = await svc.set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=1536,
        api_key="sk-secret-123",
    )
    # stored key is encrypted, not plaintext
    assert pc.api_key_enc != "sk-secret-123"
    got = await svc.get_provider()
    assert got is not None
    assert got.chat_model == "gpt-x"
    assert got.embedding_dim == 1536


async def test_get_wiki_returns_defaults_when_unset(db_session):
    svc = ProviderSettingsService(db_session)
    wiki = await svc.get_wiki()
    assert wiki == WikiConfig()


async def test_build_chat_provider_decrypts_key(db_session):
    key = Fernet.generate_key().decode()
    box = SecretBox(key)
    svc = ProviderSettingsService(db_session, box=box)
    pc = await svc.set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=8,
        api_key="sk-secret-123",
    )
    provider = build_chat_provider(pc, box)
    assert provider.chat_model == "gpt-x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_provider_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paw.services.provider_settings'`

- [ ] **Step 3: Write the factory**

Create `src/paw/providers/factory.py`:

```python
from __future__ import annotations

from paw.providers.config import ProviderConfig
from paw.providers.openai_compat import OpenAICompatProvider
from paw.security.secrets import SecretBox


def build_chat_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url=pc.base_url,
        api_key=box.decrypt(pc.api_key_enc),
        chat_model=pc.chat_model,
        embedding_model=pc.embedding_model,
    )


def build_embedding_provider(pc: ProviderConfig, box: SecretBox) -> OpenAICompatProvider:
    # Same provider type; embed() defaults to pc.embedding_model.
    return build_chat_provider(pc, box)
```

- [ ] **Step 4: Write the service**

Create `src/paw/services/provider_settings.py`:

```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.repos.settings import SettingsRepo
from paw.providers.config import PROVIDER_KEY, WIKI_KEY, ProviderConfig, WikiConfig
from paw.security.secrets import SecretBox


class ProviderSettingsService:
    def __init__(self, session: AsyncSession, *, box: SecretBox | None = None) -> None:
        self._s = session
        self._repo = SettingsRepo(session)
        self._box = box or SecretBox(get_settings().fernet_key)

    async def _all(self) -> dict[str, object]:
        row = await self._repo.get()
        return dict(row.settings) if row else {}

    async def get_provider(self) -> ProviderConfig | None:
        raw = (await self._all()).get(PROVIDER_KEY)
        return ProviderConfig.model_validate(raw) if raw else None

    async def set_provider(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        api_key: str,
        vision_model: str | None = None,
    ) -> ProviderConfig:
        pc = ProviderConfig(
            base_url=base_url,
            api_key_enc=self._box.encrypt(api_key),
            chat_model=chat_model,
            embedding_model=embedding_model,
            vision_model=vision_model,
            embedding_dim=embedding_dim,
        )
        settings = await self._all()
        settings[PROVIDER_KEY] = pc.model_dump()
        await self._repo.upsert(settings)
        await self._s.commit()
        return pc

    async def get_wiki(self) -> WikiConfig:
        raw = (await self._all()).get(WIKI_KEY)
        return WikiConfig.model_validate(raw) if raw else WikiConfig()

    async def set_wiki(self, cfg: WikiConfig) -> WikiConfig:
        settings = await self._all()
        settings[WIKI_KEY] = cfg.model_dump()
        await self._repo.upsert(settings)
        await self._s.commit()
        return cfg
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_provider_settings.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/paw/providers/factory.py src/paw/services/provider_settings.py tests/integration/test_provider_settings.py
git commit -m "feat(providers): provider settings service (encrypted key) + factory"
```

---

### Task 6: Reusable stub-LLM test double

**Files:**
- Create: `tests/stubs.py`
- Test: `tests/unit/test_stubs.py`

**Interfaces:**
- Consumes: `Message`, `ToolSpec`, `ToolCall`, `ChatResult` (Task 1).
- Produces (consumed by plans 2C/2D test suites):
  - `class StubChatProvider`: constructed with a list of `ChatResult` (a script) **or** a `responder` callable `(messages, tools) -> ChatResult`. Implements `ChatProvider`. Records `.calls`.
  - `StubChatProvider.tool(name, args)` / `.text(content)` classmethods to build scripted `ChatResult`s ergonomically.
  - `class StubEmbeddingProvider(dim: int)`: deterministic embeddings — each vector derived from `sha256(text)` folded into `dim` floats in `[0,1)`. Same text → same vector (needed for stable semantic-chunk/co-occurrence tests). Implements `EmbeddingProvider`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_stubs.py`:

```python
from paw.providers.base import Message

from tests.stubs import StubChatProvider, StubEmbeddingProvider


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_stubs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.stubs'`

- [ ] **Step 3: Write the implementation**

Create `tests/stubs.py`:

```python
from __future__ import annotations

import hashlib
from collections.abc import Callable

from paw.providers.base import ChatResult, Message, ToolCall, ToolSpec


class StubChatProvider:
    def __init__(
        self,
        script: list[ChatResult] | None = None,
        *,
        responder: Callable[[list[Message], list[ToolSpec] | None], ChatResult] | None = None,
    ) -> None:
        self._script = list(script or [])
        self._responder = responder
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


class StubEmbeddingProvider:
    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
            for b in digest:
                out.append(b / 255.0)
                if len(out) == self.dim:
                    break
            counter += 1
        return out

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        return [self._vec(t) for t in texts]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_stubs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Final gate + commit**

Run the full quality gate:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Expected: all green (ruff clean, mypy no issues, pytest all pass).

```bash
git add tests/stubs.py tests/unit/test_stubs.py
git commit -m "test(providers): reusable stub-LLM chat + embedding doubles"
```

---

## Self-Review

**Spec coverage (against §In scope · Providers and §Config):**
- `ChatProvider`/`EmbeddingProvider`/`VisionProvider` Protocols + `Message`/`ToolSpec`/`ChatResult` → Task 1. ✅
- `OpenAICompatProvider` implementing chat + embeddings → Task 3. ✅
- `structured(messages, schema, model, retries)` with **repair loop** → Tasks 2+3. ✅
- **JSON-mode fallback** when model lacks tool-calling → Task 2 (`use_tools=False`) + Task 3 (`supports_tools`). ✅
- Connection (`base_url` + decrypted `api_key`) + model names read from `app_settings` → Tasks 4+5. ✅
- Embedding **dim** captured in config → Task 4/5 (`ProviderConfig.embedding_dim`). Setup-wizard UI/endpoint wiring is Plan 2D; the storage + service are here. ✅
- Wiki-defaults (`gen_language`/`reasoning_language`, chunk params, `hub_threshold`, agent limits, `link_types`, timeouts/retries) → `WikiConfig` Task 4. ✅
- Stub-LLM (used by unit/integration tests across Phase 2) → Task 6. ✅

**Out of scope here (later plans):** the managed `vector(dim)` migration DDL (Plan 2B consumes `ProviderConfig.embedding_dim`); harness/loop/tools (2C); setup-wizard endpoint + `/settings` Connection form (2D).

**Placeholder scan:** none — every step has complete code or an exact command.

**Type consistency:** `coerce_structured(use_tools=...)` ↔ `OpenAICompatProvider.structured` delegating with `use_tools=self.supports_tools`; `ProviderConfig.embedding_dim` name reused verbatim by Plan 2B; stub `ChatResult`/`ToolCall` shapes match Task 1 dataclasses.
