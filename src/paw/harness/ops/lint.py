from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from paw.security.sanitize import extract_wikilink_targets

LINT_KINDS = ("broken_ref", "orphan", "stale", "duplicate_entity")


@dataclass(frozen=True)
class LintIssue:
    id: str
    kind: str
    target_slug: str | None
    detail: str
    fix: str | None


@dataclass(frozen=True)
class LintResult:
    issues: list[LintIssue]


def issue_id(kind: str, target: str, detail: str) -> str:
    return hashlib.sha256(f"{kind}|{target}|{detail}".encode()).hexdigest()[:16]


def find_broken_refs(
    bodies: list[tuple[str, str]], known_slugs: set[str]
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for slug, markdown in bodies:
        for target in extract_wikilink_targets(markdown):
            if target not in known_slugs:
                out.append((slug, target))
    return out


def find_orphans[T](node_ids: list[T], edges: list[tuple[T, T]]) -> list[T]:
    linked: set[T] = set()
    for src, dst in edges:
        linked.add(src)
        linked.add(dst)
    return [n for n in node_ids if n not in linked]


def find_stale[T](
    items: list[tuple[T, datetime]], *, now: datetime, stale_days: int
) -> list[T]:
    cutoff = now - timedelta(days=stale_days)
    return [node for node, ts in items if ts < cutoff]


def find_duplicate_entities(names: list[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(name.strip().lower(), []).append(name)
    return [members for members in groups.values() if len(members) > 1]
