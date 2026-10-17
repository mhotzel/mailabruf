import fcntl

from conftest import make_message

from mailabruf.fetcher import fetch_account


def run(account, server, out, tmp_path, **kwargs):
    return fetch_account(
        account, tmp_path / "state", out, connection_factory=server.connect, **kwargs
    )


def eml_files(account):
    return sorted(account.target_dir.rglob("*.eml"))


def test_first_run_saves_all_messages(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_folder("Archiv/2025")
    msg1 = make_message("one", "<1@example.org>")
    server.add_message("INBOX", 1, msg1)
    server.add_message("INBOX", 2, make_message("two", "<2@example.org>"))
    server.add_message("Archiv/2025", 7, make_message("old", "<3@example.org>"))

    result = run(account, server, out, tmp_path)

    assert result.error is None
    assert (result.new, result.duplicate, result.errors) == (3, 0, 0)
    files = eml_files(account)
    assert len(files) == 3
    assert (account.target_dir / "Archiv" / "2025").is_dir()
    inbox = sorted((account.target_dir / "INBOX").glob("*.eml"))
    assert inbox[0].read_bytes() == msg1
    assert inbox[0].name.endswith("_1.eml")
    assert inbox[0].stat().st_mode & 0o777 == 0o600


def test_second_run_downloads_nothing(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))
    run(account, server, out, tmp_path)
    server.add_message("INBOX", 2, make_message("two", "<2@example.org>"))
    server.fetched.clear()

    result = run(account, server, out, tmp_path)

    assert result.new == 1
    assert server.fetched == [("INBOX", 2)]
    assert len(eml_files(account)) == 2


def test_same_message_id_in_two_folders_is_saved_once(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_folder("Archiv")
    server.add_message("INBOX", 1, make_message("one", "<same@example.org>"))
    server.add_message("Archiv", 5, make_message("one", "<same@example.org>"))

    result = run(account, server, out, tmp_path)

    assert (result.new, result.duplicate) == (1, 1)
    assert len(eml_files(account)) == 1
    # The duplicate is remembered, so it is not checked again.
    server.fetched.clear()
    assert run(account, server, out, tmp_path).duplicate == 0


def test_messages_without_message_id_are_always_saved(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_folder("Archiv")
    server.add_message("INBOX", 1, make_message("one"))
    server.add_message("Archiv", 1, make_message("one"))

    result = run(account, server, out, tmp_path)

    assert (result.new, result.duplicate) == (2, 0)


def test_uidvalidity_change_does_not_duplicate_files(account, server, out, tmp_path):
    server.add_folder("INBOX", uidvalidity=1)
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))
    run(account, server, out, tmp_path)

    server.folders["INBOX"]["uidvalidity"] = 2
    result = run(account, server, out, tmp_path)

    assert (result.new, result.duplicate) == (0, 1)
    assert len(eml_files(account)) == 1
    assert "UIDVALIDITY changed" in out._stderr.getvalue()


def test_dry_run_saves_nothing(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))
    server.add_message("INBOX", 2, make_message("one again", "<1@example.org>"))

    result = run(account, server, out, tmp_path, dry_run=True)

    assert (result.new, result.duplicate) == (1, 1)
    assert eml_files(account) == []
    assert server.fetched == []
    assert run(account, server, out, tmp_path).new == 1


def test_failing_folder_does_not_stop_others(account, server, out, tmp_path):
    server.add_folder("Broken")
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))
    server.fail_examine.add("Broken")

    result = run(account, server, out, tmp_path)

    assert result.error is None
    assert (result.new, result.errors) == (1, 1)
    assert result.failed


def test_lost_connection_aborts_account(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.lose_connection_on.add("INBOX")

    result = run(account, server, out, tmp_path)

    assert result.error == "connection reset"


def test_excluded_and_noselect_folders_are_skipped(account, server, out, tmp_path):
    from dataclasses import replace

    account = replace(account, exclude_folders=("Spam",))
    server.add_folder("Spam")
    server.add_folder("Parent", flags=("\\Noselect",))
    server.add_message("Spam", 1, make_message("buy", "<spam@example.org>"))

    result = run(account, server, out, tmp_path)

    assert result.folders == []
    assert eml_files(account) == []


def test_locked_account_is_skipped(account, server, out, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    with open(state_dir / "test.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = run(account, server, out, tmp_path)
    assert result.locked
