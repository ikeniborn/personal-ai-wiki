from __future__ import annotations

import re
import uuid

# AGE graph name = "g_" + the domain UUID hex (32 lowercase hex chars). 34 chars total,
# always a valid Postgres identifier, and never user-controlled.
GRAPH_NAME_RE = re.compile(r"^g_[0-9a-f]{32}$")


def graph_name(domain_id: uuid.UUID) -> str:
    return f"g_{domain_id.hex}"


def assert_graph_name(name: str) -> str:
    if not GRAPH_NAME_RE.match(name):
        raise ValueError(f"invalid graph name: {name!r}")
    return name
