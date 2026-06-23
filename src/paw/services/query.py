from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.repos.domains import DomainRepo
from paw.harness.ops.query import QueryAnswer, build_messages, dont_know, to_answer
from paw.harness.retrieve import RetrievedContext, retrieve
from paw.providers.base import ChatProvider, EmbeddingProvider, Message
from paw.providers.config import RetrievalConfig
from paw.providers.factory import build_chat_provider, build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService


@dataclass
class Prepared:
    chat: ChatProvider
    messages: list[Message] | None  # None -> empty context (don't-know)
    ctx: RetrievedContext


class QueryService:
    def __init__(self, session: AsyncSession, *, fernet_key: str | None = None) -> None:
        self._s = session
        self._box = SecretBox(fernet_key or get_settings().fernet_key)
        self._redis: object | None = None

    def with_redis(self, redis: object | None) -> QueryService:
        self._redis = redis
        return self

    async def prepare(self, *, domain_id: uuid.UUID, question: str) -> Prepared:
        psvc = ProviderSettingsService(self._s, box=self._box)
        pc = await psvc.get_provider()
        if pc is None:
            raise ProblemError(
                status=422,
                title="Provider not configured",
                detail="Configure an LLM provider before querying.",
            )
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")

        wiki = await psvc.get_wiki()
        global_retr = await psvc.get_retrieval()
        domain_overrides = dom.config.get("retrieval") if isinstance(dom.config, dict) else None
        retr = (
            RetrievalConfig.model_validate({**global_retr.model_dump(), **domain_overrides})
            if isinstance(domain_overrides, dict)
            else global_retr
        )

        chat = build_chat_provider(pc, self._box)
        embedder: EmbeddingProvider = build_embedding_provider(pc, self._box)
        ctx = await retrieve(
            self._s,
            domain_id=domain_id,
            query=question,
            embedder=embedder,
            cfg=retr,
            embedding_version=await psvc.get_embedding_version(),
            redis=self._redis,
            embed_model=pc.embedding_model,
        )
        messages = build_messages(question, ctx, wiki) if ctx.passages else None
        return Prepared(chat=chat, messages=messages, ctx=ctx)

    async def complete(self, prepared: Prepared) -> QueryAnswer:
        if prepared.messages is None:
            return dont_know()
        result = await prepared.chat.chat(prepared.messages)
        from paw.harness.ops.query import DONT_KNOW

        return to_answer(result.content or DONT_KNOW, prepared.ctx)

    async def answer(self, *, domain_id: uuid.UUID, question: str) -> QueryAnswer:
        prepared = await self.prepare(domain_id=domain_id, question=question)
        return await self.complete(prepared)
