from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import User
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = UserRepo(session)

    async def list(self) -> list[User]:
        return await self._repo.list()

    async def create(self, *, email: str, password: str, role: str) -> User:
        u = await self._repo.create(email=email, pw_hash=hash_password(password), role=role)
        await self._s.commit()
        return u
