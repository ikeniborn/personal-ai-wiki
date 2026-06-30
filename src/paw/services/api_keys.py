from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from paw.api.errors import ProblemError
from paw.audit import actions
from paw.audit.log import record
from paw.db.models import ApiKey
from paw.db.repos.api_keys import ApiKeyRepo
from paw.security.api_keys import (
    API_KEY_SCOPES,
    generate_key,
    hash_secret,
    parse_bearer,
    verify_secret,
)


@dataclass(frozen=True)
class AuthedKey:
    id: uuid.UUID
    user_id: uuid.UUID
    scopes: list[str]


@dataclass(frozen=True)
class IssuedKey:
    id: uuid.UUID
    prefix: str
    token: str
    scopes: list[str]


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = ApiKeyRepo(session)

    async def issue(self, *, user_id: uuid.UUID, scopes: list[str]) -> IssuedKey:
        invalid = sorted(set(scopes) - set(API_KEY_SCOPES))
        if invalid:
            raise ProblemError(
                status=422, title="Invalid scopes", detail=f"unknown scopes: {invalid}"
            )
        prefix, secret, token = generate_key()
        row = await self._repo.create(
            user_id=user_id, prefix=prefix, hash=hash_secret(secret), scopes=list(scopes)
        )
        await record(
            self._s,
            user_id=user_id,
            action=actions.API_KEY_ISSUE,
            target_type="api_key",
            target_id=row.id,
        )
        await self._s.commit()
        return IssuedKey(id=row.id, prefix=prefix, token=token, scopes=list(scopes))

    async def list(self, user_id: uuid.UUID) -> list[ApiKey]:
        return await self._repo.list_for_user(user_id)

    async def revoke(self, *, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
        if not await self._repo.revoke(key_id, user_id):
            raise ProblemError(status=404, title="API key not found")
        await record(
            self._s,
            user_id=user_id,
            action=actions.API_KEY_REVOKE,
            target_type="api_key",
            target_id=key_id,
        )
        await self._s.commit()

    async def authenticate(self, authorization: str | None) -> AuthedKey | None:
        parsed = parse_bearer(authorization)
        if parsed is None:
            return None
        prefix, secret = parsed
        for row in await self._repo.by_prefix(prefix):
            if verify_secret(secret, row.hash) and row.revoked_at is None:
                await self._repo.touch_last_used(row.id)
                await self._s.commit()
                return AuthedKey(id=row.id, user_id=row.user_id, scopes=list(row.scopes))
        return None
