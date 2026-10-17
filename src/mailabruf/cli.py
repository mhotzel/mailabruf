# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from mailabruf import __version__, storage
from mailabruf.config import Config, ConfigError, default_config_dir, load_config
from mailabruf.fetcher import AccountResult, fetch_account, format_counts, rebuild_account
from mailabruf.imap_client import Connection, ImapError
from mailabruf.output import Output
from mailabruf.state import State

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailabruf",
        description="Download all messages of IMAP mailboxes into local .eml files.",
        epilog="Exit codes: 0 = ok, 1 = some messages, folders or accounts failed, "
        "2 = configuration or usage error.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config-dir",
        type=Path,
        metavar="DIR",
        help=f"configuration directory (default: {default_config_dir()})",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="print errors only")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="print every message")

    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    fetch = commands.add_parser("fetch", help="download new messages")
    fetch.add_argument("accounts", nargs="*", metavar="ACCOUNT", help="default: all enabled accounts")
    fetch.add_argument("--dry-run", action="store_true", help="only count new messages, save nothing")
    fetch.set_defaults(func=cmd_fetch)

    folders = commands.add_parser("folders", help="list the folders on the server")
    folders.add_argument("account", metavar="ACCOUNT")
    folders.set_defaults(func=cmd_folders)

    render = commands.add_parser(
        "render", help="offline: create missing or failed PDFs and rewrite index.html"
    )
    render.add_argument("accounts", nargs="*", metavar="ACCOUNT", help="default: all enabled accounts")
    render.add_argument("--force", action="store_true", help="re-create all PDFs")
    render.set_defaults(func=cmd_render)

    status = commands.add_parser("status", help="show what has been downloaded so far")
    status.add_argument("accounts", nargs="*", metavar="ACCOUNT", help="default: all enabled accounts")
    status.set_defaults(func=cmd_status)

    check = commands.add_parser("check", help="validate the configuration and test the login")
    check.add_argument("accounts", nargs="*", metavar="ACCOUNT", help="default: all enabled accounts")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Output(quiet=args.quiet, verbose=args.verbose)
    try:
        config = load_config(args.config_dir)
        for warning in config.warnings:
            out.warning(warning)
        return args.func(config, args, out)
    except ConfigError as exc:
        out.error(str(exc))
        return EXIT_CONFIG
    except KeyboardInterrupt:
        out.error("interrupted")
        return EXIT_INTERRUPTED


def cmd_fetch(config: Config, args: argparse.Namespace, out: Output) -> int:
    accounts = config.select(args.accounts)
    if not accounts:
        out.warning("no enabled accounts")
        return EXIT_OK
    failed = False
    suffix = " [dry run]" if args.dry_run else ""
    for account in accounts:
        result = fetch_account(account, config.state_dir, out, dry_run=args.dry_run)
        if result.locked:
            out.warning(f"[{account.name}] skipped: another mailabruf run is active for this account")
            continue
        if result.error:
            out.error(f"[{account.name}] {result.error}")
            failed = True
            if not result.folders:
                continue
        failed = failed or result.failed
        out.info(
            f"[{account.name}] total: "
            f"{format_counts(result.new, result.duplicate, result.errors)} "
            f"in {len(result.folders)} folders ({result.seconds:.1f}s){suffix}"
        )
        _report_archive(result, out)
    return EXIT_PARTIAL if failed else EXIT_OK


def cmd_render(config: Config, args: argparse.Namespace, out: Output) -> int:
    failed = False
    for account in config.select(args.accounts):
        if not (config.state_dir / f"{account.name}.sqlite3").exists():
            out.info(f"[{account.name}] nothing downloaded yet")
            continue
        result = rebuild_account(account, config.state_dir, out, force=args.force)
        if result.locked:
            out.warning(f"[{account.name}] skipped: another mailabruf run is active for this account")
            continue
        if result.error:
            out.error(f"[{account.name}] {result.error}")
        failed = failed or result.failed
        _report_archive(result, out, always=True)
    return EXIT_PARTIAL if failed else EXIT_OK


def _report_archive(result: AccountResult, out: Output, always: bool = False) -> None:
    archive = result.archive
    if archive is None:
        return
    line = (
        f"[{result.name}] PDF: {archive.pdf_created} created, {archive.pdf_failed} failed; "
        f"index: {archive.index}"
    )
    if always or archive.pdf_created or archive.pdf_failed:
        out.info(line)
    else:
        out.detail(line)


def cmd_folders(config: Config, args: argparse.Namespace, out: Output) -> int:
    (account,) = config.select([args.account])
    try:
        with Connection(account) as conn:
            folders = conn.list_folders()
    except ImapError as exc:
        out.error(f"[{account.name}] {exc}")
        return EXIT_PARTIAL
    excluded = set(account.exclude_folders)
    for folder in folders:
        display = storage.display_name(folder.name)
        notes = []
        if display != folder.name:
            notes.append(f"raw: {folder.name}")
        if not folder.selectable:
            notes.append("not selectable")
        if folder.name in excluded or display in excluded:
            notes.append("excluded")
        out.info(display + (f"  ({', '.join(notes)})" if notes else ""))
    return EXIT_OK


def cmd_status(config: Config, args: argparse.Namespace, out: Output) -> int:
    for account in config.select(args.accounts):
        db_path = config.state_dir / f"{account.name}.sqlite3"
        if not db_path.exists():
            out.info(f"[{account.name}] nothing downloaded yet (target: {account.target_dir})")
            continue
        with State(db_path) as state:
            rows = state.summary()
        for row in rows:
            out.info(
                f"[{account.name}] {storage.display_name(row.folder)}: {row.saved} saved, "
                f"{row.duplicate} duplicate, last {row.last_fetched}"
            )
        saved = sum(r.saved for r in rows)
        duplicate = sum(r.duplicate for r in rows)
        last = max((r.last_fetched for r in rows if r.last_fetched), default="never")
        out.info(
            f"[{account.name}] total: {saved} saved, {duplicate} duplicate in {len(rows)} folders, "
            f"last {last} (target: {account.target_dir})"
        )
    return EXIT_OK


def cmd_check(config: Config, args: argparse.Namespace, out: Output) -> int:
    failed = False
    for account in config.select(args.accounts):
        try:
            with Connection(account) as conn:
                count = len(conn.list_folders())
        except ImapError as exc:
            out.error(f"[{account.name}] {exc}")
            failed = True
            continue
        out.info(
            f"[{account.name}] ok: logged in to {account.host}:{account.port} as "
            f"{account.username}, {count} folders, target {account.target_dir}"
        )
    return EXIT_PARTIAL if failed else EXIT_OK
