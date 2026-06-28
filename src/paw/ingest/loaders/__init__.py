from __future__ import annotations


class UnsupportedSource(Exception):
    pass


def load_source(data: bytes, source_type: str) -> str:
    t = source_type.lower().lstrip(".")
    if t in ("md", "markdown", "txt", "text"):
        from paw.ingest.loaders.md import load
    elif t == "pdf":
        from paw.ingest.loaders.pdf import load
    elif t == "docx":
        from paw.ingest.loaders.docx import load
    elif t in ("html", "htm"):
        from paw.ingest.loaders.html import load
    elif t == "epub":
        from paw.ingest.loaders.epub import load
    else:
        raise UnsupportedSource(f"unsupported source type: {source_type}")
    out = load(data).strip()
    if not out:
        raise ValueError("source produced no extractable text")
    return out
