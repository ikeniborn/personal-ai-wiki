from functools import lru_cache

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_allowlist(raw: str) -> list[str]:
    """Split a comma-separated host-suffix allowlist into a normalized list."""
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


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
    worker_metrics_port: int = 0  # >0 starts a prometheus http server in the worker
    login_rate_limit: PositiveInt = 5
    login_rate_window_seconds: PositiveInt = 60
    login_lockout_threshold: PositiveInt = 10
    login_lockout_seconds: PositiveInt = 900
    password_min_length: int = 12

    # hardening (env layer; LLD §11)
    url_allowlist: str = ""  # comma-separated host suffixes; "" = any public host
    max_url_bytes: int = 5 * 1024 * 1024
    max_unzip_bytes: int = 100 * 1024 * 1024
    max_unzip_entries: int = 2000
    max_compression_ratio: float = 100.0
    metrics_token: str | None = None  # Bearer token gating /metrics; unset = endpoint disabled


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
