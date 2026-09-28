"""Plain-SQL migration runner. Run with: python -m tagmonitor.db.migrate

Applies db/migrations/NNNN_name.sql in order and records each one in schema_migrations.
- Each file runs in its own transaction, so a failing migration leaves no half-applied state.
- Re-running is a no-op: applied versions are skipped.
- An advisory lock makes concurrent runners (e.g. several containers starting at once) wait
  for each other instead of racing.
- A checksum per file catches edits to already-applied migrations, which would otherwise
  silently leave databases with different schemas. Fix forward with a new file instead.
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg import AsyncConnection

from tagmonitor.config import get_settings

# server/src/tagmonitor/db/migrate.py -> repo root. The Docker image mirrors this layout.
MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "db" / "migrations"

# Arbitrary constant that identifies "the migration lock" among advisory locks.
MIGRATION_LOCK_KEY = 7_311_901

FILENAME_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str  # "0001"
    filename: str  # "0001_init.sql"
    sql: bytes
    checksum: str


def load_migrations(directory: Path) -> list[Migration]:
    """Read migration files in version order, rejecting bad names and duplicate versions."""
    migrations: list[Migration] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_PATTERN.match(path.name)
        if match is None:
            raise MigrationError(f"{path.name}: expected a name like 0001_description.sql")
        version = match.group(1)
        if version in seen:
            raise MigrationError(f"two migration files share version {version}")
        seen.add(version)
        sql = path.read_bytes()
        migrations.append(
            Migration(version, path.name, sql, hashlib.sha256(sql).hexdigest()),
        )
    return migrations


async def migrate(database_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations and return the filenames applied by this call."""
    migrations = load_migrations(directory)
    applied_now: list[str] = []
    async with await AsyncConnection.connect(database_url, autocommit=True) as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text PRIMARY KEY,
                    filename   text NOT NULL,
                    checksum   text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cursor = await conn.execute("SELECT version, checksum FROM schema_migrations")
            applied = {version: checksum for version, checksum in await cursor.fetchall()}

            on_disk = {m.version for m in migrations}
            missing = sorted(set(applied) - on_disk)
            if missing:
                raise MigrationError(f"applied migrations are missing from disk: {missing}")

            for migration in migrations:
                if migration.version in applied:
                    if applied[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"{migration.filename} changed after it was applied; "
                            "never edit an applied migration, add a new one instead"
                        )
                    continue
                async with conn.transaction():
                    # The file is trusted SQL from the repo, not user input. It is passed as
                    # bytes because psycopg only runs multi-statement strings without params.
                    await conn.execute(migration.sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, filename, checksum) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.filename, migration.checksum),
                    )
                applied_now.append(migration.filename)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    return applied_now


def main() -> None:
    applied = asyncio.run(migrate(get_settings().database_url))
    if applied:
        for filename in applied:
            print(f"applied {filename}")
    else:
        print("database is up to date")


if __name__ == "__main__":
    main()
