from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from paw.config import get_settings
from paw.db.managed import ensure_embedding_column, rebuild_embedding_column
from paw.db.repos.settings import SettingsRepo
from paw.providers.config import (
    PROVIDER_KEY,
    RETRIEVAL_KEY,
    WIKI_KEY,
    ProviderConfig,
    RetrievalConfig,
    WikiConfig,
)
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

    async def persist_provider(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        api_key: str,
        vision_model: str | None = None,
    ) -> ProviderConfig:
        """Write the provider config to the session WITHOUT committing.

        The caller owns the commit boundary, so the provider row and any
        related migration (embedding column) land in a single transaction.
        """
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
        return pc

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
        pc = await self.persist_provider(
            base_url=base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            api_key=api_key,
            vision_model=vision_model,
        )
        await self._s.commit()
        return pc

    async def update_provider(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        api_key: str,
        vision_model: str | None = None,
    ) -> ProviderConfig:
        from paw.db.managed import embedding_dim as current_embedding_dim

        current = await current_embedding_dim(self._s)
        pc = await self.persist_provider(
            base_url=base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            api_key=api_key,
            vision_model=vision_model,
        )
        if current is not None and current != embedding_dim:
            await rebuild_embedding_column(self._s, embedding_dim)
        else:
            await ensure_embedding_column(self._s, embedding_dim)
        await self._s.commit()
        return pc

    async def get_wiki(self) -> WikiConfig:
        raw = (await self._all()).get(WIKI_KEY)
        return WikiConfig.model_validate(raw) if raw else WikiConfig()

    async def get_retrieval(self) -> RetrievalConfig:
        raw = (await self._all()).get(RETRIEVAL_KEY)
        return RetrievalConfig.model_validate(raw) if raw else RetrievalConfig()

    async def set_wiki(self, cfg: WikiConfig) -> WikiConfig:
        settings = await self._all()
        settings[WIKI_KEY] = cfg.model_dump()
        await self._repo.upsert(settings)
        await self._s.commit()
        return cfg
