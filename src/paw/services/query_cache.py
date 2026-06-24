from __future__ import annotations

import re
import uuid

from paw.harness.retrieve import Passage, Ref

_WS = re.compile(r"\s+")

PROMPT_VERSION = "1"  # bump if the query system prompt changes materially


def normalize_query(q: str) -> str:
    """Lower, trim, collapse internal whitespace — the exact-match key."""
    return _WS.sub(" ", q.strip().lower())


def passes_threshold(distance: float, sim_threshold: float) -> bool:
    """pgvector <=> is cosine distance (1 - similarity); compare similarity."""
    return (1.0 - distance) >= sim_threshold


def dep_article_ids(refs: list[Ref]) -> list[uuid.UUID]:
    """Dependency article ids from the answer's refs, deduped, order-preserving."""
    return list(dict.fromkeys(r.article_id for r in refs))


def ref_to_json(r: Ref) -> dict[str, str]:
    return {"article_id": str(r.article_id), "slug": r.slug, "title": r.title}


def passage_to_json(p: Passage) -> dict[str, object]:
    return {
        "chunk_id": str(p.chunk_id),
        "article_id": str(p.article_id),
        "slug": p.slug,
        "heading_path": p.heading_path,
        "text": p.text,
        "score": p.score,
    }
