import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from paw.db.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta or {},
        )
    )
    await session.flush()
