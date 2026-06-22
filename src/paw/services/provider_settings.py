from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.repos.settings import SettingsRepo
from paw.providers.config import PROVIDER_KEY, WIKI_KEY, ProviderConfig, WikiConfig
from paw.security.secrets import SecretBox


class ProviderSettingsService:
    def __init__(self, session: AsyncSession, *, box: SecretBox | None = None) -> None:
        self._s = session
        self._repo = SettingsRepo(session)
        self._box = box or SecretBox(get_settings().fernet_key)

    async def _all(self) -> dict[str, object]:
        row = await self._repo.get()
        return dict(row.settings) if row else {}

    async def get_provider(self) -> ProviderConfig | None:
        raw = (await self._all()).get(PROVIDER_KEY)
        return ProviderConfig.model_validate(raw) if raw else None

    async def set_provider(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        api_key: str,
        vision_model: str | None = None,
    ) -> ProviderConfig:
        pc = ProviderConfig(
            base_url=base_url,
            api_key_enc=self._box.encrypt(api_key),
            chat_model=chat_model,
            embedding_model=embedding_model,
            vision_model=vision_model,
            embedding_dim=embedding_dim,
        )
        settings = await self._all()
        settings[PROVIDER_KEY] = pc.model_dump()
        await self._repo.upsert(settings)
        await self._s.commit()
        return pc

    async def get_wiki(self) -> WikiConfig:
        raw = (await self._all()).get(WIKI_KEY)
        return WikiConfig.model_validate(raw) if raw else WikiConfig()

    async def set_wiki(self, cfg: WikiConfig) -> WikiConfig:
        settings = await self._all()
        settings[WIKI_KEY] = cfg.model_dump()
        await self._repo.upsert(settings)
        await self._s.commit()
        return cfg
