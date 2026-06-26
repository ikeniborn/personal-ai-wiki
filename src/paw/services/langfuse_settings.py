from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.obs.langfuse_client import LangfuseConfig
from paw.security.secrets import SecretBox
from paw.services.settings import SettingsService


class LangfuseSettingsService:
    def __init__(self, session: AsyncSession, *, box: SecretBox | None = None) -> None:
        self._svc = SettingsService(session)
        self._box = box or SecretBox(get_settings().fernet_key)

    async def load(self) -> LangfuseConfig:
        s = await self._svc.get()
        enc = s.get("langfuse_secret_key_enc") or ""
        secret = self._box.decrypt(str(enc)) if enc else ""
        return LangfuseConfig(
            enabled=bool(s.get("langfuse_enabled", False)),
            host=str(s.get("langfuse_host", "")),
            public_key=str(s.get("langfuse_public_key", "")),
            secret_key=secret,
            redact_input=bool(s.get("langfuse_redact_input", False)),
            sample_rate=float(s.get("langfuse_sample_rate", 1.0)),
        )

    async def save(
        self,
        *,
        enabled: bool,
        host: str,
        public_key: str,
        secret_key: str,
    ) -> None:
        current = await self._svc.get()
        current.update(
            {
                "langfuse_enabled": enabled,
                "langfuse_host": host,
                "langfuse_public_key": public_key,
                "langfuse_secret_key_enc": self._box.encrypt(secret_key) if secret_key else "",
            }
        )
        await self._svc.update(current)
