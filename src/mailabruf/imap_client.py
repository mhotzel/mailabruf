# SPDX-License-Identifier: GPL-2.0-only
"""Thin read-only wrapper around imaplib.

Mailboxes are opened with EXAMINE and messages fetched with BODY.PEEK, so flags
such as \\Seen are never changed on the server.
"""

from __future__ import annotations

import imaplib
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime

from mailabruf.config import Account

# UID SEARCH results of large mailboxes can exceed imaplib's default line limit.
imaplib._MAXLINE = max(imaplib._MAXLINE, 10_000_000)

HEADER_CHUNK = 200

_LIST_RE = re.compile(
    r'^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|"(?:[^"\\]|\\.)*")\s+(?P<name>.*)$', re.IGNORECASE
)
_LITERAL_RE = re.compile(r"\{\d+\}$")
_FETCH_START_RE = re.compile(r"^\d+ \(")
_UID_RE = re.compile(r"\bUID (\d+)")
_INTERNALDATE_RE = re.compile(r'INTERNALDATE "([^"]+)"')
_DATE_RE = re.compile(
    r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$"
)
_STATUS_UIDVALIDITY_RE = re.compile(r"UIDVALIDITY (\d+)", re.IGNORECASE)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


class ImapError(Exception):
    """A server command failed; the connection is still usable."""


class ConnectionLost(ImapError):
    """The connection is broken; no further commands are possible."""


@dataclass(frozen=True)
class Folder:
    name: str  # raw name as sent by the server (modified UTF-7)
    delimiter: str | None
    flags: frozenset[str]

    @property
    def selectable(self) -> bool:
        lowered = {f.lower() for f in self.flags}
        return "\\noselect" not in lowered and "\\nonexistent" not in lowered


@dataclass(frozen=True)
class HeaderInfo:
    uid: int
    message_id: str | None
    received: datetime | None


class Connection:
    def __init__(self, account: Account) -> None:
        self._account = account
        self._imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> Connection:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()

    def connect(self) -> None:
        account = self._account
        context = ssl.create_default_context()
        imap = None
        try:
            if account.ssl:
                imap = imaplib.IMAP4_SSL(
                    account.host, account.port, ssl_context=context, timeout=account.timeout
                )
            else:
                imap = imaplib.IMAP4(account.host, account.port, timeout=account.timeout)
                imap.starttls(ssl_context=context)
            if account.password.isascii() and account.username.isascii():
                imap.login(account.username, account.password)
            else:
                credentials = f"\0{account.username}\0{account.password}".encode()
                imap.authenticate("PLAIN", lambda _challenge: credentials)
        except (imaplib.IMAP4.error, OSError) as exc:
            if imap is not None:
                try:
                    imap.shutdown()
                except OSError:
                    pass
            raise ConnectionLost(f"cannot log in to {account.host}:{account.port}: {exc}") from exc
        self._imap = imap

    def logout(self) -> None:
        if self._imap is None:
            return
        try:
            self._imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        finally:
            self._imap = None

    def list_folders(self) -> list[Folder]:
        return parse_list_response(self._call("LIST", self._client.list))

    def examine(self, folder: str) -> tuple[int, int]:
        """Open ``folder`` read-only; return (UIDVALIDITY, number of messages)."""
        data = self._call(f"EXAMINE {folder}", self._client.select, quote(folder), True)
        exists = int(data[0]) if data and data[0] else 0
        _, values = self._client.response("UIDVALIDITY")
        if values and values[0]:
            return int(values[0]), exists
        status = self._call(f"STATUS {folder}", self._client.status, quote(folder), "(UIDVALIDITY)")
        match = _STATUS_UIDVALIDITY_RE.search(_text(status))
        if not match:
            raise ImapError(f"server did not report UIDVALIDITY for {folder}")
        return int(match[1]), exists

    def search_uids(self) -> list[int]:
        data = self._call("UID SEARCH", self._client.uid, "SEARCH", "ALL")
        uids: list[int] = []
        for item in data:
            if item:
                uids.extend(int(x) for x in item.split())
        return sorted(uids)

    def fetch_headers(self, uids: list[int]) -> dict[int, HeaderInfo]:
        """Message-ID and receive date for the given UIDs of the examined folder."""
        wanted = set(uids)
        result: dict[int, HeaderInfo] = {}
        for start in range(0, len(uids), HEADER_CHUNK):
            chunk = ",".join(str(u) for u in uids[start : start + HEADER_CHUNK])
            data = self._call(
                "UID FETCH (headers)",
                self._client.uid,
                "FETCH",
                chunk,
                "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE)])",
            )
            for meta, literal in iter_fetch_items(data):
                match = _UID_RE.search(meta)
                if not match or int(match[1]) not in wanted:
                    continue
                uid = int(match[1])
                date_match = _INTERNALDATE_RE.search(meta)
                internaldate = parse_internaldate(date_match[1]) if date_match else None
                result[uid] = parse_header_info(uid, literal or b"", internaldate)
        return result

    def fetch_message(self, uid: int) -> bytes:
        data = self._call(f"UID FETCH {uid}", self._client.uid, "FETCH", str(uid), "(UID BODY.PEEK[])")
        for meta, literal in iter_fetch_items(data):
            match = _UID_RE.search(meta)
            if literal is not None and (match is None or int(match[1]) == uid):
                return literal
        raise ImapError(f"UID {uid} was not returned by the server (deleted meanwhile?)")

    @property
    def _client(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise ConnectionLost("not connected")
        return self._imap

    def _call(self, what: str, func, *args) -> list:
        try:
            typ, data = func(*args)
        except imaplib.IMAP4.abort as exc:
            raise ConnectionLost(f"{what}: connection lost: {exc}") from exc
        except OSError as exc:
            raise ConnectionLost(f"{what}: {exc}") from exc
        except imaplib.IMAP4.error as exc:
            raise ImapError(f"{what}: {exc}") from exc
        if typ != "OK":
            raise ImapError(f"{what}: {_text(data)}")
        return data


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _text(data: list) -> str:
    parts = []
    for item in data or ():
        if isinstance(item, tuple):
            item = item[0]
        if isinstance(item, bytes):
            item = item.decode("utf-8", "replace")
        if item:
            parts.append(str(item))
    return " ".join(parts) or "no details"


def parse_list_response(data: list) -> list[Folder]:
    folders = []
    for item in data:
        if not item:
            continue
        literal = None
        if isinstance(item, tuple):
            item, literal = item[0], item[1]
        line = item.decode("utf-8", "replace")
        match = _LIST_RE.match(line)
        if not match:
            continue
        name = match["name"].strip()
        if literal is not None and _LITERAL_RE.search(name):
            name = literal.decode("utf-8", "replace")
        else:
            name = _unquote(name)
        delim = None if match["delim"].upper() == "NIL" else _unquote(match["delim"])
        folders.append(Folder(name=name, delimiter=delim, flags=frozenset(match["flags"].split())))
    return folders


def iter_fetch_items(data: list) -> list[tuple[str, bytes | None]]:
    """Group imaplib's FETCH response into (metadata text, literal) per message.

    imaplib returns a tuple (prefix, literal) for each literal, followed by a bytes
    element with the rest of the line (often just b")").
    """
    items: list[list] = []
    for part in data:
        if part is None:
            continue
        if isinstance(part, tuple):
            items.append([part[0].decode("ascii", "replace"), part[1]])
            continue
        text = part.decode("ascii", "replace")
        if items and not _FETCH_START_RE.match(text):
            items[-1][0] += text
        elif text.strip():
            items.append([text, None])
    return [(meta, literal) for meta, literal in items]


def parse_internaldate(value: str) -> datetime | None:
    match = _DATE_RE.match(value)
    if not match:
        return None
    day, mon, year, hh, mm, ss, sign, tzh, tzm = match.groups()
    month = _MONTHS.get(mon.lower())
    if month is None:
        return None
    offset = timedelta(hours=int(tzh), minutes=int(tzm))
    tz = timezone(offset if sign == "+" else -offset)
    try:
        return datetime(int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=tz)
    except ValueError:
        return None


def parse_header_info(uid: int, header: bytes, internaldate: datetime | None) -> HeaderInfo:
    """Extract Message-ID and receive date; falls back to the Date header."""
    message = BytesHeaderParser(policy=policy.compat32).parsebytes(header)
    raw_id = message.get("Message-ID")
    message_id = (" ".join(str(raw_id).split()) or None) if raw_id is not None else None
    received = internaldate
    if received is None and message.get("Date"):
        try:
            received = parsedate_to_datetime(str(message["Date"]))
        except (TypeError, ValueError, IndexError):
            received = None
    return HeaderInfo(uid=uid, message_id=message_id, received=received)
