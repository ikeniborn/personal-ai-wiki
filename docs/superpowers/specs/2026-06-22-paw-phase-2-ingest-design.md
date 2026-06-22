---
title: "Phase 2 — Ingest (LLM generation)"
phase: 2
status: design
date: 2026-06-22
depends_on: [1]
review:
  spec_hash: 5a6ed65bca4c1452
  last_run: 2026-06-22
  phases:
    structure:    { status: passed }
    coverage:     { status: passed }
    clarity:      { status: passed }
    consistency:  { status: passed }
  findings: []
chain:
  intent: null
---

# Phase 2 — Ingest (LLM generation)

**Goal / vertical value:** upload a source (md/pdf/docx/html) or pick a topic from an Init
plan → run an **ingest job** → an LLM harness generates a wiki article with entities,
links, and citations, then the article is chunked and embedded. Progress streams live.
First real LLM path; first background job; first vectors.

See `…paw-00-overview-design.md`. References point into LLD (`§N`).

## In scope

- **Providers (LLD §3):** `providers/base.py` — `ChatProvider`, `EmbeddingProvider`,
  `VisionProvider` Protocols + `Message`/`ToolSpec`/`ChatResult`. `providers/openai_compat.py`
  — `OpenAICompatProvider` implements all three; `structured(messages, schema, model,
  retries)` → validated pydantic with **repair loop**; **JSON-mode fallback** when the
  model lacks native tool-calling. Connection (`base_url` + decrypted `api_key`) and model
  names read from `app_settings`.
- **Harness (LLD §4):** `harness/loop.py` (system+task+tools → chat→tool_calls→results→
  repeat until final / `max_steps`, progress per step); `harness/tools.py`
  (read: `read_source`, `get_article`, `list_articles`; write: `upsert_article`,
  `add_link`; `report_issue` is collect-only and unused until Phase 6);
  `harness/ops/{ingest,init}.py`; `harness/prompts/` (versioned: shared preamble +
  extraction + drafting + summary). **Guards:** tool-allowlist per op (ingest = read +
  write), write-scope `target.domain_id == ctx.domain_id`, schema-validated output before
  write, sources/tool-results wrapped "data, not instructions", `max_steps` /
  `max_tool_calls` / `max_writes` / token-budget / per-step timeout / loop-detection;
  every tool call → `audit_log`. `upsert_article` idempotent by `slug` (merge).
- **Loaders (LLD §5):** `ingest/loaders/{md,pdf,docx,html}.py` — md/txt passthrough
  (strip frontmatter), pdf `pymupdf`, docx `mammoth`, html `trafilatura`→`markdownify`.
  (epub/url/image-OCR → Phase 9; `VisionProvider` Protocol exists but no image loader.)
- **Ingest pipeline (LLD §5, one job = one article):** A) topic extraction (structured,
  windowed map-reduce, merge/dedup) → B) article drafting (structured, create|merge,
  headings ≤ `##`) → C) deterministic write (`articles` + `article_revisions` origin=`ai`,
  `entities`, `article_entities`, `citations`) → D) links (typed `link_suggestions` +
  co-occurrence over shared `article_entities` ≥ threshold → `related`) → E) chunking +
  embedding.
- **Chunking (LLD §5):** `summary` chunk (`ord=0`, kind=`summary`, also copied to
  `articles.summary`); split by `##` → `[intro, sec…]`; per-section semantic split
  (sentence-embedding breakpoint, bounded `target_size`); sentence **overlap** (1–2
  sentences) on each non-first chunk; `heading_path`; `chunk_entities` tagging.
- **Vector:** `vector/embed.py` — batch embed chunks via `EmbeddingProvider`, write
  `chunks(kind, ord, heading_path, text, tsv, embedding, embedding_version)`.
- **Graph writes:** `graph/repo.py` — entity/link upserts used by ingest (traversal →
  Phase 3).
- **Jobs / worker (LLD §7):** `jobs/{tasks,progress}.py` — `ingest_domain` task; job
  lifecycle (API creates `jobs(queued)` + enqueue with `job_id`; worker running →
  terminal); **progress** via Redis pub/sub `job:{id}` + replay from `jobs.log`;
  cooperative `cancel_requested`; heartbeat + startup reconciler (stuck→failed);
  **job-lock per domain** (Redis, one writing job per domain); **model-lock**
  `lock:model:{name}` (serialize one model, parallel across models); arq queues
  (LLM vs light), retries/backoff, poison→dead-letter.
- **DB additions (LLD §2):** `entities`, `article_entities`, `links`, `citations`,
  `chunks` (+ HNSW `vector_cosine_ops`, GIN on `tsv`, index on `embedding_version`),
  `chunk_entities`, `jobs`. **Managed embedding-dim migration:** setup wizard captures
  connection + chat/embedding/vision models + **dim**; a managed migration then creates
  the `vector(dim)` column + HNSW. Changing dim later = ALTER + HNSW rebuild + reindex
  (reindex job → Phase 6).
- **API (LLD §8):** `POST /domains/{id}/ingest` → `job_id`; `POST /domains/{id}/init`
  (sync: create domain plan → list of topics, each topic enqueues an ingest job);
  `POST /domains/{id}/sources` extended to pdf/docx/html (streamed to storage);
  `GET /jobs/{id}` · `/events` (SSE) · `POST /jobs/{id}/cancel`.
- **Web UI:** domain-page **Ingest** action → job **drawer** (progress bar + live log via
  SSE + cancel); generated article visible on `/articles/{id}`; `/settings` **Connection**
  + models + **dim** functional (dim change shows the ALTER+reindex warning).

## Out of scope (deferred)

Retrieval/query (Phase 3) · chat (Phase 4) · graph viz + article-tree by links (Phase 5) ·
lint/fix/format/**reindex** jobs (Phase 6) · query-cache (Phase 7) · MCP (Phase 8) ·
epub/url/image-OCR loaders, bulk zip upload, observability (Phase 9). `search_wiki` tool
is **not** required for ingest and is introduced in Phase 3.

## Key flows (LLD §12)

- **Init (sync):** create domain + index/prefixes → LLM builds a structure plan (topic
  list) → each topic becomes an ingest job.
- **Ingest (async, worker):** start job → domain lock + claim → input A (upload→extract)
  or B (topic from plan) → draft (structured) → deterministic write → links → chunking →
  embedding. Progress worker → Redis pub/sub → api (SSE). Each write under write-scope +
  audit.

## Config (LLD §10)

Connection + models + dim in `app_settings` (api_key encrypted, Fernet). Global
wiki-defaults used now: `gen_language`/`reasoning_language`, chunk params, co-occurrence
`hub_threshold`, agent limits (`max_steps`, token_budget, `max_writes`, `max_tool_calls`),
`link_types` allowlist, timeouts/retries. Per-domain overrides via `domains.config`.

## Security (LLD §11)

Prompt-injection defenses (untrusted delimiters on sources + tool results, write-scope,
`max_steps`, schema validation before write); provider key decrypted only at call site,
never in agent context; per-call audit. Upload guard extended to pdf/docx/html
(magic-byte + size); zip/anti-zip-bomb stays in Phase 9.

## Acceptance criteria (verifiable)

1. Setup wizard saves connection + models + dim; managed migration creates the
   `vector(dim)` column + HNSW; api/worker pick up the encrypted key (never logged).
2. Upload a fixture pdf/docx/html/md → run ingest → exactly one article written with
   `article_revisions` origin=`ai`, ≥1 entity, ≥1 citation, chunks incl. an `ord=0`
   summary chunk, and embeddings present; `articles.summary` populated.
3. Co-occurrence linker creates `related` links above threshold; LLM `link_suggestions`
   create typed links within the domain only (cross-domain rejected).
4. Job progress streams via SSE and replays from `jobs.log` on reconnect; cancel mid-run
   leaves no partial article (cleanup).
5. Domain job-lock blocks a second writing job on the same domain; model-lock serializes
   same-model LLM calls.
6. Structured-output repair recovers from one malformed LLM response (stub-LLM); a model
   without tool-calling uses JSON-mode fallback.
7. Empty/garbage source → job fails cleanly with an error in `jobs.error`, no article.

## Tests (LLD §11)

- **Unit:** chunking (sections/summary/overlap/semantic split), co-occurrence linker,
  provider parse + structured repair (stub-LLM), prompt overlay/versioning, loop-detection
  + limit guards.
- **Integration (testcontainers + stub-LLM):** full ingest writes expected rows; managed
  dim migration; domain job-lock; model-lock serialization; pub/sub progress + replay.
- **API (httpx):** ingest→job_id, jobs SSE stream, cancel, init→topics→jobs.
- **E2E:** fixture pdf → ingest → article + entities + citations + chunks + embeddings.

## Risks / notes

- Semantic chunking + summary-chunk cost extra embeddings on ingest (LLD §5 trade-off,
  accepted for boundary quality + article-level recall).
- Map-reduce windows on long sources keep within `max_input_tokens`; tool-result size caps
  apply to `read_source`.
- One job = one article (LLD §12); Init fans out per-topic jobs rather than one mega-job.
