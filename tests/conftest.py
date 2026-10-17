import io
from pathlib import Path

import pytest

from mailabruf.config import Account
from mailabruf.imap_client import ConnectionLost, Folder, ImapError, parse_header_info
from mailabruf.output import Output


def make_message(subject: str, message_id: str | None = None, body: str = "Hello") -> bytes:
    headers = [
        "From: alice@example.org",
        "To: bob@example.org",
        f"Subject: {subject}",
        "Date: Fri, 16 Oct 2026 10:30:00 +0200",
    ]
    if message_id:
        headers.append(f"Message-ID: {message_id}")
    return ("\r\n".join(headers) + "\r\n\r\n" + body + "\r\n").encode()


class FakeServer:
    """In-memory mailbox store used instead of a real IMAP server."""

    def __init__(self) -> None:
        self.folders: dict[str, dict] = {}
        self.fetched: list[tuple[str, int]] = []
        self.fail_examine: set[str] = set()
        self.lose_connection_on: set[str] = set()

    def add_folder(self, name: str, uidvalidity: int = 1, flags: tuple[str, ...] = ()) -> None:
        self.folders[name] = {"uidvalidity": uidvalidity, "messages": {}, "flags": frozenset(flags)}

    def add_message(self, folder: str, uid: int, data: bytes) -> None:
        self.folders[folder]["messages"][uid] = data

    def connect(self, account: Account) -> "FakeConnection":
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.selected: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def list_folders(self):
        return [Folder(name, "/", f["flags"]) for name, f in self.server.folders.items()]

    def examine(self, folder):
        if folder in self.server.lose_connection_on:
            raise ConnectionLost("connection reset")
        if folder in self.server.fail_examine:
            raise ImapError("NO mailbox unavailable")
        self.selected = folder
        f = self.server.folders[folder]
        return f["uidvalidity"], len(f["messages"])

    def search_uids(self):
        return sorted(self.server.folders[self.selected]["messages"])

    def fetch_headers(self, uids):
        messages = self.server.folders[self.selected]["messages"]
        return {uid: parse_header_info(uid, messages[uid], None) for uid in uids if uid in messages}

    def fetch_message(self, uid):
        self.server.fetched.append((self.selected, uid))
        return self.server.folders[self.selected]["messages"][uid]


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def account(tmp_path: Path) -> Account:
    return Account(
        name="test",
        host="imap.example.org",
        username="user",
        password="secret",
        target_dir=tmp_path / "mail",
    )


@pytest.fixture
def out() -> Output:
    return Output(verbose=True, stdout=io.StringIO(), stderr=io.StringIO())
