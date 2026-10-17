from datetime import datetime, timedelta, timezone

from mailabruf.imap_client import (
    iter_fetch_items,
    parse_header_info,
    parse_internaldate,
    parse_list_response,
    quote,
)


def test_parse_list_response():
    data = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasChildren \\Noselect) "/" Archiv',
        b'(\\HasNoChildren \\Drafts) "/" "Entw&APw-rfe"',
        (b'(\\HasNoChildren) "/" {11}', b'Mit "Quote"'),
        b'(\\HasNoChildren) NIL "Flat"',
    ]
    folders = parse_list_response(data)
    assert [f.name for f in folders] == ["INBOX", "Archiv", "Entw&APw-rfe", 'Mit "Quote"', "Flat"]
    assert folders[0].selectable
    assert not folders[1].selectable
    assert folders[4].delimiter is None


def test_iter_fetch_items_with_trailing_data():
    data = [
        (b'1 (UID 5 BODY[HEADER.FIELDS (MESSAGE-ID)] {28}', b"Message-ID: <a@b>\r\n\r\n"),
        b' INTERNALDATE "17-Jul-2026 02:44:25 -0700")',
        b"2 (UID 6 FLAGS (\\Seen))",
    ]
    items = iter_fetch_items(data)
    assert len(items) == 2
    assert "INTERNALDATE" in items[0][0]
    assert items[0][1].startswith(b"Message-ID")
    assert items[1] == ("2 (UID 6 FLAGS (\\Seen))", None)


def test_parse_internaldate():
    value = parse_internaldate(" 7-Jul-2026 02:44:25 -0700")
    assert value == datetime(2026, 7, 7, 2, 44, 25, tzinfo=timezone(timedelta(hours=-7)))
    assert parse_internaldate("garbage") is None


def test_parse_header_info_falls_back_to_date_header():
    header = b"Message-ID:\r\n  <x@example.org>\r\nDate: Fri, 16 Oct 2026 10:30:00 +0200\r\n\r\n"
    info = parse_header_info(3, header, None)
    assert info.message_id == "<x@example.org>"
    assert info.received == datetime(2026, 10, 16, 10, 30, tzinfo=timezone(timedelta(hours=2)))


def test_parse_header_info_without_message_id():
    assert parse_header_info(1, b"\r\n", None).message_id is None


def test_quote():
    assert quote('Gesendete "Objekte"') == '"Gesendete \\"Objekte\\""'
