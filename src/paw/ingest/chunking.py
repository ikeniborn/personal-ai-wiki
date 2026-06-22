from __future__ import annotations

import math
import re
from dataclasses import dataclass

from paw.providers.base import EmbeddingProvider
from paw.providers.config import WikiConfig

_HEADING = re.compile(r"^##\s+(.*)$", re.MULTILINE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ChunkSpec:
    kind: str
    ord: int
    heading_path: str | None
    text: str


def split_sections(markdown: str) -> list[tuple[str | None, str]]:
    matches = list(_HEADING.finditer(markdown))
    sections: list[tuple[str | None, str]] = []
    intro = markdown[: matches[0].start()] if matches else markdown
    if intro.strip():
        sections.append((None, intro.strip()))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body:
            sections.append((m.group(1).strip(), body))
    return sections


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _greedy_chunks(
    sentences: list[str], embeddings: list[list[float]], *, target_size: int, overlap: int
) -> list[str]:
    if not sentences:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for i, sent in enumerate(sentences):
        boundary = False
        if cur and i > 0:
            # semantic breakpoint: low similarity to the previous sentence
            sim = cosine(embeddings[i], embeddings[i - 1])
            if size + len(sent) > target_size or sim < 0.2:
                boundary = True
        if boundary:
            chunks.append(" ".join(cur))
            cur = cur[-overlap:] if overlap > 0 else []
            size = sum(len(s) for s in cur)
        cur.append(sent)
        size += len(sent)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


async def build_chunks(
    *, summary: str, markdown: str, embedder: EmbeddingProvider, cfg: WikiConfig
) -> list[ChunkSpec]:
    out: list[ChunkSpec] = [ChunkSpec(kind="summary", ord=0, heading_path=None, text=summary)]
    ordinal = 1
    for heading, body in split_sections(markdown):
        sentences = split_sentences(body)
        if not sentences:
            continue
        embeddings = await embedder.embed(sentences)
        for text in _greedy_chunks(
            sentences,
            embeddings,
            target_size=cfg.chunk_target_size,
            overlap=cfg.chunk_overlap_sentences,
        ):
            out.append(ChunkSpec(kind="section", ord=ordinal, heading_path=heading, text=text))
            ordinal += 1
    return out
