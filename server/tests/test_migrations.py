import asyncio
import shutil
from pathlib import Path

import pytest
from psycopg import AsyncConnection

from tagmonitor.db.migrate import MIGRATIONS_DIR, MigrationError, load_migrations, migrate


async def table_names(url: str) -> set[str]:
    async with await AsyncConnection.connect(url) as conn:
        cursor = await conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {row[0] for row in await cursor.fetchall()}


def copy_migrations(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, directory)
    return directory


async def test_applies_every_migration_to_an_empty_database(empty_database_url: str) -> None:
    applied = await migrate(empty_database_url)

    assert applied == [m.filename for m in load_migrations(MIGRATIONS_DIR)]
    assert {"users", "sites", "jobs", "check_runs", "alerts", "schema_migrations"} <= (
        await table_names(empty_database_url)
    )


async def test_second_run_is_a_no_op(empty_database_url: str) -> None:
    await migrate(empty_database_url)
    assert await migrate(empty_database_url) == []


async def test_concurrent_runners_apply_each_migration_once(empty_database_url: str) -> None:
    first, second = await asyncio.gather(migrate(empty_database_url), migrate(empty_database_url))
    # The advisory lock serializes the two runners: one applies everything, the other nothing.
    assert sorted([first, second], key=len)[0] == []
    assert len(first) + len(second) == len(load_migrations(MIGRATIONS_DIR))


async def test_rejects_an_edited_migration(empty_database_url: str, tmp_path: Path) -> None:
    directory = copy_migrations(tmp_path)
    await migrate(empty_database_url, directory)
    first = sorted(directory.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- sneaky edit\n")

    with pytest.raises(MigrationError, match="changed after it was applied"):
        await migrate(empty_database_url, directory)


async def test_failed_migration_is_rolled_back(empty_database_url: str, tmp_path: Path) -> None:
    directory = copy_migrations(tmp_path)
    (directory / "9999_broken.sql").write_text(
        "CREATE TABLE half_done (id int);\nSELECT this_function_does_not_exist();\n"
    )

    with pytest.raises(Exception, match="this_function_does_not_exist"):
        await migrate(empty_database_url, directory)

    # Everything before the broken file stuck; the broken file left nothing behind.
    assert "half_done" not in await table_names(empty_database_url)
    async with await AsyncConnection.connect(empty_database_url) as conn:
        cursor = await conn.execute("SELECT version FROM schema_migrations")
        versions = {row[0] for row in await cursor.fetchall()}
    assert "9999" not in versions
    assert "0001" in versions


def test_rejects_badly_named_files(tmp_path: Path) -> None:
    (tmp_path / "add_users.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="expected a name like"):
        load_migrations(tmp_path)


def test_rejects_duplicate_versions(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="share version 0001"):
        load_migrations(tmp_path)
