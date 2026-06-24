from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from paw.audit.log import record
from paw.db.models import Article
from paw.harness.prompts import get_prompt
from paw.providers.base import ChatProvider, Message
from paw.providers.config import WikiConfig
from paw.services.cache_seam import mark_cache_stale
from paw.services.ingest_write import upsert_article
from paw.storage.postgres import PostgresStorage


class FormatProposal(BaseModel):
    markdown: str


def check_format_invariant(
    entities: list[str], citations: list[str], new_markdown: str
) -> bool:
    haystack = new_markdown.lower()
    for needle in [*entities, *citations]:
        if needle and needle.lower() not in haystack:
            return False
    return True


async def run_format_article(
    session: AsyncSession,
    *,
    domain_id: uuid.UUID,
    article: Article,
    entity_names: list[str],
    citation_quotes: list[str],
    chat: ChatProvider,
    cfg: WikiConfig,
    author_id: uuid.UUID | None,
) -> bool:
    markdown = (await PostgresStorage(session).get(article.storage_ref)).decode()
    system = get_prompt(
        "format", gen_language=cfg.gen_language, reasoning_language=cfg.reasoning_language
    )
    proposal = await chat.structured(  # type: ignore[attr-defined]
        [
            Message(role="system", content=system),
            Message(role="user", content=f"ARTICLE MARKDOWN:\n{markdown}"),
        ],
        FormatProposal,
        retries=cfg.max_retries,
    )
    if not check_format_invariant(entity_names, citation_quotes, proposal.markdown):
        return False
    art, _ = await upsert_article(
        session,
        domain_id=domain_id,
        slug=article.slug,
        title=article.title,
        markdown=proposal.markdown,
        summary=article.summary or "",
        author_id=author_id,
    )
    await record(
        session,
        user_id=author_id,
        action="tool:format",
        target_type="article",
        target_id=art.id,
        meta={"slug": article.slug},
    )
    await mark_cache_stale(session, domain_id=domain_id, article_ids=[art.id])
    return True
