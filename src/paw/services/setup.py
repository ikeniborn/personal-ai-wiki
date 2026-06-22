from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.db.models import User
from paw.db.repos.settings import SettingsRepo
from paw.db.repos.users import UserRepo
from paw.security.passwords import hash_password


class SetupService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._users = UserRepo(session)
        self._settings = SettingsRepo(session)

    async def needs_setup(self) -> bool:
        return (await self._users.count()) == 0

    async def complete(self, *, email: str, password: str) -> User:
        if not await self.needs_setup():
            raise ProblemError(status=409, title="Already initialized")
        admin = await self._users.create(email=email, pw_hash=hash_password(password), role="admin")
        await self._settings.upsert({})  # seed empty singleton
        await self._s.commit()
        return admin
