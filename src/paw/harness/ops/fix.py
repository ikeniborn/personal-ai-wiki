from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from paw.audit.log import record
from paw.db.repos.articles import ArticleRepo
from paw.graph.repo import GraphRepo
from paw.harness.ops.lint import LintIssue
from paw.harness.prompts import get_prompt
from paw.providers.base import ChatProvider, Message
from paw.providers.config import WikiConfig
from paw.services.cache_seam import mark_domain_cache_stale
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


class FixLink(BaseModel):
    dst_slug: str
    type: str


class FixProposal(BaseModel):
    markdown: str
    summary: str = ""
    add_links: list[FixLink] = Field(default_factory=list)


async def propose_fix(
    chat: ChatProvider,
    *,
    article_title: str,
    article_markdown: str,
    issue: LintIssue,
    cfg: WikiConfig,
) -> FixProposal:
    system = get_prompt(
        "fix", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    user = (
        f"ISSUE ({issue.kind}): {issue.detail}\nSUGGESTED FIX: {issue.fix}\n\n"
        f"ARTICLE TITLE: {article_title}\nARTICLE MARKDOWN:\n{article_markdown}"
    )
    return await chat.structured(  # type: ignore[attr-defined,no-any-return]
        [Message(role="system", content=system), Message(role="user", content=user)],
        FixProposal,
        retries=cfg.max_retries,
    )


async def apply_fix(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    issue: LintIssue,
    proposal: FixProposal,
    author_id: uuid.UUID | None,
) -> bool:
    if issue.target_slug is None:
        return False
    repo = ArticleRepo(session)
    slug_map = await repo.slug_id_map(domain_id)
    target_id = slug_map.get(issue.target_slug)
    if target_id is None:
        return False
    target = await repo.get(target_id)
    if target is None:
        return False
    art, _ = await upsert_article(
        session,
        domain_id=domain_id,
        slug=target.slug,
        title=target.title,
        markdown=proposal.markdown,
        summary=proposal.summary or (target.summary or ""),
        author_id=author_id,
    )
    graph = GraphRepo(session)
    allowed = WikiConfig().link_types
    for link in proposal.add_links:
        dst_id = slug_map.get(link.dst_slug)
        if dst_id is None or dst_id == art.id or link.type not in allowed:
            continue
        await graph.link(
            domain_id=domain_id, src_article_id=art.id, dst_article_id=dst_id, type=link.type
        )
    await record(
        session,
        user_id=author_id,
        action="tool:fix",
        target_type="article",
        target_id=art.id,
        meta={"issue_kind": issue.kind, "issue_id": issue.id},
    )
    await mark_domain_cache_stale(session, domain_id)
    return True


async def run_fix_issue(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    issue: LintIssue,
    chat: ChatProvider,
    cfg: WikiConfig,
    author_id: uuid.UUID | None,
) -> bool:
    if issue.target_slug is None:
        return False
    repo = ArticleRepo(session)
    slug_map = await repo.slug_id_map(domain_id)
    target_id = slug_map.get(issue.target_slug)
    if target_id is None:
        return False
    target = await repo.get(target_id)
    if target is None:
        return False
    markdown = (await PostgresStorage(session).get(target.storage_ref)).decode()
    proposal = await propose_fix(
        chat,
        article_title=target.title,
        article_markdown=markdown,
        issue=issue,
        cfg=cfg,
    )
    return await apply_fix(
        session, domain_id=domain_id, issue=issue, proposal=proposal, author_id=author_id
    )
