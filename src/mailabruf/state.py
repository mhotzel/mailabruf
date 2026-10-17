"""Local record of already downloaded messages (one SQLite file per account)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    name        TEXT PRIMARY KEY,
    uidvalidity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    folder      TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    message_id  TEXT,
    path        TEXT,
    status      TEXT NOT NULL CHECK (status IN ('saved', 'duplicate')),
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (folder, uidvalidity, uid)
);
CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages (message_id);
"""


@dataclass(frozen=True)
class FolderSummary:
    folder: str
    saved: int
    duplicate: int
    last_fetched: str | None


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def uidvalidity(self, folder: str) -> int | None:
        row = self._db.execute("SELECT uidvalidity FROM folders WHERE name = ?", (folder,)).fetchone()
        return row[0] if row else None

    def set_uidvalidity(self, folder: str, uidvalidity: int) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO folders (name, uidvalidity) VALUES (?, ?) "
                "ON CONFLICT (name) DO UPDATE SET uidvalidity = excluded.uidvalidity",
                (folder, uidvalidity),
            )

    def known_uids(self, folder: str, uidvalidity: int) -> set[int]:
        rows = self._db.execute(
            "SELECT uid FROM messages WHERE folder = ? AND uidvalidity = ?", (folder, uidvalidity)
        )
        return {row[0] for row in rows}

    def has_message_id(self, message_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM messages WHERE message_id = ? LIMIT 1", (message_id,)
        ).fetchone()
        return row is not None

    def record(
        self,
        folder: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        path: str | None,
        status: str,
    ) -> None:
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO messages "
                "(folder, uidvalidity, uid, message_id, path, status, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (folder, uidvalidity, uid, message_id, path, status, fetched_at),
            )

    def summary(self) -> list[FolderSummary]:
        rows = self._db.execute(
            "SELECT folder, SUM(status = 'saved'), SUM(status = 'duplicate'), MAX(fetched_at) "
            "FROM messages GROUP BY folder ORDER BY folder"
        )
        return [FolderSummary(*row) for row in rows]
