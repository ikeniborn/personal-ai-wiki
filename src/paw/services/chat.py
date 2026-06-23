from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.config import get_settings
from paw.db.models import ChatMessage, ChatSession
from paw.db.models import User as UserModel
from paw.db.repos.chat import ChatRepo
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.harness.ops.chat import (
    ChatTurn,
    build_chat_messages,
    dont_know_turn,
    refs_payload,
    to_chat_turn,
    window_turns,
)
from paw.harness.ops.query import DONT_KNOW
from paw.harness.prompts import PROMPT_VERSION
from paw.harness.retrieve import Ref, RetrievedContext, retrieve
from paw.providers.base import ChatProvider, EmbeddingProvider, Message
from paw.providers.config import RetrievalConfig, WikiConfig
from paw.providers.factory import build_chat_provider, build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.services.retention import resolve_retention


def auto_title(question: str, *, max_len: int = 60) -> str:
    stripped = question.strip()
    if not stripped:
        return "New chat"
    first_line = stripped.splitlines()[0].strip()
    return first_line[:max_len].rstrip() or "New chat"


@dataclass
class PreparedTurn:
    chat: ChatProvider
    messages: list[Message] | None  # None -> empty context (don't-know, no LLM)
    ctx: RetrievedContext
    model: str
    prompt_version: str


class ChatService:
    def __init__(self, session: AsyncSession, *, fernet_key: str | None = None) -> None:
        self._s = session
        self._box = SecretBox(fernet_key or get_settings().fernet_key)
        self._redis: object | None = None

    def with_redis(self, redis: object | None) -> ChatService:
        self._redis = redis
        return self

    async def resolve_session(
        self,
        *,
        user: UserModel,
        domain_id: uuid.UUID | None,
        session_id: uuid.UUID | None,
    ) -> ChatSession:
        repo = ChatRepo(self._s)
        if session_id is not None:
            return await self.get_owned(session_id=session_id, user_id=user.id)
        if domain_id is None:
            raise ProblemError(
                status=422,
                title="domain_id required",
                detail="Starting a new chat needs a domain_id.",
            )
        dom = await DomainRepo(self._s).get(domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        # New session is committed before the first turn; an empty session orphaned by a
        # failed turn is benign (GC prunes by age).
        sess = await repo.create_session(user_id=user.id, domain_id=domain_id)
        await self._s.commit()
        return sess

    async def prepare_turn(self, *, session: ChatSession, question: str) -> PreparedTurn:
        psvc = ProviderSettingsService(self._s, box=self._box)
        pc = await psvc.get_provider()
        if pc is None:
            raise ProblemError(
                status=422,
                title="Provider not configured",
                detail="Configure an LLM provider before chatting.",
            )
        dom = await DomainRepo(self._s).get(session.domain_id)
        if dom is None:
            raise ProblemError(status=404, title="Domain not found")
        config = dom.config if isinstance(dom.config, dict) else {}

        global_wiki = await psvc.get_wiki()
        wiki_overrides = config.get("wiki")
        wiki = (
            WikiConfig.model_validate({**global_wiki.model_dump(), **wiki_overrides})
            if isinstance(wiki_overrides, dict)
            else global_wiki
        )

        global_retr = await psvc.get_retrieval()
        retr_overrides = config.get("retrieval")
        retr = (
            RetrievalConfig.model_validate({**global_retr.model_dump(), **retr_overrides})
            if isinstance(retr_overrides, dict)
            else global_retr
        )

        chat_cfg = await psvc.get_chat()
        owner = await UserRepo(self._s).get(session.user_id)
        prefs = owner.chat_prefs if owner and isinstance(owner.chat_prefs, dict) else {}
        depth = resolve_retention(chat_cfg, prefs).history_depth

        rows = await ChatRepo(self._s).list_messages(session.id)
        history = window_turns([(m.role, m.content) for m in rows], depth)

        configured_model = config.get("chat_model")
        model = configured_model if isinstance(configured_model, str) else pc.chat_model

        chat = build_chat_provider(pc, self._box)
        embedder: EmbeddingProvider = build_embedding_provider(pc, self._box)
        ctx = await retrieve(
            self._s,
            domain_id=session.domain_id,
            query=question,
            embedder=embedder,
            cfg=retr,
            embedding_version=await psvc.get_embedding_version(),
            redis=self._redis,
            embed_model=pc.embedding_model,
        )
        messages = build_chat_messages(question, history, ctx, wiki) if ctx.passages else None
        return PreparedTurn(
            chat=chat, messages=messages, ctx=ctx, model=model, prompt_version=PROMPT_VERSION
        )

    async def complete_turn(self, prepared: PreparedTurn) -> tuple[ChatTurn, dict[str, int]]:
        if prepared.messages is None:
            return dont_know_turn(), {}
        result = await prepared.chat.chat(prepared.messages, model=prepared.model)
        return to_chat_turn(result.content or DONT_KNOW, prepared.ctx), result.usage

    async def record_turn(
        self,
        *,
        session: ChatSession,
        question: str,
        answer_md: str,
        refs: list[Ref],
        model: str,
        prompt_version: str,
        usage: dict[str, int],
    ) -> None:
        repo = ChatRepo(self._s)
        if await repo.count_messages(session.id) == 0:
            await repo.set_title(session.id, auto_title(question))
        await repo.add_message(session_id=session.id, role="user", content=question, meta={})
        meta = {
            "refs": refs_payload(refs),
            "model": model,
            "prompt_version": prompt_version,
            "usage": usage,
        }
        await repo.add_message(
            session_id=session.id, role="assistant", content=answer_md, meta=meta
        )
        await repo.bump_last_active(session.id)
        await self._s.commit()

    async def list_user_sessions(
        self, *, user_id: uuid.UUID, limit: int, cursor: tuple[str, str] | None
    ) -> list[ChatSession]:
        # fetch limit+1 so the caller can compute next_cursor
        return await ChatRepo(self._s).list_by_user(user_id, limit=limit + 1, cursor=cursor)

    async def get_owned(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> ChatSession:
        sess = await ChatRepo(self._s).get(session_id)
        if sess is None or sess.user_id != user_id:
            raise ProblemError(status=404, title="Chat session not found")
        return sess

    async def session_messages(self, session_id: uuid.UUID) -> list[ChatMessage]:
        return await ChatRepo(self._s).list_messages(session_id)

    async def delete_owned(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        sess = await self.get_owned(session_id=session_id, user_id=user_id)
        await ChatRepo(self._s).delete(sess)
        await self._s.commit()
