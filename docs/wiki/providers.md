# Providers

## Overview
The providers layer is the LLM/embedding boundary. `base.py` defines `ChatProvider`, `EmbeddingProvider` and `VisionProvider` as structural Protocols; `openai_compat.py` is the single concrete impl (chat, embed and image `describe`) against any OpenAI-compatible endpoint; `factory.py` builds it from a stored `ProviderConfig`; `structured.py` coerces JSON/tool output into Pydantic models; `config.py` holds the typed per-section DB-config schema. API keys are Fernet-encrypted at rest. See [[harness#The agentic loop]], [[security#Secrets]].

## Chat provider
`ChatProvider` (`base.py`) is a `typing.Protocol` with `chat(messages, *, tools=None, model=None, json_mode=False) -> ChatResult` and `stream(messages, *, model=None) -> AsyncIterator[str]`. Messages, tool specs and results are plain dataclasses (`Message`, `ToolSpec`, `ToolCall`, `ChatResult`).

- `chat` returns content plus parsed `tool_calls`, `finish_reason` and token `usage`.
- `stream` yields content deltas for the chat UI ([[harness#The agentic loop]]).
- Being a Protocol, any object with these methods qualifies — tests can supply fakes.

## Embedding provider
`EmbeddingProvider` (`base.py`) is a one-method Protocol: `embed(texts, *, model=None) -> list[list[float]]`. It is what chunking and reindex call to vectorize text. See [[vector#Embeddings]].

## Vision provider
`VisionProvider` (`base.py`) is a one-method Protocol: `describe(image, *, prompt, model=None) -> str`, used to OCR/caption `image` sources during ingest ([[ingest#Loaders]]). `OpenAICompatProvider.describe` implements it: it base64-encodes the bytes, sniffs the MIME type with `_image_mime` (JPEG/WEBP signatures, else `image/png`), and sends a single multimodal user turn (a `text` part plus an `image_url` `data:` URI) to `model or self.vision_model or self.chat_model`, returning the reply content.

- The model lives on the provider as `self.vision_model`; the worker wraps each call in a per-model `model_lock` ([[jobs#Locks]]) and prompts in the domain's `reasoning_language`.

## Factory
`factory.py` builds providers from a stored `ProviderConfig` plus a `SecretBox`. `build_chat_provider(pc, box)` instantiates `OpenAICompatProvider` with `pc.base_url`, the **decrypted** `pc.api_key_enc`, `pc.chat_model` and `pc.embedding_model`. `build_embedding_provider` is the same object — `embed()` simply defaults to `pc.embedding_model`. `build_vision_provider(pc, box)` returns an `OpenAICompatProvider` carrying `pc.vision_model`, or **`None` when `vision_model` is unset** — callers treat `None` as "vision not configured" and fail the image source. See [[security#Secrets]].

## Structured output
`structured.py::coerce_structured` forces a model to return schema-valid JSON for a Pydantic `model_cls`. It builds an `emit_result` tool from `model.model_json_schema()` (`schema_tool`) and loops up to `retries + 1` times, re-validating each reply and feeding the validation error back as a corrective user turn.

- With `use_tools=True` it sends the schema as a tool and reads `tool_calls`; otherwise it uses `json_mode` and parses `content`.
- `OpenAICompatProvider.structured(...)` is the entry point; it picks the path via `self.supports_tools`.
- After exhausting retries it raises `StructuredError`. Used by harness extraction ([[harness#The agentic loop]]).

## Config models
`config.py` defines the typed Pydantic models that make up `app_settings.config` — one model per section keyed by the `*_KEY` constants (`provider`, `wiki`, `retrieval`, `chat`, `graph`, `maintenance`, `embedding`, `query_cache`). This is the per-section schema for DB-stored settings; see [[architecture#Config layering (env ⊕ DB)]].

- `ProviderConfig` — `base_url`, `api_key_enc`, `chat_model`, `embedding_model`, `vision_model`, `embedding_dim`.
- `WikiConfig` — generation/harness budgets and chunking knobs (`chunk_target_size`, `chunk_overlap_sentences`, `max_steps`, `token_budget`, `link_types`…), consumed by [[ingest#Chunking]].
- `RetrievalConfig` — hybrid-search knobs (`k1`, `k2`, `top_n`, `rrf_k`, weights, `bfs_depth`, `fts_regconfig`).
- `ChatConfig` — chat history depth and session retention limits.
- `GraphConfig` — graph-view `default_depth`/`max_depth` and link types, plus the Phase-10 graph engine knobs: `engine` (`cte` default | `age`), `expand_depth` (AGE `LINKS` hops), `max_entities`, `max_neighbors` (entity-bridge/neighbour caps). Resolved global ⊕ per-domain, so AGE can be enabled on one domain while others stay on the CTE path. See [[graph#AGE graph engine]].
- `MaintenanceConfig` — `enabled_ops`, `reindex_batch_size`, `stale_days`.
- `EmbeddingConfig` — the `version` int that search filters on, bumped on a model/dim change.
- `QueryCacheConfig` (Phase 7, `query_cache` key) — `enabled` (bool, default `True`) gates the cache globally; `sim_threshold` (float, default `0.92`) is the cosine-similarity floor for an ANN hit to count as a cache hit; `ttl_seconds` (int, default 30 days) controls how long idle entries survive GC; `suggest_top_k` (int, default `5`) caps as-you-type suggestions. Resolved global ⊕ per-domain from `app_settings.config`.

## Secrets
`ProviderConfig.api_key_enc` is never the raw key — it stores a **Fernet token** (the output of `SecretBox.encrypt`). The factory calls `box.decrypt(pc.api_key_enc)` only at provider-build time to hand the live key to the OpenAI client; the plaintext is never persisted. See [[security#Secrets]].
