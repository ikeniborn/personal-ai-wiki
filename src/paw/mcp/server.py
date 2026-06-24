from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.models import Domain
from paw.db.repos.domains import DomainRepo
from paw.db.session import get_sessionmaker
from paw.mcp import tools as mcp_tools
from paw.providers.config import RetrievalConfig
from paw.providers.factory import build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


async def _resolve_domain(session: AsyncSession, name: str) -> Domain:
    dom = await DomainRepo(session).get_by_name(name)
    if dom is None:
        raise ValueError(f"unknown domain: {name}")
    return dom


def _retrieval_for(global_retr: RetrievalConfig, dom: Domain) -> RetrievalConfig:
    overrides = dom.config.get("retrieval") if isinstance(dom.config, dict) else None
    if isinstance(overrides, dict):
        return RetrievalConfig.model_validate({**global_retr.model_dump(), **overrides})
    return global_retr


def build_mcp() -> FastMCP:
    mcp = FastMCP("paw", stateless_http=True, json_response=True)
    # Mounted at /mcp; serve the endpoint at the mount root so the URL is exactly /mcp.
    mcp.settings.streamable_http_path = "/"

    @mcp.tool(description="Hybrid search (vector + FTS + graph) over a domain's wiki. Read-only.")
    async def search_wiki(query: str, domain: str, top_k: int | None = None) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            psvc = ProviderSettingsService(session)
            pc = await psvc.get_provider()
            if pc is None:
                raise ValueError("provider not configured")
            dom = await _resolve_domain(session, domain)
            cfg = _retrieval_for(await psvc.get_retrieval(), dom)
            embedder = build_embedding_provider(pc, SecretBox(get_settings().fernet_key))
            result = await mcp_tools.search_wiki(
                session,
                domain_id=dom.id,
                query=query,
                embedder=embedder,
                cfg=cfg,
                embedding_version=await psvc.get_embedding_version(),
                top_k=top_k,
            )
            return result.model_dump()

    @mcp.tool(description="Get a domain article by id or slug. Read-only.")
    async def get_article(ref: str, domain: str) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            dom = await _resolve_domain(session, domain)
            result = await mcp_tools.get_article(session, domain_id=dom.id, ref=ref)
            return result.model_dump()

    @mcp.tool(description="List typed links (outgoing + backlinks) for an article. Read-only.")
    async def list_links(article: str, domain: str) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            dom = await _resolve_domain(session, domain)
            result = await mcp_tools.list_links(session, domain_id=dom.id, ref=article)
            return result.model_dump()

    return mcp
