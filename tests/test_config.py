import pytest

from mailabruf.config import ConfigError, load_config

ACCOUNT = """
host = "imap.strato.de"
username = "info@example.de"
password = "secret"
"""


def write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def test_defaults_and_target_dir_from_global_config(tmp_path):
    write(tmp_path / "config.toml", '[defaults]\ntarget_dir = "archive"\nstate_dir = "/tmp/st"\n')
    write(tmp_path / "accounts" / "strato.toml", ACCOUNT)

    config = load_config(tmp_path)

    account = config.accounts["strato"]
    assert account.port == 993 and account.ssl and not account.starttls
    assert account.target_dir == tmp_path / "archive" / "strato"
    assert str(config.state_dir) == "/tmp/st"
    assert config.warnings == []
    assert config.select() == [account]


def test_account_target_dir_and_starttls(tmp_path):
    write(
        tmp_path / "accounts" / "a.toml",
        ACCOUNT + 'target_dir = "~/Mail"\nssl = false\nstarttls = true\nenabled = false\n',
    )
    config = load_config(tmp_path)
    account = config.accounts["a"]
    assert account.port == 143
    assert not account.target_dir.is_relative_to(tmp_path)
    assert config.select() == []
    assert config.select(["a"]) == [account]


@pytest.mark.parametrize(
    "extra, message",
    [
        ("", "target_dir"),
        ('target_dir = "x"\nprot = 1\n', "unknown key"),
        ('target_dir = "x"\nport = "993"\n', "'port' must be"),
        ('target_dir = "x"\nstarttls = true\n', "mutually exclusive"),
        ('target_dir = "x"\nssl = false\n', "unencrypted"),
    ],
)
def test_invalid_account(tmp_path, extra, message):
    write(tmp_path / "accounts" / "a.toml", ACCOUNT + extra)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_missing_password(tmp_path):
    write(tmp_path / "accounts" / "a.toml", 'host = "h"\nusername = "u"\ntarget_dir = "x"\n')
    with pytest.raises(ConfigError, match="password"):
        load_config(tmp_path)


def test_no_accounts(tmp_path):
    with pytest.raises(ConfigError, match="no account files"):
        load_config(tmp_path)


def test_permission_warning(tmp_path):
    write(tmp_path / "accounts" / "a.toml", ACCOUNT + 'target_dir = "x"\n', mode=0o644)
    config = load_config(tmp_path)
    assert "chmod 600" in config.warnings[0]


def test_unknown_account_selected(tmp_path):
    write(tmp_path / "accounts" / "a.toml", ACCOUNT + 'target_dir = "x"\n')
    with pytest.raises(ConfigError, match="unknown account"):
        load_config(tmp_path).select(["b"])
