"""SQLite-backed per-Workspace Skill usage state."""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import threading

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
    SkillUsage,
)


class SkillUsageStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        try:
            self.database_path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=1.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout = 1000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_usage (
                    workspace_key TEXT NOT NULL,
                    skill_key TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace_key, skill_key)
                )
                """
            )
            self._connection.commit()
        try:
            self.database_path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SkillUsageStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def ensure_discovered(
        self,
        workspace_key: str,
        skills: Iterable[SkillDefinition],
        *,
        now: datetime | None = None,
    ) -> dict[str, SkillUsage]:
        timestamp = _as_utc(now or datetime.now(UTC)).isoformat()
        skill_keys = [skill.skill_key for skill in skills]
        with self._lock:
            with self._connection:
                self._connection.executemany(
                    """
                    INSERT INTO skill_usage (workspace_key, skill_key, discovered_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(workspace_key, skill_key) DO NOTHING
                    """,
                    (
                        (workspace_key, skill_key, timestamp)
                        for skill_key in skill_keys
                    ),
                )
            return self.get_many(workspace_key, skill_keys)

    def mark_used(
        self,
        workspace_key: str,
        skills: Iterable[SkillDefinition],
        *,
        now: datetime | None = None,
    ) -> dict[str, SkillUsage]:
        timestamp = _as_utc(now or datetime.now(UTC)).isoformat()
        skill_keys = [skill.skill_key for skill in skills]
        with self._lock:
            with self._connection:
                self._connection.executemany(
                    """
                    INSERT INTO skill_usage (
                        workspace_key, skill_key, discovered_at, last_used_at, use_count
                    ) VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(workspace_key, skill_key) DO UPDATE SET
                        last_used_at = excluded.last_used_at,
                        use_count = skill_usage.use_count + 1
                    """,
                    (
                        (workspace_key, skill_key, timestamp, timestamp)
                        for skill_key in skill_keys
                    ),
                )
            return self.get_many(workspace_key, skill_keys)

    def get_many(
        self,
        workspace_key: str,
        skill_keys: Iterable[str],
    ) -> dict[str, SkillUsage]:
        keys = list(dict.fromkeys(skill_keys))
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT workspace_key, skill_key, discovered_at, last_used_at, use_count
                FROM skill_usage
                WHERE workspace_key = ? AND skill_key IN ({placeholders})
                """,
                [workspace_key, *keys],
            ).fetchall()
        return {row["skill_key"]: _row_to_usage(row) for row in rows}

    def remove(
        self,
        workspace_key: str,
        skill_keys: Iterable[str],
    ) -> int:
        keys = list(dict.fromkeys(skill_keys))
        if not keys:
            return 0
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            with self._connection:
                cursor = self._connection.execute(
                    f"""
                    DELETE FROM skill_usage
                    WHERE workspace_key = ? AND skill_key IN ({placeholders})
                    """,
                    [workspace_key, *keys],
                )
            return cursor.rowcount

    def activate_after_maintenance(
        self,
        workspace_key: str,
        skills: Iterable[SkillDefinition],
        *,
        now: datetime | None = None,
    ) -> dict[str, SkillUsage]:
        timestamp = _as_utc(now or datetime.now(UTC)).isoformat()
        skill_keys = [skill.skill_key for skill in skills]
        with self._lock:
            with self._connection:
                self._connection.executemany(
                    """
                    INSERT INTO skill_usage (
                        workspace_key, skill_key, discovered_at, last_used_at, use_count
                    ) VALUES (?, ?, ?, ?, 0)
                    ON CONFLICT(workspace_key, skill_key) DO UPDATE SET
                        last_used_at = excluded.last_used_at
                    """,
                    (
                        (workspace_key, skill_key, timestamp, timestamp)
                        for skill_key in skill_keys
                    ),
                )
            return self.get_many(workspace_key, skill_keys)


def _row_to_usage(row: sqlite3.Row) -> SkillUsage:
    last_used_at = row["last_used_at"]
    return SkillUsage(
        workspace_key=row["workspace_key"],
        skill_key=row["skill_key"],
        discovered_at=_parse_timestamp(row["discovered_at"]),
        last_used_at=(
            _parse_timestamp(last_used_at) if last_used_at is not None else None
        ),
        use_count=row["use_count"],
    )


def _parse_timestamp(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Skill usage timestamps must be timezone-aware")
    return value.astimezone(UTC)
