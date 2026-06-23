from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from paw.providers.config import ChatConfig


@dataclass(frozen=True)
class Retention:
    history_depth: int
    max_sessions: int
    max_age_days: int


def _pick(value: Any, default: int) -> int:
    # null / absent -> global default (spec: "null key -> global default")
    return int(value) if value is not None else default


def resolve_retention(cfg: ChatConfig, prefs: dict[str, Any]) -> Retention:
    ret = prefs.get("retention") or {}
    return Retention(
        history_depth=_pick(prefs.get("history_depth"), cfg.history_depth),
        max_sessions=_pick(ret.get("max_sessions"), cfg.retention_max_sessions),
        max_age_days=_pick(ret.get("max_age_days"), cfg.retention_max_age_days),
    )


def select_sessions_to_prune(
    sessions: list[tuple[uuid.UUID, datetime]],
    *,
    max_sessions: int,
    max_age_days: int,
    now: datetime,
) -> list[uuid.UUID]:
    """Return ids to delete: those beyond max_sessions (by recency) OR older than max_age_days."""
    cutoff = now - timedelta(days=max_age_days)
    ordered = sorted(sessions, key=lambda s: s[1], reverse=True)
    doomed: list[uuid.UUID] = []
    for index, (sid, last_active) in enumerate(ordered):
        if index >= max_sessions or last_active < cutoff:
            doomed.append(sid)
    return doomed
