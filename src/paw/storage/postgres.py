from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CHUNK = 1024 * 1024


class PostgresStorage:
    """Small payloads -> blobs.bytea (ref 'blob:<uuid>').
    Large payloads -> PostgreSQL Large Object (ref 'lo:<oid>')."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def put(self, data: bytes, *, content_type: str | None = None,
                  large: bool = False) -> str:
        if large:
            row = await self._s.execute(text("SELECT lo_from_bytea(0, :d) AS oid"), {"d": data})
            oid = row.scalar_one()
            await self._s.commit()
            return f"lo:{oid}"
        row = await self._s.execute(
            text("INSERT INTO blobs (data, content_type) VALUES (:d, :ct) RETURNING id"),
            {"d": data, "ct": content_type},
        )
        bid = row.scalar_one()
        await self._s.commit()
        return f"blob:{bid}"

    async def get(self, ref: str) -> bytes:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            row = await self._s.execute(
                text("SELECT data FROM blobs WHERE id = :id"), {"id": ident}
            )
            val = row.scalar_one_or_none()
            if val is None:
                raise KeyError(ref)
            return bytes(val)
        if kind == "lo":
            row = await self._s.execute(text("SELECT lo_get(:oid)"), {"oid": int(ident)})
            return bytes(row.scalar_one())
        raise ValueError(f"unknown ref: {ref}")

    async def open(self, ref: str) -> AsyncIterator[bytes]:
        data = await self.get(ref)
        for i in range(0, len(data), _CHUNK):
            yield data[i : i + _CHUNK]

    async def delete(self, ref: str) -> None:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            await self._s.execute(text("DELETE FROM blobs WHERE id = :id"), {"id": ident})
        elif kind == "lo":
            await self._s.execute(text("SELECT lo_unlink(:oid)"), {"oid": int(ident)})
        else:
            raise ValueError(f"unknown ref: {ref}")
        await self._s.commit()

    async def exists(self, ref: str) -> bool:
        kind, _, ident = ref.partition(":")
        if kind == "blob":
            row = await self._s.execute(
                text("SELECT 1 FROM blobs WHERE id = :id"), {"id": ident}
            )
            return row.scalar_one_or_none() is not None
        if kind == "lo":
            row = await self._s.execute(
                text("SELECT 1 FROM pg_largeobject_metadata WHERE oid = :oid"), {"oid": int(ident)}
            )
            return row.scalar_one_or_none() is not None
        raise ValueError(f"unknown ref: {ref}")
