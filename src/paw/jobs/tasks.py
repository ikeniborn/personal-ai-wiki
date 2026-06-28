from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC
from typing import Any

from paw.config import get_settings
from paw.db.repos.jobs import JobRepo
from paw.db.repos.sources import SourceRepo
from paw.db.session import get_sessionmaker
from paw.harness.ops.ingest import run_ingest
from paw.harness.prompts import PROMPT_VERSION
from paw.ingest.loaders import load_source
from paw.ingest.loaders.image import describe_image
from paw.ingest.loaders.url import load_url
from paw.jobs.locks import domain_lock, model_lock
from paw.jobs.progress import publish
from paw.obs import metrics
from paw.obs.instrument import instrument_chat, instrument_embedding
from paw.obs.langfuse_client import trace_op
from paw.providers.base import ChatProvider, EmbeddingProvider
from paw.providers.config import ProviderConfig, WikiConfig
from paw.providers.factory import (
    build_chat_provider,
    build_embedding_provider,
    build_vision_provider,
)
from paw.security.secrets import SecretBox
from paw.services.langfuse_settings import LangfuseSettingsService
from paw.services.provider_settings import ProviderSettingsService
from paw.storage.postgres import PostgresStorage


class IngestCancelled(Exception):
    pass


class MaintenanceCancelled(Exception):
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


async def _source_markdown(
    session: Any,
    source_id: str,
    *,
    box: SecretBox,
    wiki: WikiConfig,
    redis: Any,
    pc: ProviderConfig | None = None,
) -> str:
    src = await SourceRepo(session).get(uuid.UUID(source_id))
    if src is None:
        raise RuntimeError("source not found")
    if src.type == "url":
        if not src.url:
            raise RuntimeError("url source missing url")
        from paw.config import parse_allowlist

        settings = get_settings()
        allow = parse_allowlist(settings.url_allowlist)
        return await load_url(src.url, allowlist=allow, max_bytes=settings.max_url_bytes)

    data = await PostgresStorage(session).get(src.storage_ref)
    if src.type == "image":
        if pc is None:
            pc = await ProviderSettingsService(session, box=box).get_provider()
        if pc is None:
            raise RuntimeError("provider not configured")
        vision = build_vision_provider(pc, box)
        if vision is None:
            raise RuntimeError("vision_model not configured; cannot OCR image source")
        prompt = (
            "Transcribe all text in this image and briefly describe any diagrams. "
            f"Respond in {wiki.reasoning_language}."
        )
        async with model_lock(redis, pc.vision_model or "default", kind="ingest"):
            return await describe_image(data, vision, prompt=prompt)
    return load_source(data, src.type)


def _record_job(kind: str, ctx: dict[str, Any], status: str, started: float) -> str:
    """Record job completion metrics; returns `status` for use as `return _record_job(...)`."""
    try:
        try_n = int(ctx.get("job_try", 1) or 1)
        if try_n > 1:
            metrics.JOB_RETRIES.labels(kind=kind).inc()
        if status == "failed":
            max_tries = int(ctx.get("max_tries", 5) or 5)
            if try_n >= max_tries:
                metrics.JOB_DEADLETTER.labels(kind=kind).inc()
        metrics.JOB_DURATION.labels(kind=kind).observe(time.perf_counter() - started)
        metrics.JOB_TOTAL.labels(kind=kind, status=status).inc()
    except Exception:  # noqa: BLE001
        pass  # metrics must never change a job's outcome
    return status


async def ingest_domain(
    ctx: dict[str, Any],
    job_id: str,
    domain_id: str,
    source_id: str | None = None,
    topic: str | None = None,
) -> str:
    started = time.perf_counter()
    redis = ctx["redis"]
    from paw.worker import set_queue_depth  # lazy: tasks is imported by worker (avoid cycle)
    await set_queue_depth(redis)
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
                return _record_job("ingest", ctx, "failed", started)
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()

            lf_cfg = await LangfuseSettingsService(data_s, box=box).load()
            trace = trace_op(
                lf_cfg, name="ingest", trace_id=job_id,
                metadata={"domain_id": domain_id, "prompt_version": PROMPT_VERSION},
            )

            async def on_step(msg: str) -> None:
                if await jobs.is_cancel_requested(jid):
                    raise IngestCancelled()
                trace.span(name=f"tool:{msg}", metadata={})
                await jobs.heartbeat(jid)
                await jobs.append_log(jid, {"step": msg})
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": msg})

            try:
                chat, embedder, wiki, dim = await _build_providers(data_s, box)
                chat = instrument_chat(chat, op="ingest", trace=trace)
                embedder = instrument_embedding(embedder, op="ingest", trace=trace)
                source_md = (
                    await _source_markdown(data_s, source_id, box=box, wiki=wiki, redis=redis)
                    if source_id
                    else (topic or "")
                )
                if not source_md.strip():
                    raise RuntimeError("empty source")
                async with model_lock(redis, getattr(chat, "chat_model", "default"), kind="ingest"):
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
                metrics.ARTICLES.inc()
                metrics.CHUNKS.inc(result.chunk_count)
                await jobs.set_status(jid, "succeeded", article_id=result.article_id)
                await jobs.append_log(jid, {"step": "done"})
                await job_s.commit()
                await _safe_publish(
                    redis,
                    jid,
                    {"step": "done", "status": "succeeded", "article_id": str(result.article_id)},
                )
                return _record_job("ingest", ctx, "succeeded", started)
            except IngestCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return _record_job("ingest", ctx, "cancelled", started)
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("ingest", ctx, "failed", started)
            finally:
                trace.flush()


async def gc_housekeeping(ctx: dict[str, Any]) -> str:
    """Prune chat sessions beyond each user's retention (count + age).

    Admin-triggered in v1. Extensible: Phase 7 adds cache-TTL cleanup here.
    """
    from datetime import datetime, timedelta

    from paw.db.repos.chat import ChatRepo
    from paw.db.repos.domains import DomainRepo
    from paw.db.repos.query_cache import QueryCacheRepo
    from paw.db.repos.users import UserRepo
    from paw.services.provider_settings import ProviderSettingsService
    from paw.services.query_cache import QueryCacheService
    from paw.services.retention import resolve_retention, select_sessions_to_prune

    started = time.perf_counter()
    if redis := ctx.get("redis"):
        from paw.worker import set_queue_depth  # lazy: avoid import cycle

        await set_queue_depth(redis)
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
        # Phase 7: TTL sweep of the query cache, honoring per-domain ttl overrides.
        qc_repo = QueryCacheRepo(session)
        qc_svc = QueryCacheService(session)
        for domain in await DomainRepo(session).list():
            qc_cfg = await qc_svc.config(domain.id)
            cutoff = now - timedelta(seconds=qc_cfg.ttl_seconds)
            await qc_repo.delete_expired(cutoff=cutoff, domain_id=domain.id)
        await session.commit()
    _record_job("gc", ctx, "succeeded", started)
    return f"gc:{pruned}"


async def fix_issues(
    ctx: dict[str, Any], job_id: str, domain_id: str, issue_ids: list[str]
) -> str:
    from datetime import datetime

    from paw.harness.ops.fix import run_fix_issue
    from paw.harness.ops.lint import run_lint
    from paw.services.provider_settings import ProviderSettingsService

    started = time.perf_counter()
    redis = ctx["redis"]
    from paw.worker import set_queue_depth  # lazy: avoid import cycle

    await set_queue_depth(redis)
    box = SecretBox(get_settings().fernet_key)
    jid = uuid.UUID(job_id)
    did = uuid.UUID(domain_id)
    selected = set(issue_ids)
    maker = get_sessionmaker()
    async with maker() as job_s, maker() as data_s:
        jobs = JobRepo(job_s)
        async with domain_lock(redis, domain_id) as got:
            if not got:
                await jobs.set_status(jid, "failed", error="domain busy")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("fix", ctx, "failed", started)
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            lf_cfg = await LangfuseSettingsService(data_s, box=box).load()
            trace = trace_op(
                lf_cfg, name="fix", trace_id=job_id,
                metadata={"domain_id": domain_id, "prompt_version": PROMPT_VERSION},
            )
            try:
                chat, _embedder, wiki, _dim = await _build_providers(data_s, box)
                chat = instrument_chat(chat, op="fix", trace=trace)
                psvc = ProviderSettingsService(data_s, box=box)
                mcfg = await psvc.get_maintenance()
                issues = (
                    await run_lint(data_s, domain_id=did, cfg=mcfg, now=datetime.now(UTC))
                ).issues
                targets = [i for i in issues if i.id in selected]
                fixed = 0
                async with model_lock(redis, getattr(chat, "chat_model", "default"), kind="fix"):
                    for issue in targets:
                        if await jobs.is_cancel_requested(jid):
                            raise MaintenanceCancelled()
                        if await run_fix_issue(
                            data_s, domain_id=did, issue=issue, chat=chat,
                            cfg=wiki, author_id=None,
                        ):
                            fixed += 1
                        await jobs.heartbeat(jid)
                        await jobs.append_log(jid, {"step": "fix", "issue_id": issue.id})
                        # job session only (progress/heartbeat); data_s commits after the loop
                        await job_s.commit()
                        await _safe_publish(redis, jid, {"step": "fix", "issue_id": issue.id})
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "fixed", "count": fixed})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": fixed}
                )
                return _record_job("fix", ctx, "succeeded", started)
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return _record_job("fix", ctx, "cancelled", started)
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("fix", ctx, "failed", started)
            finally:
                trace.flush()


async def format_articles(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from paw.db.repos.articles import ArticleRepo
    from paw.db.repos.citations import CitationRepo
    from paw.harness.ops.format import run_format_article

    started = time.perf_counter()
    redis = ctx["redis"]
    from paw.worker import set_queue_depth  # lazy: avoid import cycle

    await set_queue_depth(redis)
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
                return _record_job("format", ctx, "failed", started)
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            lf_cfg = await LangfuseSettingsService(data_s, box=box).load()
            trace = trace_op(
                lf_cfg, name="format", trace_id=job_id,
                metadata={"domain_id": domain_id, "prompt_version": PROMPT_VERSION},
            )
            try:
                chat, _embedder, wiki, _dim = await _build_providers(data_s, box)
                chat = instrument_chat(chat, op="format", trace=trace)
                repo = ArticleRepo(data_s)
                citations = CitationRepo(data_s)
                articles = await repo.list_by_domain(did)
                formatted = 0
                async with model_lock(redis, getattr(chat, "chat_model", "default"), kind="format"):
                    for art in articles:
                        if await jobs.is_cancel_requested(jid):
                            raise MaintenanceCancelled()
                        names = await repo.entity_names_for(art.id)
                        quotes = [
                            c.quote
                            for c in await citations.list_for_article(art.id)
                            if c.quote
                        ]
                        if await run_format_article(
                            data_s, domain_id=did, article=art, entity_names=names,
                            citation_quotes=quotes, chat=chat, cfg=wiki, author_id=None,
                        ):
                            formatted += 1
                        await jobs.heartbeat(jid)
                        await jobs.append_log(jid, {"step": "format", "slug": art.slug})
                        # job session only (progress/heartbeat); data_s commits after the loop
                        await job_s.commit()
                        await _safe_publish(redis, jid, {"step": "format", "slug": art.slug})
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "formatted", "count": formatted})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": formatted}
                )
                return _record_job("format", ctx, "succeeded", started)
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return _record_job("format", ctx, "cancelled", started)
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("format", ctx, "failed", started)
            finally:
                trace.flush()


async def reindex_domain(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from paw.db.managed import ensure_embedding_column
    from paw.services.provider_settings import ProviderSettingsService
    from paw.vector.reindex import reindex_domain_chunks

    started = time.perf_counter()
    redis = ctx["redis"]
    from paw.worker import set_queue_depth  # lazy: avoid import cycle

    await set_queue_depth(redis)
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
                return _record_job("reindex", ctx, "failed", started)
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()

            async def on_batch(done: int, total: int) -> None:
                if await jobs.is_cancel_requested(jid):
                    raise MaintenanceCancelled()
                await jobs.heartbeat(jid)
                await jobs.append_log(jid, {"step": "batch", "done": done, "total": total})
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "batch", "done": done, "total": total})

            lf_cfg = await LangfuseSettingsService(data_s, box=box).load()
            trace = trace_op(
                lf_cfg, name="reindex", trace_id=job_id,
                metadata={"domain_id": domain_id, "prompt_version": PROMPT_VERSION},
            )
            try:
                chat, embedder, _wiki, dim = await _build_providers(data_s, box)
                embedder = instrument_embedding(embedder, op="reindex", trace=trace)
                psvc = ProviderSettingsService(data_s, box=box)
                target = await psvc.get_embedding_version()
                mcfg = await psvc.get_maintenance()
                await ensure_embedding_column(data_s, dim)
                async with model_lock(
                    redis, getattr(chat, "chat_model", "default"), kind="reindex"
                ):
                    count = await reindex_domain_chunks(
                        data_s, domain_id=did, target_version=target,
                        embedder=embedder, batch_size=mcfg.reindex_batch_size,
                        on_batch=on_batch,
                    )
                await data_s.commit()
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "reindexed", "count": count})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": count}
                )
                return _record_job("reindex", ctx, "succeeded", started)
            except MaintenanceCancelled:
                await data_s.rollback()
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return _record_job("reindex", ctx, "cancelled", started)
            except Exception as e:  # noqa: BLE001
                await data_s.rollback()
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("reindex", ctx, "failed", started)
            finally:
                trace.flush()


async def lint_domain(ctx: dict[str, Any], job_id: str, domain_id: str) -> str:
    from datetime import datetime

    from paw.harness.ops.lint import run_lint
    from paw.services.provider_settings import ProviderSettingsService

    started = time.perf_counter()
    redis = ctx["redis"]
    from paw.worker import set_queue_depth  # lazy: avoid import cycle

    await set_queue_depth(redis)
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
                return _record_job("lint", ctx, "failed", started)
            await jobs.set_status(jid, "running")
            await jobs.heartbeat(jid)
            await job_s.commit()
            try:
                if await jobs.is_cancel_requested(jid):
                    raise MaintenanceCancelled()
                cfg = await ProviderSettingsService(data_s, box=box).get_maintenance()
                result = await run_lint(
                    data_s, domain_id=did, cfg=cfg, now=datetime.now(UTC)
                )
                payload = [
                    {
                        "id": i.id,
                        "kind": i.kind,
                        "target_slug": i.target_slug,
                        "detail": i.detail,
                        "fix": i.fix,
                    }
                    for i in result.issues
                ]
                await jobs.append_log(jid, {"step": "issues", "issues": payload})
                await jobs.set_status(jid, "succeeded")
                await jobs.append_log(jid, {"step": "done"})
                await job_s.commit()
                await _safe_publish(
                    redis, jid, {"step": "done", "status": "succeeded", "count": len(payload)}
                )
                return _record_job("lint", ctx, "succeeded", started)
            except MaintenanceCancelled:
                await jobs.set_status(jid, "cancelled")
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "cancelled", "status": "cancelled"})
                return _record_job("lint", ctx, "cancelled", started)
            except Exception as e:  # noqa: BLE001
                await jobs.set_status(jid, "failed", error=str(e)[:500])
                await job_s.commit()
                await _safe_publish(redis, jid, {"step": "error", "status": "failed"})
                return _record_job("lint", ctx, "failed", started)
