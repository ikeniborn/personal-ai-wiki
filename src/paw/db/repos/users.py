import uuid

from sqlalchemy import select
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
        from sqlalchemy import func

        res = await self._s.execute(select(func.count()).select_from(User))
        return int(res.scalar_one())

    async def list(self) -> list[User]:
        res = await self._s.execute(select(User).order_by(User.created_at))
        return list(res.scalars().all())
