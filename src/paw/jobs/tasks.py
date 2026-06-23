from __future__ import annotations

import asyncio
import uuid
from datetime import UTC
from typing import Any

from paw.config import get_settings
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.db.session import get_sessionmaker
from paw.harness.ops.ingest import run_ingest
from paw.ingest.loaders import load_source
from paw.jobs.locks import domain_lock, model_lock
from paw.jobs.progress import publish
from paw.providers.base import ChatProvider, EmbeddingProvider
from paw.providers.config import WikiConfig
from paw.providers.factory import build_chat_provider, build_embedding_provider
from paw.security.secrets import SecretBox
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage


class IngestCancelled(Exception):
    pass


async def _safe_publish(redis: Any, jid: uuid.UUID, event: dict[str, Any]) -> None:
    # Progress notifications are best-effort; a Redis hiccup must never change
    # job status or fail an ingest.
    try:
        await publish(redis, jid, event)
    except Exception:  # noqa: BLE001
        pass


async def _build_providers(
    session: Any, box: SecretBox
) -> tuple[ChatProvider, EmbeddingProvider, WikiConfig, int]:
    svc = ProviderSettingsService(session, box=box)
    pc = await svc.get_provider()
    if pc is None:
        raise RuntimeError("provider not configured")
    wiki = await svc.get_wiki()
    chat = build_chat_provider(pc, box)
    embedder = build_embedding_provider(pc, box)
    return chat, embedder, wiki, pc.embedding_dim


async def _source_markdown(session: Any, source_id: str) -> str:
    src = await SourceRepo(session).get(uuid.UUID(source_id))
    if src is None:
        raise RuntimeError("source not found")
    data = await PostgresStorage(session).get(src.storage_ref)
    return load_source(data, src.type)


async def ingest_domain(
    ctx: dict[str, Any],
    job_id: str,
    domain_id: str,
    source_id: str | None = None,
    topic: str | None = None,
) -> str:
    redis = ctx["redis"]
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()

            async def on_step(msg: str) -> None:
                if await jobs.is_cancel_requested(jid):
                    raise IngestCancelled()
                await jobs.heartbeat(jid)
                await jobs.append_log(jid, {"step": msg})
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": msg})

            try:
                chat, embedder, wiki, dim = await _build_providers(data_s, box)
                source_md = (
                    await _source_markdown(data_s, source_id) if source_id else (topic or "")
                )
                if not source_md.strip():
                    raise RuntimeError("empty source")
                async with model_lock(redis, getattr(chat, "chat_model", "default")):
                    result = await asyncio.wait_for(
                        run_ingest(
                            data_s,
                            domain_id=did,
                            source_md=source_md,
                            chat=chat,
                            embedder=embedder,
                            cfg=wiki,
                            dim=dim,
                            on_step=on_step,
                        ),
                        timeout=wiki.request_timeout_s * wiki.max_steps,
                    )
                await data_s.commit()
                await jobs.set_status(jid, "succeeded", article_id=result.article_id)
                await jobs.append_log(jid, {"step": "done"})
                await job_s.commit()
                await _safe_publish(
                    redis,
                    jid,
                    {"step": "done", "status": "succeeded", "article_id": str(result.article_id)},
                )
                return "succeeded"
            except IngestCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return "cancelled"
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return "failed"


async def gc_housekeeping(ctx: dict[str, Any]) -> str:
    """Prune chat sessions beyond each user's retention (count + age).

    Admin-triggered in v1. Extensible: Phase 7 adds cache-TTL cleanup here.
    """
    from datetime import datetime

    from paw.db.repos.chat import ChatRepo
    from paw.db.repos.users import UserRepo
    from paw.services.provider_settings import ProviderSettingsService
    from paw.services.retention import resolve_retention, select_sessions_to_prune

    box = SecretBox(get_settings().fernet_key)
    pruned = 0
    async with get_sessionmaker()() as session:
        cfg = await ProviderSettingsService(session, box=box).get_chat()
        now = datetime.now(UTC)
        repo = ChatRepo(session)
        for user in await UserRepo(session).list():
            prefs = user.chat_prefs if isinstance(user.chat_prefs, dict) else {}
            ret = resolve_retention(cfg, prefs)
            sessions = await repo.list_for_gc(user.id)
            doomed = select_sessions_to_prune(
                sessions,
                max_sessions=ret.max_sessions,
                max_age_days=ret.max_age_days,
                now=now,
            )
            if doomed:
                await repo.delete_by_ids(doomed)
                pruned += len(doomed)
        await session.commit()
    return f"gc:{pruned}"
