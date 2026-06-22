from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import AppSettings


class SettingsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> AppSettings | None:
        res = await self._s.execute(select(AppSettings).where(AppSettings.id.is_(True)))
        return res.scalar_one_or_none()

    async def upsert(self, settings: dict[str, Any]) -> AppSettings:
        row = await self.get()
        if row is None:
            row = AppSettings(id=True, settings=settings)
            self._s.add(row)
        else:
            row.settings = settings
        await self._s.flush()
        return row
