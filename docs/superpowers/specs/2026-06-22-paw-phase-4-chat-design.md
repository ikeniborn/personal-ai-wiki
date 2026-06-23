---
title: "Phase 4 — Chat + history"
phase: 4
status: design
date: 2026-06-22
depends_on: [3]
chain:
  intent: null
review:
  spec_hash: b94520f3b5efd1a5
  last_run: 2026-06-23
  phases:
    structure:   { status: passed }
    coverage:    { status: passed }
    clarity:     { status: passed }
    consistency: { status: passed }
  findings:
    - id: F-001
      phase: coverage
      severity: WARNING
      section: "In scope / Acceptance criteria / Tests"
      section_hash: a026f265ab249639
      text: >-
        The "Web UI" in-scope item (dedicated Chat messenger screen, secondary
        sidebar session history, turn stream, source chips per turn) has no
        corresponding acceptance criterion and no test. SSE streaming is covered
        by AC #4 and the API tests, but the rendered UI (chips, sidebar history
        list) is not verifiable by any stated criterion.
      verdict: open
      verdict_at: null
    - id: F-002
      phase: clarity
      severity: WARNING
      section: "In scope / Security / API"
      section_hash: a026f265ab249639
      text: >-
        The same entity is named inconsistently as "session" and "thread":
        `chat_sessions` table and "their own sessions" vs "own threads"
        (GET /chat/sessions) and "Own-thread isolation" (Security). Pick one
        term for the user-facing aggregate.
      verdict: open
      verdict_at: null
    - id: F-003
      phase: clarity
      severity: INFO
      section: "Config (LLD §10) / In scope"
      section_hash: f89dcee8a1c3f872
      text: >-
        Config-key naming mismatch: global setting is `chat_history_depth`
        (Config §10) while the per-user chat_prefs key is `history_depth`
        (In scope). Confirm this is an intentional global-vs-override naming
        difference, otherwise align the names.
      verdict: open
      verdict_at: null
    - id: F-004
      phase: clarity
      severity: INFO
      section: "In scope / Config / Acceptance criteria"
      section_hash: a026f265ab249639
      text: >-
        Retention keys are written nested as `retention.max_sessions` /
        `retention.max_age_days` in the In-scope chat_prefs override list, but
        bare as `max_sessions` / `max_age_days` in Config §10 and in Acceptance
        criterion #5. Clarify whether the `retention.` prefix is part of the key
        path.
      verdict: open
      verdict_at: null
---

# Phase 4 — Chat + history

**Goal / vertical value:** hold a **multi-turn** conversation against a domain with
persisted history; each turn reuses the Phase 3 retrieval path and returns cited answers.
Sessions are listed, titled, deletable, and pruned by per-user retention.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## In scope

- **Chat op (LLD §4):** `harness/ops/chat.py` + chat prompt → `ChatTurn{answer_md, refs[]}`.
  Read-only tools (same allowlist as query); thread context = last `history_depth` turns
  folded into the prompt. Retrieval reuses Phase 3 (hybrid + BFS + assembly).
- **DB (LLD §2):** `chat_sessions` (user_id, domain_id, title, `last_active_at`,
  index `(user_id, last_active_at DESC)`), `chat_messages` (session_id, role, content,
  `meta` jsonb = refs/citations/model/prompt_version/token usage, index
  `(session_id, created_at)`).
- **Services:** `services/chat.py` — create session; append user/assistant messages;
  **auto-title** from the first turn; bump `last_active_at` (retention/GC ordering key).
- **API (LLD §8):** `POST /chat` (+ SSE stream of the assistant turn);
  `GET /chat/sessions` (cursor, own threads by `last_active_at`); `GET /chat/{session}`
  (messages); `DELETE /chat/{session}`.
- **Web UI:** dedicated **Chat** messenger screen (frame C, 💬): secondary sidebar =
  **session history** (own, by `last_active_at`, deletable) + turn stream + input;
  assistant turn streams via SSE; source chips per turn.
- **Retention (LLD §7/§10):** `users.chat_prefs` overrides (`retention.max_sessions`,
  `retention.max_age_days`, `history_depth`; null key → global default). Introduce
  `jobs/tasks.py:gc_housekeeping` covering **chat retention** (prune sessions beyond N /
  older than N days, per user); admin-triggered in v1 (cron → backlog). Cache-TTL cleanup
  is added to this same job in Phase 7.

## Out of scope (deferred)

Answer caching (chat threads are **never** cached — LLD §6) · suggestions (Phase 7) ·
cache-TTL GC (Phase 7) · scheduled GC cron (backlog).

## Key flows (LLD §12)

Chat (sync/stream): load last `history_depth` turns → retrieval (Phase 3) → LLM answer
(streamed) → persist user + assistant messages (with `meta`) → bump `last_active_at`.
First turn also sets the session title.

## Config (LLD §10)

Global `chat_history_depth`, chat retention (`max_sessions`, `max_age_days`); per-user
overrides in `users.chat_prefs`; per-domain `chat_model`/languages.

## Security

Own-thread isolation: a user may only list/read/delete their own sessions (enforced in
deps). Chat content is untrusted in prompts (delimiters). No write tools in chat context.

## Acceptance criteria (verifiable)

1. A multi-turn exchange keeps context across turns up to `history_depth`; turn N+1 can
   reference turn N.
2. First turn auto-generates a session title; `last_active_at` updates on each turn.
3. `GET /chat/sessions` returns only the caller's sessions, newest-active first, by cursor;
   another user's session id is 404/403 on read/delete.
4. Assistant turns stream via SSE and carry refs in `meta`.
5. `gc_housekeeping` prunes sessions exceeding a user's `max_sessions` and those older than
   `max_age_days`; defaults apply when `chat_prefs` keys are null.

## Tests (LLD §11)

- **Unit:** history-depth windowing; retention selection (per-user N/age, null→global).
- **Integration (testcontainers + stub-LLM):** GC removes the right sessions; cascade
  `session → messages` on delete.
- **API (httpx):** chat SSE, sessions cursor + own-only, delete, cross-user denial.
- **E2E:** multi-turn conversation with carried context and cited answers.

## Risks / notes

- Keep `gc_housekeeping` extensible — Phase 7 adds cache-TTL cleanup to the same task.
- `meta` is the audit surface for a turn (model, prompt_version, tokens) — populate it now
  so Phase 9 cost metrics can read it.
