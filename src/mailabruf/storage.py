# SPDX-License-Identifier: GPL-2.0-or-later
"""Local file layout: folder paths, file names and atomic writes."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from mailabruf import utf7

_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f/\\]")


def display_name(folder: str) -> str:
    """Human-readable folder name (modified UTF-7 decoded where possible)."""
    try:
        return utf7.decode(folder)
    except ValueError:
        return folder


def sanitize_component(part: str) -> str:
    part = _UNSAFE_CHARS.sub("_", part).strip()
    if part in ("", ".", ".."):
        return part.replace(".", "_") or "_"
    return part


def folder_path(folder: str, delimiter: str | None) -> Path:
    """Relative directory for an IMAP folder; hierarchy levels become subdirectories."""
    name = display_name(folder)
    parts = name.split(delimiter) if delimiter else [name]
    return Path(*(sanitize_component(p) for p in parts))


def message_filename(received: datetime | None, uid: int) -> str:
    if received is None:
        return f"unknown-date_{uid}.eml"
    return f"{received.astimezone():%Y-%m-%d_%H%M%S}_{uid}.eml"


def received_from_filename(name: str) -> datetime | None:
    """Inverse of message_filename (local time); None for unknown-date files."""
    try:
        return datetime.strptime(name[:17], "%Y-%m-%d_%H%M%S").astimezone()
    except ValueError:
        return None


def unique_path(path: Path) -> Path:
    """Return ``path`` or, if it exists, ``<stem>-1<suffix>``, ``<stem>-2<suffix>``, ..."""
    candidate = path
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file so that no partial files are left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
