from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from typing import Iterable

from asset_management.domain.errors import ConfigurationError
from asset_management.time.clock import Clock


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    def __post_init__(self) -> None:
        if self.version <= 0 or not self.name.strip() or not self.sql.strip():
            raise ConfigurationError("migration requires a positive version, name, and SQL")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


class Migrator:
    """Sequential, forward-only, replay-safe SQLite migration runner."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock

    def migrate(self, migrations: Iterable[Migration]) -> tuple[int, ...]:
        ordered = tuple(sorted(migrations, key=lambda item: item.version))
        versions = tuple(item.version for item in ordered)
        if len(versions) != len(set(versions)):
            raise ConfigurationError("migration versions must be unique")
        if versions and versions != tuple(range(versions[0], versions[-1] + 1)):
            raise ConfigurationError("migration versions must be contiguous")

        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migration (
                  version INTEGER PRIMARY KEY,
                  name TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  applied_at_utc TEXT NOT NULL
                )
                """
            )
        applied = {
            int(row[0]): (str(row[1]), str(row[2]))
            for row in self._conn.execute(
                "SELECT version, name, content_hash FROM schema_migration ORDER BY version"
            )
        }
        if applied and (not versions or max(applied) > max(versions)):
            raise ConfigurationError("database schema is newer than this application")
        completed: list[int] = []
        for migration in ordered:
            recorded = applied.get(migration.version)
            if recorded:
                if recorded != (migration.name, migration.content_hash):
                    raise ConfigurationError(
                        f"applied migration {migration.version} hash or name changed"
                    )
                continue
            previous = migration.version - 1
            if migration.version != versions[0] and previous not in applied and previous not in completed:
                raise ConfigurationError(f"migration {migration.version} has an unapplied predecessor")
            script = (
                "BEGIN IMMEDIATE;\n"
                + migration.sql
                + "\nINSERT INTO schema_migration(version, name, content_hash, applied_at_utc) VALUES ("
                + f"{migration.version}, {sql_literal(migration.name)}, {sql_literal(migration.content_hash)}, "
                + f"{sql_literal(self._clock.now_utc().isoformat())});\nCOMMIT;"
            )
            try:
                self._conn.executescript(script)
            except sqlite3.DatabaseError:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise
            completed.append(migration.version)
        return tuple(completed)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
