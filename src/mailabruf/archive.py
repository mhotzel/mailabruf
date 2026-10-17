# SPDX-License-Identifier: GPL-2.0-or-later
"""Post-processing of an account's archive: PDF copies and index.html."""

from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mailabruf import mailinfo, storage
from mailabruf.config import Account
from mailabruf.index import IndexEntry, write_index
from mailabruf.output import Output
from mailabruf.state import SavedMessage, State

PROGRESS_STEP = 50


@dataclass
class ArchiveResult:
    pdf_created: int = 0
    pdf_failed: int = 0
    pdf_skipped: int = 0
    # Why no PDFs could be created at all (WeasyPrint or its libraries missing).
    pdf_unavailable: str | None = None
    index: Path | None = None


def pdf_unavailable() -> str | None:
    """Import WeasyPrint on first use; return why that failed, or None."""
    # WeasyPrint prints a banner to stdout when a library is missing.
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            import mailabruf.pdf  # noqa: F401
        except (ImportError, OSError) as exc:
            reason = f"WeasyPrint cannot be loaded ({type(exc).__name__}: {exc})."
            if sys.platform == "darwin":
                return (
                    reason + " Install Pango with 'brew install pango' and set "
                    "DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib, see README."
                )
            return reason + " Install Pango, see README."
    return None


def render_pdf(data: bytes, target: Path) -> None:
    from mailabruf.pdf import render_pdf as render

    render(data, target)


def update_archive(
    account: Account,
    state: State,
    out: Output,
    *,
    retry_failed: bool = False,
    force: bool = False,
) -> ArchiveResult:
    """Create missing PDFs and rewrite index.html.

    PDFs that failed before are only retried with ``retry_failed``; ``force``
    re-renders every PDF.
    """
    result = ArchiveResult()
    _backfill_summaries(state, out)
    messages = [m for m in state.saved_messages() if Path(m.path).exists()]

    pending = [m for m in messages if _needs_pdf(m, retry_failed, force)]
    if pending and (reason := pdf_unavailable()):
        # Leave the PDF status untouched, so the next run creates them.
        result.pdf_unavailable = reason
        result.pdf_skipped = len(pending)
        pending = []
    for number, message in enumerate(pending, 1):
        eml = Path(message.path)
        try:
            render_pdf(eml.read_bytes(), eml.with_suffix(".pdf"))
        except Exception as exc:  # WeasyPrint can fail in many ways on odd mails
            state.set_pdf_status(message.key, "error", f"{type(exc).__name__}: {exc}")
            result.pdf_failed += 1
            out.warning(f"[{account.name}] PDF for {eml} failed: {type(exc).__name__}: {exc}")
        else:
            state.set_pdf_status(message.key, "ok")
            result.pdf_created += 1
            out.detail(f"[{account.name}] created {eml.with_suffix('.pdf')}")
        if len(pending) > PROGRESS_STEP and number % PROGRESS_STEP == 0:
            out.info(f"[{account.name}] PDF {number}/{len(pending)} …")

    entries = [_index_entry(account, m) for m in state.saved_messages() if Path(m.path).exists()]
    result.index = write_index(account.name, account.target_dir, entries)
    return result


def _needs_pdf(message: SavedMessage, retry_failed: bool, force: bool) -> bool:
    if force or message.pdf_status is None:
        return True
    if message.pdf_status == "error":
        return retry_failed
    return not Path(message.path).with_suffix(".pdf").exists()


def _backfill_summaries(state: State, out: Output) -> None:
    """Fill index metadata for messages downloaded before it was recorded."""
    for key, path in state.unsummarized():
        eml = Path(path)
        try:
            summary = mailinfo.summarize(mailinfo.read_header_bytes(eml))
        except OSError as exc:
            out.warning(f"cannot read {eml}: {exc.strerror}")
            continue
        state.set_summary(key, storage.received_from_filename(eml.name), summary)


def _index_entry(account: Account, message: SavedMessage) -> IndexEntry:
    eml = Path(message.path)
    pdf = eml.with_suffix(".pdf")
    try:
        folder = eml.parent.relative_to(account.target_dir).as_posix()
    except ValueError:
        folder = storage.display_name(message.folder)
    return IndexEntry(
        received=datetime.fromisoformat(message.received) if message.received else None,
        folder=folder,
        subject=message.subject or "",
        sender=message.sender or "",
        recipients=message.recipients or "",
        eml=eml,
        pdf=pdf if message.pdf_status == "ok" and pdf.exists() else None,
    )
