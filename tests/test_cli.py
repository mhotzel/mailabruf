from conftest import make_message

from mailabruf.cli import main
from mailabruf.config import load_config
from mailabruf.fetcher import fetch_account


def setup_config(tmp_path):
    config_dir = tmp_path / "config"
    (config_dir / "accounts").mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f'[defaults]\ntarget_dir = "{tmp_path / "mail"}"\nstate_dir = "{tmp_path / "state"}"\n'
    )
    account_file = config_dir / "accounts" / "test.toml"
    account_file.write_text('host = "imap.invalid"\nusername = "u"\npassword = "p"\n')
    account_file.chmod(0o600)
    return config_dir


def fetch_two_messages(config_dir, server, out):
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))
    server.add_message("INBOX", 2, make_message("two", "<2@example.org>"))
    config = load_config(config_dir)
    fetch_account(config.accounts["test"], config.state_dir, out, connection_factory=server.connect)
    return config.accounts["test"]


def test_render_before_first_fetch(tmp_path, capsys):
    config_dir = setup_config(tmp_path)
    assert main(["--config-dir", str(config_dir), "render"]) == 0
    assert "[test] nothing downloaded yet" in capsys.readouterr().out


def test_render_and_force(tmp_path, server, out, capsys):
    config_dir = setup_config(tmp_path)
    account = fetch_two_messages(config_dir, server, out)
    (account.target_dir / "index.html").unlink()

    assert main(["--config-dir", str(config_dir), "render"]) == 0
    assert "[test] PDF: 0 created, 0 failed; index: " in capsys.readouterr().out
    assert (account.target_dir / "index.html").exists()

    assert main(["--config-dir", str(config_dir), "render", "--force"]) == 0
    assert "[test] PDF: 2 created, 0 failed" in capsys.readouterr().out


def test_render_reports_failed_pdfs(tmp_path, server, out, capsys, monkeypatch):
    config_dir = setup_config(tmp_path)
    fetch_two_messages(config_dir, server, out)

    def broken(data, target):
        raise RuntimeError("boom")

    monkeypatch.setattr("mailabruf.archive.render_pdf", broken)
    assert main(["--config-dir", str(config_dir), "render", "--force"]) == 1
    captured = capsys.readouterr()
    assert "[test] PDF: 0 created, 2 failed" in captured.out
    assert captured.err.count("RuntimeError: boom") == 2


def test_render_reports_missing_weasyprint(tmp_path, server, out, capsys, monkeypatch):
    config_dir = setup_config(tmp_path)
    fetch_two_messages(config_dir, server, out)

    monkeypatch.setattr("mailabruf.archive.pdf_unavailable", lambda: "no pango")
    assert main(["--config-dir", str(config_dir), "render", "--force"]) == 1
    captured = capsys.readouterr()
    assert "error: [test] 2 PDFs skipped: no pango" in captured.err
    assert "[test] PDF: 0 created, 0 failed" in captured.out


def test_render_unknown_account(tmp_path, capsys):
    config_dir = setup_config(tmp_path)
    assert main(["--config-dir", str(config_dir), "render", "nope"]) == 2
    assert "unknown account(s): nope" in capsys.readouterr().err
