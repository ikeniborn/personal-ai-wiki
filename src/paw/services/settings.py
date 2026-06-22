from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.repos.settings import SettingsRepo


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = SettingsRepo(session)

    async def get(self) -> dict[str, Any]:
        row = await self._repo.get()
        return dict(row.settings) if row else {}

    async def update(self, settings: dict[str, Any]) -> dict[str, Any]:
        row = await self._repo.upsert(settings)
        await self._s.commit()
        return dict(row.settings)
