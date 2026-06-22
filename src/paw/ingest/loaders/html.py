from __future__ import annotations

import trafilatura
from markdownify import markdownify


def load(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    extracted = trafilatura.extract(text, output_format="markdown", include_links=False)
    if extracted:
        return str(extracted)
    # fallback: convert raw HTML to markdown
    return str(markdownify(text))
