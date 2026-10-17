# SPDX-License-Identifier: GPL-2.0-or-later
"""Loading and validating the configuration.

Layout (default ``~/.config/mailabruf``)::

    config.toml              optional, [defaults] target_dir / state_dir
    accounts/<name>.toml     one file per mailbox
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "mailabruf"

_DEFAULTS_KEYS = {"target_dir", "state_dir"}
_ACCOUNT_KEYS = {
    "host": "string",
    "port": "integer",
    "ssl": "boolean",
    "starttls": "boolean",
    "username": "string",
    "password": "string",
    "target_dir": "string",
    "enabled": "boolean",
    "exclude_folders": "list of strings",
    "timeout": "number",
}


class ConfigError(Exception):
    """Missing or invalid configuration."""


@dataclass(frozen=True)
class Account:
    name: str
    host: str
    username: str
    password: str
    target_dir: Path
    port: int = 993
    ssl: bool = True
    starttls: bool = False
    enabled: bool = True
    exclude_folders: tuple[str, ...] = ()
    timeout: float = 60.0


@dataclass
class Config:
    config_dir: Path
    state_dir: Path
    accounts: dict[str, Account]
    warnings: list[str] = field(default_factory=list)

    def select(self, names: Iterable[str] = ()) -> list[Account]:
        """Accounts named explicitly, or all enabled accounts if none are named."""
        names = list(names)
        if not names:
            return [a for a in self.accounts.values() if a.enabled]
        unknown = [n for n in names if n not in self.accounts]
        if unknown:
            known = ", ".join(self.accounts) or "none"
            raise ConfigError(f"unknown account(s): {', '.join(unknown)} (configured: {known})")
        return [self.accounts[n] for n in names]


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / APP_NAME


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / APP_NAME


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = (config_dir or default_config_dir()).expanduser()
    if not config_dir.is_dir():
        raise ConfigError(f"configuration directory not found: {config_dir}")

    defaults: dict = {}
    global_file = config_dir / "config.toml"
    if global_file.exists():
        data = _read_toml(global_file)
        _reject_unknown(global_file, data, {"defaults"})
        defaults = data.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ConfigError(f"{global_file}: [defaults] must be a table")
        _reject_unknown(global_file, defaults, _DEFAULTS_KEYS)
        for key, value in defaults.items():
            _check_type(global_file, key, value, "string")

    state_dir = (
        _resolve_path(defaults["state_dir"], config_dir)
        if "state_dir" in defaults
        else default_state_dir()
    )
    default_target = (
        _resolve_path(defaults["target_dir"], config_dir) if "target_dir" in defaults else None
    )

    accounts_dir = config_dir / "accounts"
    files = sorted(accounts_dir.glob("*.toml")) if accounts_dir.is_dir() else []
    if not files:
        raise ConfigError(f"no account files (*.toml) found in {accounts_dir}")

    warnings = []
    accounts = {}
    for path in files:
        if warning := _permission_warning(path):
            warnings.append(warning)
        accounts[path.stem] = _parse_account(
            path.stem, _read_toml(path), path, config_dir, default_target
        )
    return Config(config_dir=config_dir, state_dir=state_dir, accounts=accounts, warnings=warnings)


def _parse_account(
    name: str, data: dict, path: Path, config_dir: Path, default_target: Path | None
) -> Account:
    _reject_unknown(path, data, set(_ACCOUNT_KEYS))
    for key, value in data.items():
        _check_type(path, key, value, _ACCOUNT_KEYS[key])
    for key in ("host", "username", "password"):
        if not data.get(key):
            raise ConfigError(f"{path}: missing required key '{key}'")

    if "target_dir" in data:
        target_dir = _resolve_path(data["target_dir"], config_dir)
    elif default_target is not None:
        target_dir = default_target / name
    else:
        raise ConfigError(
            f"{path}: 'target_dir' is not set (neither in the account nor in config.toml [defaults])"
        )

    use_ssl = data.get("ssl", True)
    starttls = data.get("starttls", False)
    if use_ssl and starttls:
        raise ConfigError(f"{path}: 'ssl' and 'starttls' are mutually exclusive")
    if not use_ssl and not starttls:
        raise ConfigError(f"{path}: unencrypted connections are not supported; set ssl or starttls")

    port = data.get("port", 993 if use_ssl else 143)
    if not 0 < port < 65536:
        raise ConfigError(f"{path}: 'port' out of range: {port}")
    timeout = float(data.get("timeout", 60.0))
    if timeout <= 0:
        raise ConfigError(f"{path}: 'timeout' must be positive")

    return Account(
        name=name,
        host=data["host"],
        username=data["username"],
        password=data["password"],
        target_dir=target_dir,
        port=port,
        ssl=use_ssl,
        starttls=starttls,
        enabled=data.get("enabled", True),
        exclude_folders=tuple(data.get("exclude_folders", ())),
        timeout=timeout,
    )


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from exc


def _reject_unknown(path: Path, data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(sorted(unknown))}")


def _check_type(path: Path, key: str, value: object, expected: str) -> None:
    if expected == "boolean":
        ok = isinstance(value, bool)
    elif expected == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "string":
        ok = isinstance(value, str)
    else:
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    if not ok:
        raise ConfigError(f"{path}: '{key}' must be a {expected}")


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _permission_warning(path: Path) -> str | None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return (
            f"{path} contains a password but is accessible by other users "
            f"(mode {stat.S_IMODE(mode):o}); run: chmod 600 {path}"
        )
    return None
