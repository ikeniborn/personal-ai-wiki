from __future__ import annotations

import re

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def load(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return _FRONTMATTER.sub("", text, count=1)
