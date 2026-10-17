# SPDX-License-Identifier: GPL-2.0-or-later
"""Subject, sender and recipients of a message for the index."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesHeaderParser
from email.utils import getaddresses
from pathlib import Path

_HEADER_READ_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class MailSummary:
    subject: str
    sender: str
    recipients: str


def summarize(data: bytes) -> MailSummary:
    message = BytesHeaderParser(policy=policy.default).parsebytes(data)
    return MailSummary(
        subject=header_text(message, "Subject"),
        sender=address_text(message, "From"),
        recipients=address_text(message, "To", "Cc"),
    )


def read_header_bytes(path: Path) -> bytes:
    """Read a file up to the end of its header block (or a size limit)."""
    data = b""
    with path.open("rb") as fh:
        while len(data) < _HEADER_READ_LIMIT:
            chunk = fh.read(65536)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data or b"\n\n" in data:
                break
    return data


def header_text(message: Message, name: str) -> str:
    """Decoded header value on a single line; empty if missing or unparseable."""
    try:
        value = message.get(name)
    except Exception:  # malformed headers can make the email package raise
        return ""
    return " ".join(str(value).split()) if value is not None else ""


def address_text(message: Message, *names: str) -> str:
    values = []
    for name in names:
        try:
            values.extend(str(v) for v in message.get_all(name, []))
        except Exception:
            continue
    addresses = []
    for display, addr in getaddresses(values):
        display = " ".join(display.split())
        if display and addr:
            addresses.append(f"{display} <{addr}>")
        elif display or addr:
            addresses.append(display or addr)
    return ", ".join(addresses)
