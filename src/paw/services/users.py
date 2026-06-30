import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.audit import actions
from paw.audit.log import record
from paw.db.models import USER_ROLES, User
from paw.db.repos.users import UserRepo
from paw.security.passwords import WeakPassword, hash_password, validate_password_strength


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = UserRepo(session)

    async def list(self) -> list[User]:
        return await self._repo.list()

    async def create(
        self, *, email: str, password: str, role: str, actor_id: uuid.UUID | None = None
    ) -> User:
        try:
            validate_password_strength(password)
        except WeakPassword as e:
            raise ProblemError(status=422, title="Weak password", detail=str(e)) from e
        u = await self._repo.create(email=email, pw_hash=hash_password(password), role=role)
        await record(
            self._s,
            user_id=actor_id,
            action=actions.USER_CREATE,
            target_type="user",
            target_id=u.id,
        )
        await self._s.commit()
        return u

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._repo.get(user_id)

    async def set_role(
        self, *, user_id: uuid.UUID, role: str, actor_id: uuid.UUID | None = None
    ) -> User:
        if role not in USER_ROLES:
            raise ProblemError(
                status=422, title="Invalid role", detail=f"role must be one of {USER_ROLES}"
            )
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        if (
            target.role == "admin"
            and role != "admin"
            and await self._repo.count_admins() <= 1
        ):
            raise ProblemError(
                status=409, title="Last admin", detail="cannot demote the last admin"
            )
        await self._repo.set_role(user_id, role)
        await record(
            self._s,
            user_id=actor_id,
            action=actions.USER_ROLE_CHANGE,
            target_type="user",
            target_id=user_id,
        )
        await self._s.commit()
        refreshed = await self._repo.get(user_id)
        assert refreshed is not None
        return refreshed

    async def delete(self, *, user_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> None:
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        if target.role == "admin" and await self._repo.count_admins() <= 1:
            raise ProblemError(
                status=409, title="Last admin", detail="cannot delete the last admin"
            )
        await record(
            self._s,
            user_id=actor_id,
            action=actions.USER_DELETE,
            target_type="user",
            target_id=user_id,
        )
        await self._repo.delete(user_id)
        await self._s.commit()

    async def set_ui_language(self, *, user_id: uuid.UUID, lang: str) -> None:
        if lang not in ("en", "ru"):
            raise ProblemError(
                status=422,
                title="Invalid language",
                detail="lang must be one of ('en', 'ru')",
            )
        target = await self._repo.get(user_id)
        if target is None:
            raise ProblemError(status=404, title="User not found")
        prefs = dict(target.chat_prefs or {})
        prefs["ui_language"] = lang
        await self._repo.set_chat_prefs(user_id, prefs)
        await self._s.commit()
