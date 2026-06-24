from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.articles import ArticleRepo
from paw.db.repos.entities import EntityRepo
from paw.db.repos.links import LinkRepo
from paw.providers.config import MaintenanceConfig
from paw.security.sanitize import extract_wikilink_targets
from paw.storage.postgres import PostgresStorage

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


async def run_lint(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    cfg: MaintenanceConfig,
    now: datetime,
) -> LintResult:
    articles = await ArticleRepo(session).list_by_domain(domain_id)
    store = PostgresStorage(session)
    known_slugs = {a.slug for a in articles}
    slug_of = {a.id: a.slug for a in articles}

    bodies: list[tuple[str, str]] = []
    for a in articles:
        markdown = (await store.get(a.storage_ref)).decode()
        bodies.append((a.slug, markdown))

    edges = await LinkRepo(session).domain_link_pairs(domain_id)
    entity_names = [e.name for e in await EntityRepo(session).list_by_domain(domain_id)]

    issues: list[LintIssue] = []

    for article_slug, missing in find_broken_refs(bodies, known_slugs):
        issues.append(
            LintIssue(
                id=issue_id("broken_ref", article_slug, missing),
                kind="broken_ref",
                target_slug=article_slug,
                detail=f"broken wikilink [[{missing}]]",
                fix=f"remove or correct the [[{missing}]] link",
            )
        )

    for aid in find_orphans([a.id for a in articles], edges):
        slug = slug_of[aid]
        issues.append(
            LintIssue(
                id=issue_id("orphan", slug, ""),
                kind="orphan",
                target_slug=slug,
                detail="article has no incoming or outgoing links",
                fix="add a link connecting this article to a related one",
            )
        )

    for aid in find_stale(
        [(a.id, a.updated_at) for a in articles], now=now, stale_days=cfg.stale_days
    ):
        slug = slug_of[aid]
        issues.append(
            LintIssue(
                id=issue_id("stale", slug, ""),
                kind="stale",
                target_slug=slug,
                detail=f"not updated in over {cfg.stale_days} days",
                fix="review and refresh the article content",
            )
        )

    for group in find_duplicate_entities(entity_names):
        detail = "duplicate entity names: " + ", ".join(group)
        issues.append(
            LintIssue(
                id=issue_id("duplicate_entity", group[0].strip().lower(), detail),
                kind="duplicate_entity",
                target_slug=None,
                detail=detail,
                fix="merge the duplicate entities (deferred)",
            )
        )

    return LintResult(issues=issues)
