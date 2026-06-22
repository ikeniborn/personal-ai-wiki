from paw.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://MASKING@db:5432/paw")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("FERNET_KEY", "k" * 44)
    s = Settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_url == "redis://redis:6379/0"
    assert s.max_upload_bytes == 10 * 1024 * 1024  # default


def test_settings_missing_required(monkeypatch):
    for k in ("DATABASE_URL", "REDIS_URL", "SESSION_SECRET", "FERNET_KEY"):
        monkeypatch.delenv(k, raising=False)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings()
