import uuid

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import delete, func, select

import paw.services.jobs as jobs_svc
from paw.api.deps import get_session_store
from paw.audit import actions
from paw.db.models import AuditLog
from paw.db.repos.domains import DomainRepo
from paw.db.repos.sources import SourceRepo
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.api_keys import ApiKeyService
from paw.services.articles import ArticleService
from paw.services.jobs import JobService
from paw.services.provider_settings import ProviderSettingsService
from paw.services.setup import SetupService
from paw.services.users import UserService


async def _count(session, action: str) -> int:
    res = await session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
    )
    return int(res.scalar_one())


async def _latest(session, action: str) -> AuditLog:
    res = await session.execute(
        select(AuditLog)
        .where(AuditLog.action == action)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    return res.scalar_one()


async def test_user_create_writes_audit_row(db_session, wired_settings):
    before = await _count(db_session, actions.USER_CREATE)
    created = await UserService(db_session).create(
        email="audited@example.com", password="pw12345678901", role="viewer"
    )
    after = await _count(db_session, actions.USER_CREATE)
    row = await _latest(db_session, actions.USER_CREATE)
    assert after == before + 1
    assert row.target_type == "user"
    assert row.target_id == created.id


async def test_user_admin_operations_write_audit_rows(db_session, wired_settings):
    actor = await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    target = await UserRepo(db_session).create(
        email="viewer@example.com", pw_hash=hash_password("pw12345678901"), role="viewer"
    )
    await db_session.commit()

    await UserService(db_session).set_role(user_id=target.id, role="editor", actor_id=actor.id)
    role_row = await _latest(db_session, actions.USER_ROLE_CHANGE)
    assert role_row.user_id == actor.id
    assert role_row.target_type == "user"
    assert role_row.target_id == target.id

    await UserService(db_session).delete(user_id=target.id, actor_id=actor.id)
    delete_row = await _latest(db_session, actions.USER_DELETE)
    assert delete_row.user_id == actor.id
    assert delete_row.target_type == "user"
    assert delete_row.target_id == target.id

    assert actor.id is not None


async def test_setup_complete_writes_audit_row(db_session, wired_settings):
    admin = await SetupService(db_session).complete(
        email="setup@example.com",
        password="pw12345678901",
        base_url="https://api.example/v1",
        api_key="sk-secret",
        chat_model="chat",
        embedding_model="embedding",
        embedding_dim=8,
    )
    row = await _latest(db_session, actions.SETUP_COMPLETE)
    assert row.user_id == admin.id
    assert row.target_type == "user"
    assert row.target_id == admin.id


async def test_api_key_issue_and_revoke_write_audit_rows(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="keys@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    issued = await ApiKeyService(db_session).issue(user_id=user.id, scopes=["read"])
    issue_row = await _latest(db_session, actions.API_KEY_ISSUE)
    assert issue_row.user_id == user.id
    assert issue_row.target_type == "api_key"
    assert issue_row.target_id == issued.id

    await ApiKeyService(db_session).revoke(user_id=user.id, key_id=issued.id)
    revoke_row = await _latest(db_session, actions.API_KEY_REVOKE)
    assert revoke_row.user_id == user.id
    assert revoke_row.target_type == "api_key"
    assert revoke_row.target_id == issued.id


async def test_provider_changes_write_audit_rows(db_session, wired_settings):
    actor = await UserRepo(db_session).create(
        email="provider@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await db_session.commit()
    key = Fernet.generate_key().decode()
    svc = ProviderSettingsService(db_session, box=SecretBox(key))
    await svc.set_provider(
        base_url="https://user:pass@api.example/v1?token=secret",
        chat_model="chat-1",
        embedding_model="embedding-1",
        embedding_dim=8,
        api_key="sk-secret",
        actor_id=actor.id,
    )
    await svc.update_provider(
        base_url="https://api.example/v1",
        chat_model="chat-2",
        embedding_model="embedding-2",
        embedding_dim=8,
        api_key="sk-secret",
        actor_id=actor.id,
    )
    assert await _count(db_session, actions.PROVIDER_CHANGE) == 2
    row = await _latest(db_session, actions.PROVIDER_CHANGE)
    assert row.user_id == actor.id
    assert "base_url" not in row.meta
    assert "api_key" not in row.meta


async def test_start_ingest_writes_audit_row(db_session, wired_settings):
    actor = await UserRepo(db_session).create(
        email="ingest@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    domain = await DomainRepo(db_session).create(
        name="Docs", source_prefix="sources/docs", wiki_prefix="wiki/docs"
    )
    source = await SourceRepo(db_session).create(
        domain_id=domain.id,
        storage_ref="blob:source",
        filename="source.md",
        type="markdown",
        checksum="abc",
    )
    await db_session.commit()

    await JobService(db_session).start_ingest(
        domain_id=domain.id, source_id=source.id, actor_id=actor.id
    )
    row = await _latest(db_session, actions.INGEST_START)
    assert row.user_id == actor.id
    assert row.target_type == "source"
    assert row.target_id == source.id


async def test_init_domain_writes_ingest_audit_rows(
    db_session, wired_settings, monkeypatch: MonkeyPatch
):
    actor = await UserRepo(db_session).create(
        email="init@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    await ProviderSettingsService(db_session).set_provider(
        base_url="https://api.example/v1",
        chat_model="gpt-x",
        embedding_model="emb-x",
        embedding_dim=8,
        api_key="sk-x",
        actor_id=actor.id,
    )
    domain = await DomainRepo(db_session).create(
        name="Init", source_prefix="sources/init", wiki_prefix="wiki/init"
    )
    await db_session.commit()

    async def fake_plan(*, domain_name, brief, chat, cfg):
        return ["Alpha", "Beta"]

    async def fake_enqueue(redis, **kwargs):
        return None

    monkeypatch.setattr(jobs_svc, "build_structure_plan", fake_plan)
    monkeypatch.setattr(jobs_svc, "enqueue_ingest", fake_enqueue)

    before = await _count(db_session, actions.INGEST_START)
    await JobService(db_session).init_domain(
        domain_id=domain.id, brief="seed", actor_id=actor.id
    )
    after = await _count(db_session, actions.INGEST_START)

    assert after == before + 2
    row = await _latest(db_session, actions.INGEST_START)
    assert row.user_id == actor.id
    assert row.target_type == "topic"
    assert row.meta["topic"] in {"Alpha", "Beta"}


async def test_article_rollback_writes_audit_row(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="author@example.com", pw_hash=hash_password("pw12345678901"), role="admin"
    )
    domain = await DomainRepo(db_session).create(
        name="Articles", source_prefix="sources/articles", wiki_prefix="wiki/articles"
    )
    await db_session.commit()

    svc = ArticleService(db_session)
    article = await svc.create(
        domain_id=domain.id,
        slug="topic",
        title="Topic",
        markdown="# One",
        author_id=user.id,
    )
    await svc.update(
        article_id=article.id,
        expected_rev=1,
        title="Topic",
        markdown="# Two",
        author_id=user.id,
    )
    before = await _count(db_session, actions.INGEST_ROLLBACK)
    await svc.rollback(article_id=article.id, rev_no=1, author_id=user.id)

    after = await _count(db_session, actions.INGEST_ROLLBACK)
    row = await _latest(db_session, actions.INGEST_ROLLBACK)
    assert after == before + 1
    assert row.user_id == user.id
    assert row.target_type == "article"
    assert row.target_id == article.id


async def test_login_and_logout_write_audit_rows(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="login@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "login@example.com", "password": "pw12345"}
        )
        assert login.status_code == 200
        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204

    login_row = await _latest(db_session, actions.LOGIN)
    assert login_row.user_id == user.id
    logout_row = await _latest(db_session, actions.LOGOUT)
    assert logout_row.user_id == user.id


async def test_logout_with_deleted_user_session_clears_session(db_session, wired_settings):
    user = await UserRepo(db_session).create(
        email="deleted-login@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://t") as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "deleted-login@example.com", "password": "pw12345"},
        )
        assert login.status_code == 200
        sid = client.cookies.get("paw_session")
        assert sid
        await db_session.execute(delete(AuditLog).where(AuditLog.user_id == user.id))
        await UserRepo(db_session).delete(user.id)
        await db_session.commit()

        logout = await client.post("/api/v1/auth/logout")

    assert logout.status_code == 204
    assert await get_session_store().get(sid) is None


async def test_admin_api_user_create_records_actor(db_session, wired_settings):
    admin = await UserRepo(db_session).create(
        email="actor@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "actor@example.com", "password": "pw12345"}
        )
        assert login.status_code == 200
        csrf = client.cookies.get("paw_csrf")
        assert csrf
        created = await client.post(
            "/api/v1/users",
            json={
                "email": "created-by-admin@example.com",
                "password": "pw12345678901",
                "role": "viewer",
            },
            headers={"x-csrf-token": csrf},
        )
        assert created.status_code == 201

    row = await _latest(db_session, actions.USER_CREATE)
    assert row.user_id == admin.id
    assert row.target_id == uuid.UUID(created.json()["id"])
