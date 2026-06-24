# Storage

## Overview
Storage abstracts persisting raw upload bytes behind the `StorageBackend` Protocol so backends are swappable. The only concrete backend today is `PostgresStorage`, which writes small payloads to the `blobs` table and large ones to PostgreSQL Large Objects, returning an opaque `storage_ref` string. Callers depend on the Protocol, never the concrete class.

## Backends
`StorageBackend` (`storage/base.py`) is a `typing.Protocol`, not a base class — any object with the right async methods satisfies it structurally. This keeps an object-store backend (S3, etc.) a drop-in: depend on the Protocol, never on `PostgresStorage`. See [[architecture#Layered dependencies (no cycles)]].

The Protocol surface:

```python
class StorageBackend(Protocol):
    async def put(self, data: bytes, *, content_type: str | None = None, large: bool = False) -> str: ...
    async def get(self, ref: str) -> bytes: ...
    def open(self, ref: str) -> AsyncIterator[bytes]: ...
    async def delete(self, ref: str) -> None: ...
    async def exists(self, ref: str) -> bool: ...
```

- `put` persists bytes and returns a `storage_ref`.
- `get` / `open` read it back (whole, or chunked).
- `delete` / `exists` remove and probe by ref.

## PostgresStorage
`PostgresStorage` (`storage/postgres.py`) holds an `AsyncSession` and routes by size. Small payloads insert into `blobs (data, content_type)` and return `blob:<uuid>`; `large=True` payloads go to a PostgreSQL Large Object via `lo_from_bytea` and return `lo:<oid>`. See the `blobs` table in [[db#Models and tables]].

- It calls `session.flush()` but **never `commit()`** — the owning service is the single commit boundary. See [[services#The commit-boundary rule]].
- `open()` re-reads via `get()` then yields 1 MiB chunks (`_CHUNK`).
- `delete()` runs `DELETE FROM blobs` or `lo_unlink`; `exists()` probes `blobs` or `pg_largeobject_metadata`.
- Used by ingest to stash uploaded source bytes — see [[ingest#Loaders]].

## storage_ref
A `storage_ref` is the opaque string `put` returns and every other method consumes. Its `kind:ident` shape is parsed with `ref.partition(":")`: `blob:<uuid>` resolves against the `blobs` table, `lo:<oid>` against a Large Object. Callers treat it as opaque — only the backend interprets it.

- `blob:<uuid>` — small payload row in `blobs`.
- `lo:<oid>` — PostgreSQL Large Object oid.
- An unknown prefix raises `ValueError`; a missing `blob:` row raises `KeyError`.
- Persist the ref on the owning row (e.g. a source) inside the service's single commit — see [[services#The commit-boundary rule]] and the API surface in [[api#Dependency helpers (deps.py)]].
