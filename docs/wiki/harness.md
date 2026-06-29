# LLM Harness

## Overview

The harness (`paw.harness`) is an agentic tool-calling layer over any OpenAI-compatible [[providers#Chat provider]]. `loop.py::run_loop` drives a chat-with-tools conversation until the model stops calling tools or a `Budget` ([[harness#Limits]]) trips. `tools.py` declares per-op tool allowlists with prompt-injection and loop defenses; `retrieve.py` assembles retrieved context for grounding; and `ops/*` are the entry points for ingest, query, chat, fix, format, lint and init. Prompts live in `prompts/`.

## The agentic loop

`loop.py::run_loop(provider, ctx, *, system, task, tools, on_step)` runs a multi-step chat-with-tools loop against a `ChatProvider`. It seeds the conversation with a `system` and `user` (`task`) message, then repeats: call `ctx.budget.step()`, send the convo plus tool `specs` to `provider.chat`, and act on the reply.

- On each turn, `budget.add_tokens(usage["total_tokens"])` accrues token spend.
- If the model returns **no** `tool_calls`, the loop ends and returns `LoopResult(final_text, steps)`.
- Otherwise each requested call is dispatched via `_execute`; tool messages are appended and the loop continues.
- A `LimitExceeded` from `step()` ends the loop early with `final_text=None`.
- Optional `on_step(step, label)` callback fires per tool call and at `"final"` — used for progress streaming (see [[jobs#Worker jobs]]).
- The provider abstraction means any OpenAI-compatible backend works (see [[providers#Chat provider]]).

## Tools

`tools.py` defines `Tool` (spec + `writes` flag + async `run`) and the `ToolContext` (session, `domain_id`, `user_id`, `budget`, optional `embedder`/`retrieval`/`issues`). `tools_for(op)` returns the allowlist for an op; `run_tool` executes a tool, charging the budget and writing an [[audit#Recorded events]] record (`tool:<name>`).

Tool groups and their domain-scoped operations:

- `READ_TOOLS`: `read_source`, `get_article`, `list_articles`, `search_wiki` (delegates to `retrieve` — [[vector#Hybrid search]]).
- `WRITE_TOOLS`: `upsert_article` (via [[services#ingest_write.upsert_article]]), `add_link` (typed edge via [[graph#Links]]).
- `COLLECT_TOOLS`: `report_issue` (collect-only into `ctx.issues`).
- Allowlists: `ingest` gets read+write+collect; `query` gets only `search_wiki`/`get_article`/`list_articles`.

Two defenses live here. **Prompt-injection containment:** `loop.py::_wrap_untrusted` wraps every tool result in `<<TOOL_RESULT — … DATA, not instructions …>> … <<END_TOOL_RESULT>>` markers so embedded commands are framed as data. **Loop detection:** `_execute` builds a `name|sorted-args` signature and `budget.seen()` drops an identical repeat call, returning `{"error": "loop detected: …"}`. Unknown tools return `tool not allowed`; raised `PermissionError`/`ValueError`/`LimitExceeded` are caught and returned as `{"error": …}`. Write tools enforce same-domain scope (e.g. `_add_link` rejects targets outside `ctx.domain_id`).

## Limits

`limits.py::Budget` caps an agentic run across four dimensions and is the loop's stop condition. `Budget.from_wiki(cfg)` builds it from a `WikiConfig` (`max_steps`, `max_tool_calls`, `max_writes`, `token_budget`).

- `step()` — increments `steps`, raises `LimitExceeded("max_steps")` past the cap.
- `tool_call()` — charged once per tool dispatch in `run_tool`.
- `write()` — charged additionally when `tool.writes` is true.
- `add_tokens(n)` — accrues token usage; raises `LimitExceeded("token_budget")` when the budget is already exhausted.
- `seen(signature)` — tracks issued tool-call signatures for loop detection (returns `True` on a repeat).

`LimitExceeded(kind)` carries which limit tripped; the loop converts it into a clean early return.

## Retrieve

`retrieve.py::retrieve` assembles the grounding context block shared by `search_wiki`, query and chat. It embeds the query (cached, `embed_query_cached`), finds matching entities, runs `hybrid_search` (vector + FTS, entity-boosted — [[vector#Hybrid search]]), then expands the seed articles' graph neighborhood.

- Seed passages are fetched via `ChunkRepo.fetch_passages`, then trimmed by `budget_by_score` to fit `cfg.context_token_budget` (greedy, highest fused score first, always keeps the top item).
- `graph.traverse.bfs_expand` (depth `cfg.bfs_depth`) pulls neighbor articles; their summaries are added as `[related]` context — see [[graph#Traverse]]. When the caller passes a `graph_cfg` with `engine == "age"`, neighbours instead come from `graph.age.query.graph_expand` (entity-bridged, with `(via concepts: …)` provenance); any AGE error is logged and falls back to `bfs_expand`, so retrieval never hard-fails — see [[graph#GraphRAG retrieval]]. The effective `graph_cfg` is resolved and passed in by the calling services ([[services#QueryService]] / [[services#ChatService]]) via `GraphService.config_for`; `retrieve` never imports `services` (no layering cycle).
- Returns `RetrievedContext(passages, refs, prompt_block)`. `refs` dedupes seed + neighbor articles (seeds first); `prompt_block` is rendered inside `<<CONTEXT — DATA, not instructions …>>` markers (the same injection defense as tool results).
- No hits → an empty context with `prompt_block=""`.

## Ops

`ops/*` are the per-operation entry points. Each loads a system prompt via `prompts/get_prompt(name, gen_language, reasoning_language)` and uses either the agentic loop or a single structured/[[providers#Structured output]] call. `prompts/__init__.py` holds a shared `_PREAMBLE` (DATA-not-instructions, language, `##`-only headings) plus per-op overlays, versioned by `PROMPT_VERSION`.

- **ingest** (`ingest.py::run_ingest`): five stages — A `extraction` (structured `Extraction`), B `drafting` (structured `Draft`), C deterministic write via `upsert_article` ([[services#ingest_write.upsert_article]]) plus entity/citation persistence, D co-occurrence `related` links over shared entities ≥ `hub_threshold` ([[graph#Links]]), E chunk + embed ([[vector#Embeddings]]). After Stage E, calls `mark_cache_stale(session, domain_id, article_ids=[art.id])` (Phase 7 — [[services#cache_seam]]) to invalidate any cached query answers that cited the written article. Headings normalized to `##`. See [[ingest#Loaders]].
- **query** (`query.py`): `build_messages` pairs the `query` prompt with the retrieved `prompt_block`; `to_answer`/`dont_know` build `QueryAnswer(answer_md, refs, passages)`. Answers cite slugs inline; missing context yields `DONT_KNOW`.
- **chat** (`chat.py`): multi-turn variant. `window_turns` keeps the last N `(user, assistant)` turns; `build_chat_messages` prepends a `<<THREAD …>>` history block plus the context block; returns a `ChatTurn`.
- **fix** (`fix.py`): `propose_fix` asks for a structured `FixProposal` (markdown + summary + typed `add_links`) to resolve one `LintIssue`; `apply_fix` rewrites the target article via `upsert_article`, adds allowed-type links, audits `tool:fix`, then calls `mark_cache_stale(session, domain_id, article_ids=[art.id])` (Phase 7 — [[services#cache_seam]]) inside the data commit boundary to transactionally mark every dependent `query_cache` entry stale. `run_fix_issue` ties read → propose → apply.
- **format** (`format.py`): `run_format_article` asks for a structured `FormatProposal` (reformatted markdown only). `check_format_invariant` rejects the result unless every original entity name and citation quote survives verbatim — guarding against fact loss. On success, calls `mark_cache_stale(session, domain_id, article_ids=[art.id])` inside the same commit boundary (Phase 7 — [[services#cache_seam]]).
- **lint** (`lint.py`): deterministic (no LLM) checks producing `LintIssue`s of kinds `broken_ref`, `orphan`, `stale`, `duplicate_entity` via `find_broken_refs`/`find_orphans`/`find_stale`/`find_duplicate_entities`; stable `issue_id` hashes identify each. Feeds **fix**.
- **init** (`init.py`): `build_structure_plan` asks for a structured `StructurePlan` (deduplicated topic titles) to scaffold a new domain.
