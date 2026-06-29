"""Backup -> restore roundtrip (acceptance criterion #4).

Proves `pg_dump --format=custom` + `pg_restore --clean --if-exists` reproduces the
corpus. Needs the Docker daemon (testcontainers Postgres) AND the libpq client tools
(`pg_dump`/`pg_restore`) on PATH; skips cleanly when either is missing so the unit
layer / Docker-less environments stay green. The real run happens on a Docker host / CI.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("psycopg2")
import psycopg2  # noqa: E402

if (
    shutil.which("pg_dump") is None
    or shutil.which("pg_restore") is None
    or subprocess.run(["pg_dump", "--version"], capture_output=True).returncode != 0
):
    pytest.skip("pg_dump/pg_restore not available", allow_module_level=True)

try:
    from testcontainers.postgres import PostgresContainer
except Exception:  # pragma: no cover - import guard
    pytest.skip("testcontainers not available", allow_module_level=True)


def _libpq_env(container: PostgresContainer) -> dict[str, str]:
    return {
        "PGHOST": container.get_container_host_ip(),
        "PGPORT": str(container.get_exposed_port(5432)),
        "PGUSER": container.username,
        "PGPASSWORD": container.password,
        "PGDATABASE": container.dbname,
    }


def _dsn(container: PostgresContainer) -> str:
    return (
        f"host={container.get_container_host_ip()} "
        f"port={container.get_exposed_port(5432)} "
        f"user={container.username} password={container.password} "
        f"dbname={container.dbname}"
    )


def test_backup_restore_roundtrip() -> None:
    try:
        ctx = PostgresContainer("pgvector/pgvector:pg16")
        ctx.start()
    except Exception as exc:  # Docker daemon not reachable
        pytest.skip(f"Docker unavailable: {exc}")

    try:
        env = _libpq_env(ctx)
        dsn = _dsn(ctx)

        # 1. Seed a known corpus.
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE corpus (id int primary key, body text)")
            cur.execute(
                "INSERT INTO corpus (id, body) VALUES (1,'alpha'),(2,'beta'),(3,'gamma')"
            )
            conn.commit()

        # 2. pg_dump --format=custom to a temp file.
        with tempfile.TemporaryDirectory() as tmp:
            dump = str(Path(tmp) / "paw.dump")
            subprocess.run(
                ["pg_dump", "--format=custom", "--file", dump], check=True, env=env
            )
            assert Path(dump).stat().st_size > 0

            # 3. Destroy the data.
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM corpus")
                conn.commit()
                cur.execute("SELECT count(*) FROM corpus")
                assert cur.fetchone()[0] == 0

            # 4. Restore from the dump.
            subprocess.run(
                ["pg_restore", "--clean", "--if-exists", "--no-owner",
                 "--no-privileges", "--dbname", ctx.dbname, dump],
                check=True, env=env,
            )

        # 5. Corpus is reproduced exactly.
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM corpus")
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT body FROM corpus ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == ["alpha", "beta", "gamma"]
    finally:
        ctx.stop()
