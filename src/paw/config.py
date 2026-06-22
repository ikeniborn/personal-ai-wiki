from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    session_secret: str
    fernet_key: str

    # limits (env layer; LLD §10)
    max_upload_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 12 * 1024 * 1024
    session_ttl_seconds: int = 60 * 60 * 24 * 7


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
