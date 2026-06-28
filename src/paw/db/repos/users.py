import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import User


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, email: str, pw_hash: str, role: str = "viewer") -> User:
        u = User(email=email, pw_hash=pw_hash, role=role)
        self._s.add(u)
        await self._s.flush()
        return u

    async def get_by_email(self, email: str) -> User | None:
        res = await self._s.execute(select(User).where(User.email == email))
        return res.scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def count(self) -> int:
        res = await self._s.execute(select(func.count()).select_from(User))
        return int(res.scalar_one())

    async def list(self) -> list[User]:
        res = await self._s.execute(select(User).order_by(User.created_at))
        return list(res.scalars().all())

    async def set_role(self, user_id: uuid.UUID, role: str) -> bool:
        res = cast(
            CursorResult[Any],
            await self._s.execute(
                update(User).where(User.id == user_id).values(role=role)
            ),
        )
        return bool(res.rowcount)

    async def delete(self, user_id: uuid.UUID) -> bool:
        res = cast(
            CursorResult[Any],
            await self._s.execute(delete(User).where(User.id == user_id)),
        )
        return bool(res.rowcount)

    async def count_admins(self) -> int:
        res = await self._s.execute(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        return int(res.scalar_one())

    async def set_chat_prefs(self, user_id: uuid.UUID, prefs: dict[str, Any]) -> None:
        await self._s.execute(
            update(User).where(User.id == user_id).values(chat_prefs=prefs)
        )
