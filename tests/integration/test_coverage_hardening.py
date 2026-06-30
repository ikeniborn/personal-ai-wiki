import time
import uuid

import pytest
from tests.stubs import StubChatProvider, StubEmbeddingProvider

import paw.jobs.tasks as tasks_mod
import paw.services.maintenance as maintenance_mod
from paw.api.errors import ProblemError
from paw.db.repos.domains import DomainRepo
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.articles import ArticleService
from paw.services.maintenance import MaintenanceService
from paw.services.provider_settings import ProviderSettingsService
from paw.services.setup import SetupService
from paw.services.users import UserService


async def test_maintenance_start_lint_format_reindex(db_session, wired_settings, monkeypatch):
    calls: list[tuple[str, uuid.UUID]] = []

    async def fake_enqueue(_ctx, *, job_id, domain_id, **_kwargs):
        calls.append((str(job_id), domain_id))

    monkeypatch.setattr(maintenance_mod, "enqueue_lint", fake_enqueue)
    monkeypatch.setattr(maintenance_mod, "enqueue_format", fake_enqueue)
    monkeypatch.setattr(maintenance_mod, "enqueue_reindex", fake_enqueue)

    dom = await DomainRepo(db_session).create(name="ops", source_prefix="s", wiki_prefix="w")
    dom.config = {"maintenance": {"enabled_ops": ["lint", "format", "reindex"]}}
    await db_session.commit()

    svc = MaintenanceService(db_session)
    lint = await svc.start_lint(domain_id=dom.id)
    fmt = await svc.start_format(domain_id=dom.id)
    reindex = await svc.start_reindex(domain_id=dom.id)

    assert [lint.kind, fmt.kind, reindex.kind] == ["lint", "format", "reindex"]
    assert [domain_id for _, domain_id in calls] == [dom.id, dom.id, dom.id]


async def test_maintenance_rejects_unknown_or_disabled_domain(db_session, wired_settings):
    svc = MaintenanceService(db_session)
    with pytest.raises(ProblemError) as missing:
        await svc.start_lint(domain_id=uuid.uuid4())
    assert missing.value.status == 404

    dom = await DomainRepo(db_session).create(name="disabled", source_prefix="s", wiki_prefix="w")
    dom.config = {"maintenance": {"enabled_ops": []}}
    await db_session.commit()

    with pytest.raises(ProblemError) as disabled:
        await svc.start_lint(domain_id=dom.id)
    assert disabled.value.status == 422


async def test_setup_already_initialized_409(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()

    with pytest.raises(ProblemError) as exc:
        await SetupService(db_session).complete(
            email="second@example.com",
            password="pw12345678901",
            base_url="https://api.example/v1",
            api_key="sk-x",
            chat_model="chat",
            embedding_model="embed",
            embedding_dim=8,
        )
    assert exc.value.status == 409


async def test_set_role_last_admin_409(db_session, wired_settings):
    admin = await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()

    with pytest.raises(ProblemError) as exc:
        await UserService(db_session).set_role(user_id=admin.id, role="viewer")
    assert exc.value.status == 409


async def test_delete_last_admin_409(db_session, wired_settings):
    admin = await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()

    with pytest.raises(ProblemError) as exc:
        await UserService(db_session).delete(user_id=admin.id)
    assert exc.value.status == 409


async def test_article_rollback_not_found_paths(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="author@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    dom = await DomainRepo(db_session).create(name="articles", source_prefix="s", wiki_prefix="w")
    await db_session.commit()

    svc = ArticleService(db_session)
    with pytest.raises(ProblemError) as missing_article:
        await svc.rollback(article_id=uuid.uuid4(), rev_no=1, author_id=user.id)
    assert missing_article.value.status == 404

    article = await svc.create(
        domain_id=dom.id,
        slug="intro",
        title="Intro",
        markdown="# Intro",
        author_id=user.id,
    )
    with pytest.raises(ProblemError) as missing_revision:
        await svc.rollback(article_id=article.id, rev_no=99, author_id=user.id)
    assert missing_revision.value.status == 404


async def test_job_helpers_log_best_effort_failures(monkeypatch):
    async def broken_publish(_redis, _job_id, _event):
        raise RuntimeError("redis down")

    class BrokenMetric:
        def labels(self, **_labels):
            raise RuntimeError("metrics down")

    log_messages: list[str] = []

    def fake_debug(message: str, *args, **kwargs):
        log_messages.append(message)

    monkeypatch.setattr(tasks_mod, "publish", broken_publish)
    monkeypatch.setattr(tasks_mod.logger, "debug", fake_debug)

    await tasks_mod._safe_publish(object(), uuid.uuid4(), {"step": "x"})
    assert "best-effort job progress publish failed" in log_messages

    assert tasks_mod._record_job(
        "ingest", {"job_try": 2, "max_tries": 5}, "succeeded", time.perf_counter()
    ) == "succeeded"
    assert tasks_mod._record_job(
        "ingest", {"job_try": 5, "max_tries": 5}, "failed", time.perf_counter()
    ) == "failed"

    monkeypatch.setattr(tasks_mod.metrics, "JOB_DURATION", BrokenMetric())
    assert tasks_mod._record_job("ingest", {}, "succeeded", time.perf_counter()) == "succeeded"
    assert "best-effort job metrics update failed" in log_messages


async def test_build_providers_requires_and_builds_provider(
    db_session, wired_settings, monkeypatch
):
    box = SecretBox("k" * 43 + "=")
    with pytest.raises(RuntimeError):
        await tasks_mod._build_providers(db_session, box)

    await ProviderSettingsService(db_session, box=box).set_provider(
        base_url="https://api.example/v1",
        chat_model="chat",
        embedding_model="embed",
        embedding_dim=8,
        api_key="sk-x",
    )

    monkeypatch.setattr(tasks_mod, "build_chat_provider", lambda _pc, _box: StubChatProvider([]))
    monkeypatch.setattr(
        tasks_mod, "build_embedding_provider", lambda _pc, _box: StubEmbeddingProvider(dim=8)
    )

    chat, embedder, wiki, dim = await tasks_mod._build_providers(db_session, box)

    assert isinstance(chat, StubChatProvider)
    assert isinstance(embedder, StubEmbeddingProvider)
    assert wiki.reasoning_language == "en"
    assert dim == 8
