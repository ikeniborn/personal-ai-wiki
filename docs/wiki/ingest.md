# Ingest

## Overview
Ingest turns raw uploaded bytes into embeddable text. Per-format loaders in `ingest/loaders/*` normalize md/pdf/docx/html/epub down to a single markdown string; `url` and `image` sources take async side paths (SSRF-guarded fetch, vision OCR). Then `chunking.py` splits that markdown by `##` sections, breaks each into sentences, and greedily groups them into overlapping chunks sized by `WikiConfig`. The chunks feed embedding for hybrid retrieval. See [[storage#Backends]], [[vector#Embeddings]], [[harness#Ops]].

## Loaders
Most loaders (`ingest/loaders/<fmt>.py`) expose a single sync `load(data: bytes) -> str` that returns markdown-ish text. `load_source(data, type)` dispatches by source type; ingest pulls the raw bytes from storage first (see [[storage#PostgresStorage]]). `url` and `image` are **not** handled by `load_source` — they have async entry points the worker calls directly (see [[jobs#Worker jobs]]).

- `md.py` — decodes UTF-8 (`errors="replace"`) and strips a leading YAML front-matter block via the `_FRONTMATTER` regex. No external lib.
- `pdf.py` — opens the byte stream with **PyMuPDF** (`fitz.open(stream=…, filetype="pdf")`) and joins `page.get_text()` across pages with blank lines.
- `docx.py` — converts with **mammoth** (`mammoth.convert_to_markdown`), returning `result.value` (markdown) or `""`.
- `html.py` — extracts main content with **trafilatura** (`output_format="markdown"`, links dropped); falls back to **markdownify** on the raw HTML when extraction yields nothing.
- `epub.py` — reads the EPUB with **ebooklib** (`epub.read_epub` via a temp file), walks the **spine** in reading order, and feeds each `ITEM_DOCUMENT` body through `html.py`'s `load`, joining the per-document markdown with blank lines.
- `url.py` — async `load_url(url, *, allowlist, max_bytes)`: fetches the page through the [[security#SSRF guard]] (`safe_get`), runs the bytes through `html.py`'s `load`, and raises `ValueError` if no text is extractable.
- `image.py` — async `describe_image(data, vision, *, prompt)`: thin wrapper that delegates to a [[providers#Vision provider]] (`VisionProvider.describe`) to OCR/caption the image into text.

## Chunking
`chunking.py::build_chunks` is async (it needs the embedder for semantic breakpoints). It always emits a leading `summary` chunk (`ord=0`), then walks each section producing `section` chunks. Output is a list of `ChunkSpec(kind, ord, heading_path, text)`. See [[vector#Embeddings]].

- `split_sections` splits on `^##\s+` headings (`_HEADING`); text before the first heading becomes an untitled intro section.
- `split_sentences` splits a section body on sentence punctuation (`_SENTENCE`: `(?<=[.!?])\s+`).
- `_greedy_chunks` accumulates sentences until `size + len(sent) > target_size` **or** the cosine similarity to the previous sentence drops below `0.2` (a semantic breakpoint), then starts a new chunk.
- Sizing comes from [[providers#Config models]]: `cfg.chunk_target_size` (default 800 chars) and `cfg.chunk_overlap_sentences` (default 1) — on a boundary, the last `overlap` sentences carry into the next chunk.
- Per-section sentence embeddings are fetched via `await embedder.embed(sentences)` ([[providers#Embedding provider]]); empty sections are skipped.
