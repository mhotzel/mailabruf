"""Local record of already downloaded messages (one SQLite file per account)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mailabruf.mailinfo import MailSummary

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

# Columns added after the first release; existing databases are migrated on open.
_ADDED_COLUMNS = {
    "received": "TEXT",
    "subject": "TEXT",
    "sender": "TEXT",
    "recipients": "TEXT",
    "summarized": "INTEGER NOT NULL DEFAULT 0",
    "pdf_status": "TEXT",
    "pdf_error": "TEXT",
}

MessageKey = tuple[str, int, int]


@dataclass(frozen=True)
class FolderSummary:
    folder: str
    saved: int
    duplicate: int
    last_fetched: str | None


@dataclass(frozen=True)
class SavedMessage:
    folder: str
    uidvalidity: int
    uid: int
    path: str
    received: str | None
    subject: str | None
    sender: str | None
    recipients: str | None
    pdf_status: str | None

    @property
    def key(self) -> MessageKey:
        return (self.folder, self.uidvalidity, self.uid)


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(messages)")}
        with self._db:
            for column, definition in _ADDED_COLUMNS.items():
                if column not in existing:
                    self._db.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")

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
        received: datetime | None = None,
        summary: MailSummary | None = None,
    ) -> None:
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO messages "
                "(folder, uidvalidity, uid, message_id, path, status, fetched_at, "
                "received, subject, sender, recipients, summarized) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    folder, uidvalidity, uid, message_id, path, status, fetched_at,
                    received.isoformat() if received else None,
                    summary.subject if summary else None,
                    summary.sender if summary else None,
                    summary.recipients if summary else None,
                    1 if summary else 0,
                ),
            )

    def unsummarized(self) -> list[tuple[MessageKey, str]]:
        """Saved messages without subject/sender/recipients (downloaded by older versions)."""
        rows = self._db.execute(
            "SELECT folder, uidvalidity, uid, path FROM messages "
            "WHERE status = 'saved' AND summarized = 0"
        )
        return [((folder, uidvalidity, uid), path) for folder, uidvalidity, uid, path in rows]

    def set_summary(
        self, key: MessageKey, received: datetime | None, summary: MailSummary
    ) -> None:
        with self._db:
            self._db.execute(
                "UPDATE messages SET received = COALESCE(received, ?), subject = ?, sender = ?, "
                "recipients = ?, summarized = 1 WHERE folder = ? AND uidvalidity = ? AND uid = ?",
                (
                    received.isoformat() if received else None,
                    summary.subject, summary.sender, summary.recipients, *key,
                ),
            )

    def saved_messages(self) -> list[SavedMessage]:
        rows = self._db.execute(
            "SELECT folder, uidvalidity, uid, path, received, subject, sender, recipients, "
            "pdf_status FROM messages WHERE status = 'saved' AND path IS NOT NULL"
        )
        return [SavedMessage(*row) for row in rows]

    def set_pdf_status(self, key: MessageKey, status: str, error: str | None = None) -> None:
        with self._db:
            self._db.execute(
                "UPDATE messages SET pdf_status = ?, pdf_error = ? "
                "WHERE folder = ? AND uidvalidity = ? AND uid = ?",
                (status, error, *key),
            )

    def summary(self) -> list[FolderSummary]:
        rows = self._db.execute(
            "SELECT folder, SUM(status = 'saved'), SUM(status = 'duplicate'), MAX(fetched_at) "
            "FROM messages GROUP BY folder ORDER BY folder"
        )
        return [FolderSummary(*row) for row in rows]
