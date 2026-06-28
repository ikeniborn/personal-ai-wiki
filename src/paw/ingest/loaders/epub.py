from __future__ import annotations

import tempfile

import ebooklib
from ebooklib import epub

from paw.ingest.loaders.html import load as html_to_md


def load(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
        tmp.write(data)
        tmp.flush()
        book = epub.read_epub(tmp.name)

    parts: list[str] = []
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        html_bytes = item.get_body_content()
        md = html_to_md(html_bytes).strip()
        if md:
            parts.append(md)
    return "\n\n".join(parts)
