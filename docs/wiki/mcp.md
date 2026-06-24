# MCP Server

## Overview

Phase 8 adds an MCP (Model Context Protocol) server at `/mcp`. `build_mcp()` registers three read-only tools (`search_wiki`, `get_article`, `list_links`) on a `FastMCP` instance. All requests are gated by `MCPAuthMiddleware` which validates a Bearer api-key and checks the `read` scope. The server is mounted as a stateless, streamable-HTTP ASGI app inside `create_app()`. See [[security#API keys]] and [[api#Api-keys router]].

## Tools

The three MCP tools are pure async functions in `mcp/tools.py`, each accepting an `AsyncSession`, a `domain_id`, and tool-specific args. They raise `ValueError` on bad input and return Pydantic model instances. No `paw.api` import — the tool layer is independent of HTTP.

- `search_wiki(session, *, domain_id, query, embedder, cfg, embedding_version, top_k)` — delegates to [[harness#Retrieve]] (`retrieve()`); returns `SearchResult` with `passages: list[PassageOut]` (`chunk_id/slug/heading_path/text/score`) and `refs: list[RefOut]` (`article_id/slug/title`). `top_k` overrides `cfg.top_n` when set.
- `get_article(session, *, domain_id, ref)` — resolves `ref` as UUID or slug via `_resolve_article`, reads the blob from `PostgresStorage`, returns `ArticleResult` (`id/slug/title/summary/current_rev/updated_at/markdown`). See [[db#Models and tables]].
- `list_links(session, *, domain_id, ref)` — resolves the article, then calls `LinkRepo.outgoing` and `LinkRepo.backlinks`; returns `LinksResult` (`article_id`, `outgoing/backlinks: list[LinkOut]` with `type/article_id/slug/title`). See [[graph#Subgraph]].

All three use `_resolve_article` which checks both UUID and slug lookups and verifies `art.domain_id == domain_id`; missing or cross-domain articles raise `ValueError("article not found in domain")`.

## Server assembly

`mcp/server.py::build_mcp()` constructs a `FastMCP("paw", stateless_http=True, json_response=True)` instance and sets `mcp.settings.streamable_http_path = "/"` so that when the app is mounted at `/mcp` the MCP endpoint is reachable at exactly `/mcp`.

Each registered tool opens its own session via `get_sessionmaker()()`, constructs its per-tool dependencies, then delegates to the pure tool function. The pattern is the same for all three:

- `search_wiki` tool: calls `ProviderSettingsService` to get provider config and retrieval config, resolves the domain by name, applies per-domain retrieval overrides via `_retrieval_for` (merges `dom.config["retrieval"]` dict over the global `RetrievalConfig`), builds the embedder with `build_embedding_provider` + `SecretBox`, then calls `mcp_tools.search_wiki`.
- `get_article` tool: resolves domain by name, calls `mcp_tools.get_article`.
- `list_links` tool: resolves domain by name, calls `mcp_tools.list_links`.

`_retrieval_for` merges per-domain config overrides only when `dom.config` is a dict containing a `"retrieval"` key with a dict value; otherwise falls back to the global config unchanged.

## Auth & mount

`MCPAuthMiddleware` (`mcp/auth.py`) is a pure-ASGI middleware (not `BaseHTTPMiddleware`) to avoid buffering the Streamable-HTTP/SSE response bodies. It guards any path under `/mcp`:

- Reads the `Authorization` header from the raw ASGI scope headers.
- Opens a session and calls `ApiKeyService.authenticate(authorization)` — returns `None` on any parse failure or wrong secret.
- Returns `401 Unauthorized` (problem+json) if authentication fails.
- Returns `403 Forbidden` (problem+json) if the key exists but does not carry `MCP_REQUIRED_SCOPE` (`"read"`).
- Falls through to the downstream app on success.

`main.py::create_app()` orchestrates the mount in a specific order required by FastMCP: `mcp.streamable_http_app()` must be called before `mcp.session_manager` is accessed. The lifespan context manager wraps `mcp.session_manager.run()`. `MCPAuthMiddleware` is added last via `app.add_middleware()` so it wraps the entire application including the `/mcp` mount. See [[api#App wiring]] and [[security#API keys]].
