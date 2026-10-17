from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailabruf import storage, utf7


@pytest.mark.parametrize(
    "raw, decoded",
    [
        ("INBOX", "INBOX"),
        ("Entw&APw-rfe", "Entwürfe"),
        ("Gel&APY-schte Elemente", "Gelöschte Elemente"),
        ("Tom &- Jerry", "Tom & Jerry"),
        ("&ZeVnLIqe-", "日本語"),
    ],
)
def test_utf7_decode(raw, decoded):
    assert utf7.decode(raw) == decoded


def test_utf7_invalid():
    with pytest.raises(ValueError):
        utf7.decode("broken&AB")


def test_folder_path():
    assert storage.folder_path("INBOX.Entw&APw-rfe", ".") == Path("INBOX/Entwürfe")
    assert storage.folder_path("a/b\\c", None) == Path("a_b_c")
    assert storage.folder_path("x/../y", "/") == Path("x/__/y")


def test_message_filename():
    received = datetime(2026, 10, 16, 8, 30, tzinfo=timezone.utc)
    name = storage.message_filename(received, 42)
    assert name.endswith("_42.eml") and name.startswith("2026-10-16_")
    assert storage.message_filename(None, 7) == "unknown-date_7.eml"


def test_unique_path_and_atomic_write(tmp_path):
    target = tmp_path / "sub" / "a.eml"
    storage.write_atomic(target, b"one")
    second = storage.unique_path(target)
    assert second == tmp_path / "sub" / "a-1.eml"
    storage.write_atomic(second, b"two")
    assert target.read_bytes() == b"one" and second.read_bytes() == b"two"
    assert sorted(p.name for p in target.parent.iterdir()) == ["a-1.eml", "a.eml"]
