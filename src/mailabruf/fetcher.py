"""Download new messages of one account into .eml files."""

from __future__ import annotations

import fcntl
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mailabruf import mailinfo, storage
from mailabruf.archive import ArchiveResult, update_archive
from mailabruf.config import Account
from mailabruf.imap_client import Connection, ConnectionLost, Folder, ImapError
from mailabruf.output import Output
from mailabruf.state import State


@dataclass
class FolderResult:
    name: str
    new: int = 0
    duplicate: int = 0
    errors: int = 0


@dataclass
class AccountResult:
    name: str
    folders: list[FolderResult] = field(default_factory=list)
    error: str | None = None
    locked: bool = False
    seconds: float = 0.0
    archive: ArchiveResult | None = None

    @property
    def new(self) -> int:
        return sum(f.new for f in self.folders)

    @property
    def duplicate(self) -> int:
        return sum(f.duplicate for f in self.folders)

    @property
    def errors(self) -> int:
        return sum(f.errors for f in self.folders)

    @property
    def failed(self) -> bool:
        pdf_failed = self.archive.pdf_failed if self.archive else 0
        return self.error is not None or self.errors > 0 or pdf_failed > 0


def format_counts(new: int, duplicate: int, errors: int) -> str:
    return f"{new} new, {duplicate} duplicate, {errors} errors"


@contextmanager
def account_lock(state_dir: Path, account: Account) -> Iterator[bool]:
    """Exclusive per-account lock; yields False if another run holds it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / f"{account.name}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def fetch_account(
    account: Account,
    state_dir: Path,
    out: Output,
    *,
    dry_run: bool = False,
    connection_factory: Callable[[Account], Connection] = Connection,
) -> AccountResult:
    """Download new messages, then create missing PDFs and rewrite index.html."""
    result = AccountResult(account.name)
    started = time.monotonic()
    with account_lock(state_dir, account) as acquired:
        if not acquired:
            result.locked = True
            return result
        try:
            with State(state_dir / f"{account.name}.sqlite3") as state:
                try:
                    with connection_factory(account) as conn:
                        seen_ids: set[str] = set()
                        for folder in conn.list_folders():
                            if not folder.selectable or _excluded(account, folder):
                                continue
                            result.folders.append(
                                _fetch_folder(conn, state, account, folder, out, dry_run, seen_ids)
                            )
                except ImapError as exc:
                    result.error = str(exc)
                # Runs without a connection, also after IMAP errors.
                if not dry_run:
                    result.archive = update_archive(account, state, out)
        except (OSError, sqlite3.Error) as exc:
            result.error = f"local error: {exc}"
        finally:
            result.seconds = time.monotonic() - started
    return result


def rebuild_account(
    account: Account, state_dir: Path, out: Output, *, force: bool = False
) -> AccountResult:
    """Offline: retry failed PDFs (or re-render all with ``force``) and rewrite index.html."""
    result = AccountResult(account.name)
    started = time.monotonic()
    with account_lock(state_dir, account) as acquired:
        if not acquired:
            result.locked = True
            return result
        try:
            with State(state_dir / f"{account.name}.sqlite3") as state:
                result.archive = update_archive(
                    account, state, out, retry_failed=True, force=force
                )
        except (OSError, sqlite3.Error) as exc:
            result.error = f"local error: {exc}"
        finally:
            result.seconds = time.monotonic() - started
    return result


def _excluded(account: Account, folder: Folder) -> bool:
    excluded = set(account.exclude_folders)
    return folder.name in excluded or storage.display_name(folder.name) in excluded


def _fetch_folder(
    conn: Connection,
    state: State,
    account: Account,
    folder: Folder,
    out: Output,
    dry_run: bool,
    seen_ids: set[str],
) -> FolderResult:
    display = storage.display_name(folder.name)
    prefix = f"[{account.name}] {display}"
    result = FolderResult(display)

    try:
        uidvalidity, exists = conn.examine(folder.name)
        stored = state.uidvalidity(folder.name)
        if stored is not None and stored != uidvalidity:
            out.warning(
                f"{prefix}: UIDVALIDITY changed ({stored} -> {uidvalidity}); "
                "messages are re-checked by Message-ID"
            )
        if stored != uidvalidity and not dry_run:
            state.set_uidvalidity(folder.name, uidvalidity)

        new_uids: list[int] = []
        if exists:
            known = state.known_uids(folder.name, uidvalidity)
            new_uids = [uid for uid in conn.search_uids() if uid not in known]
        headers = conn.fetch_headers(new_uids) if new_uids else {}
    except ConnectionLost:
        raise
    except ImapError as exc:
        out.error(f"{prefix}: {exc}")
        result.errors += 1
        return result

    target_dir = account.target_dir / storage.folder_path(folder.name, folder.delimiter)
    for uid in new_uids:
        info = headers.get(uid)
        if info is None:
            out.detail(f"{prefix}: UID {uid} disappeared from the server, skipped")
            continue
        message_id = info.message_id

        if message_id and (message_id in seen_ids or state.has_message_id(message_id)):
            result.duplicate += 1
            out.detail(f"{prefix}: UID {uid} is a duplicate of {message_id}")
            if not dry_run:
                state.record(folder.name, uidvalidity, uid, message_id, None, "duplicate")
            continue

        if dry_run:
            result.new += 1
            if message_id:
                seen_ids.add(message_id)
            continue

        try:
            data = conn.fetch_message(uid)
        except ConnectionLost:
            raise
        except ImapError as exc:
            out.error(f"{prefix}: UID {uid}: {exc}")
            result.errors += 1
            continue

        path = storage.unique_path(target_dir / storage.message_filename(info.received, uid))
        storage.write_atomic(path, data)
        state.record(
            folder.name, uidvalidity, uid, message_id, str(path), "saved",
            received=info.received, summary=mailinfo.summarize(data),
        )
        result.new += 1
        out.detail(f"{prefix}: saved {path}")

    line = f"{prefix}: {format_counts(result.new, result.duplicate, result.errors)}"
    if result.new or result.duplicate or result.errors:
        out.info(line)
    else:
        out.detail(line)
    return result
