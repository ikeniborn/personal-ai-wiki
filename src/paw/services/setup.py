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

    async def complete(
        self,
        *,
        email: str,
        password: str,
        base_url: str,
        api_key: str,
        chat_model: str,
        embedding_model: str,
        embedding_dim: int,
        vision_model: str | None = None,
    ) -> User:
        if not await self.needs_setup():
            raise ProblemError(status=409, title="Already initialized")
        admin = await self._users.create(email=email, pw_hash=hash_password(password), role="admin")
        await self._settings.upsert({})
        from paw.db.managed import ensure_embedding_column
        from paw.services.provider_settings import ProviderSettingsService

        psvc = ProviderSettingsService(self._s)
        await psvc.set_provider(
            base_url=base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            api_key=api_key,
            vision_model=vision_model,
        )
        await ensure_embedding_column(self._s, embedding_dim)
        await self._s.commit()
        return admin
