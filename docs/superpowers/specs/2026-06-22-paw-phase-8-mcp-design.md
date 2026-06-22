---
title: "Phase 8 — MCP server"
phase: 8
status: design
date: 2026-06-22
depends_on: [3]
---

# Phase 8 — MCP server

**Goal / vertical value:** expose the wiki to external clients (IDEs/agents) over MCP,
**read-only**, authenticated by API key. Reuses the Phase 3 retrieval path and Phase 5
link reads — no new retrieval logic.

See `…paw-00-overview-design.md`. References point into LLD (`§8`).

## In scope

- **MCP server (LLD §8):** `mcp/server.py` mounted at `/mcp` (MCP Python SDK, **Streamable
  HTTP**), `mcp/tools.py`:
  - `search_wiki(query, domain, top_k?)` — hybrid + RRF + BFS + context assembly (Phase 3,
    **no LLM**), returns ranked passages with article refs.
  - `get_article(id|slug, domain)` — article content + metadata.
  - `list_links(article, domain)` — typed edges (Phase 5 graph read).
- **Auth (LLD §8/§11):** API-key `Bearer paw_<prefix>.<secret>` — lookup by `prefix`,
  verify `sha256`, honor `scopes`, reject `revoked_at`. CSRF exempt (api-key path). MCP is
  **read-only**: no write tools are registered.
- **API-key issuance (minimal):** `POST·GET·DELETE /api-keys` (issue with scopes / list /
  revoke) so a user can mint a key for MCP. Full management UI polish → Phase 9.

## Out of scope (deferred)

Any write/mutating MCP tool · rate limiting (backlog) · api-keys management **UI** polish
(Phase 9) · observability of MCP traffic (Phase 9). No new tables (`api_keys` exists from
Phase 1).

## Data model touched

Reads `api_keys` (auth), `chunks`/`links`/`articles` (tools). No schema changes.

## Key flows

MCP client → `/mcp` (Bearer api-key) → tool call → reuse retrieval/link reads → structured
result. `search_wiki` follows the Phase 3 path up to context assembly (no answer LLM).

## Config (LLD §10)

`top_k`/retrieval config shared with Phase 3; api-key scopes allowlist.

## Security (LLD §11)

API keys stored hashed (prefix + sha256), scoped, revocable; secret shown once at
issuance. Read-only surface (no write tools). Domain scoping enforced per call. Retrieved
content remains untrusted data for the calling client.

## Acceptance criteria (verifiable)

1. An MCP client authenticates with a valid `Bearer paw_<prefix>.<secret>` and lists the
   three read-only tools; no write tool is exposed.
2. `search_wiki` returns ranked passages with refs from a seeded corpus; `get_article`
   returns the article; `list_links` returns typed edges.
3. A revoked or unknown key is rejected (401); a key lacking the required scope is denied
   (403).
4. Requests are scoped to the specified domain; another domain's content is not returned.

## Tests (LLD §11)

- **Unit:** tool input/output schemas + serialization; api-key parse/verify.
- **Integration (testcontainers):** MCP tools against a seeded corpus; scope + domain
  enforcement.
- **API (httpx):** api-key issue/list/revoke; auth accept/reject.
- **E2E:** MCP client `search_wiki` → `get_article` round-trip.

## Risks / notes

- Keep `search_wiki` sharing the exact Phase 3 retrieval code (one implementation, two
  entry points: REST `/query` pre-LLM and MCP) to avoid drift.
- Streamable HTTP must be proxied by Traefik without buffering (same as SSE, LLD §11).
