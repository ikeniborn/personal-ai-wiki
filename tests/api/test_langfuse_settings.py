"""API round-trip test: Langfuse config persisted via PUT /api/v1/settings.

Approach (A): encrypt the secret in-test with SecretBox using the wired FERNET_KEY,
PUT all four langfuse_* keys through the existing settings endpoint (CSRF + admin auth),
then call LangfuseSettingsService.load() in a separate session and assert the
decrypted secret equals the original plaintext.

Approach (A) was chosen over (B) because the task spec requires proving the settings
API persists the keys — the PUT endpoint is the production path a client would use.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from paw.config import get_settings
from paw.db.repos.users import UserRepo
from paw.main import create_app
from paw.security.passwords import hash_password
from paw.security.secrets import SecretBox
from paw.services.langfuse_settings import LangfuseSettingsService


@pytest.fixture
async def admin_client(db_session, wired_settings):
    await UserRepo(db_session).create(
        email="admin@example.com", pw_hash=hash_password("pw12345"), role="admin"
    )
    await db_session.commit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        await c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "pw12345"}
        )
        csrf = c.cookies.get("paw_csrf")
        yield c, csrf


async def test_langfuse_settings_round_trip(admin_client, db_session):
    """PUT langfuse_* keys via the settings API; load() must decrypt secret correctly."""
    c, csrf = admin_client

    box = SecretBox(get_settings().fernet_key)
    encrypted = box.encrypt("sk-secret")

    r = await c.put(
        "/api/v1/settings",
        json={
            "langfuse_enabled": True,
            "langfuse_host": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key_enc": encrypted,
        },
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200

    # Fresh read via service — proves encrypt-at-rest + decrypt round-trip.
    cfg = await LangfuseSettingsService(db_session).load()
    assert cfg.enabled is True
    assert cfg.host == "https://cloud.langfuse.com"
    assert cfg.public_key == "pk-test-123"
    assert cfg.secret_key == "sk-secret"  # decrypted plaintext, not the Fernet token


async def test_langfuse_settings_missing_secret_returns_empty(admin_client, db_session):
    """Absent langfuse_secret_key_enc must not crash load(); secret_key defaults to ''."""
    c, csrf = admin_client

    r = await c.put(
        "/api/v1/settings",
        json={
            "langfuse_enabled": False,
            "langfuse_host": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test-456",
        },
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200

    cfg = await LangfuseSettingsService(db_session).load()
    assert cfg.enabled is False
    assert cfg.secret_key == ""
