from __future__ import annotations

import io

import mammoth  # type: ignore[import-untyped]


def load(data: bytes) -> str:
    result = mammoth.convert_to_markdown(io.BytesIO(data))
    return str(result.value)
