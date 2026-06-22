from __future__ import annotations

import fitz  # type: ignore[import-untyped]  # pymupdf


def load(data: bytes) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
