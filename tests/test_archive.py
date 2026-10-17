import sqlite3
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from conftest import make_message

from mailabruf import mailinfo
from mailabruf.fetcher import fetch_account, rebuild_account
from mailabruf.index import IndexEntry, render_index
from mailabruf.pdf import MailURLFetcher, build_html, render_pdf
from mailabruf.state import State

HTML_MAIL = b"""From: =?utf-8?q?J=C3=BCrgen_M=C3=BCller?= <jm@example.org>
To: Bob <bob@example.org>, carol@example.org
Cc: Dave <dave@example.org>
Subject: =?utf-8?q?Gr=C3=BC=C3=9Fe_<script>?=
Date: Fri, 16 Oct 2026 10:30:00 +0200
Message-ID: <html@example.org>
MIME-Version: 1.0
Content-Type: multipart/related; boundary="B"

--B
Content-Type: text/html; charset=utf-8

<html><head><style>p{color:red}</style></head><body><p>Hallo</p>
<img src="cid:logo@x"><img src="https://tracker.example.com/pixel.gif"><img src="file:///etc/passwd">
</body></html>
--B
Content-Type: image/png; name="logo.png"
Content-ID: <logo@x>
Content-Transfer-Encoding: base64

iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==
--B--
""".replace(b"\n", b"\r\n")


def test_summarize_decodes_headers():
    summary = mailinfo.summarize(HTML_MAIL)
    assert summary.subject == "Grüße <script>"
    assert summary.sender == "Jürgen Müller <jm@example.org>"
    assert summary.recipients == "Bob <bob@example.org>, carol@example.org, Dave <dave@example.org>"


def test_build_html_inserts_header_and_escapes():
    message = BytesParser(policy=policy.default).parsebytes(HTML_MAIL)
    document = build_html(message)
    assert document.index('id="mailabruf-header"') > document.index("<body>")
    assert "Grüße &lt;script&gt;" in document
    assert "logo.png" in document


def test_plain_mail_is_escaped():
    message = BytesParser(policy=policy.default).parsebytes(make_message("x", body="a < b & c"))
    assert '<pre id="mailabruf-plain">a &lt; b &amp; c' in build_html(message)


def test_url_fetcher_serves_cid_and_blocks_everything_else():
    fetcher = MailURLFetcher({"logo@x": (b"PNG", "image/png")})
    response = fetcher.fetch("cid:logo@x")
    assert response.read() == b"PNG" and response.content_type == "image/png"
    for url in ("https://tracker.example.com/p.gif", "file:///etc/passwd", "cid:unknown"):
        with pytest.raises(ValueError):
            fetcher.fetch(url)


def test_render_pdf(tmp_path):
    target = tmp_path / "mail.pdf"
    render_pdf(HTML_MAIL, target)
    assert target.read_bytes().startswith(b"%PDF")
    assert target.stat().st_mode & 0o777 == 0o600


def test_render_index_links_and_escaping(tmp_path):
    entries = [
        IndexEntry(
            received=datetime(2026, 10, 16, 8, 30, tzinfo=timezone.utc),
            folder="Kündigungen",
            subject="<b>Hi</b> & bye",
            sender="A <a@x>",
            recipients="b@x",
            eml=tmp_path / "Kündigungen" / "2026 x_1.eml",
            pdf=None,
        ),
        IndexEntry(None, "INBOX", "", "", "", tmp_path / "INBOX" / "u_2.eml", tmp_path / "INBOX" / "u_2.pdf"),
    ]
    page = render_index("acc", tmp_path, entries)
    assert "&lt;b&gt;Hi&lt;/b&gt; &amp; bye" in page
    assert 'href="K%C3%BCndigungen/2026%20x_1.eml"' in page
    assert 'href="INBOX/u_2.pdf"' in page
    assert page.count(">PDF</a>") == 1
    assert '<option value="INBOX">' in page
    # Newest first, entries without date last.
    assert page.index("2026%20x_1.eml") < page.index("u_2.eml")


def test_fetch_creates_pdfs_and_index(account, server, out, tmp_path):
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, HTML_MAIL)
    server.add_message("INBOX", 2, make_message("plain", "<2@example.org>"))

    result = fetch_account(account, tmp_path / "state", out, connection_factory=server.connect)

    assert result.archive.pdf_created == 2 and not result.failed
    emls = sorted(account.target_dir.rglob("*.eml"))
    assert all(e.with_suffix(".pdf").exists() for e in emls)
    index = (account.target_dir / "index.html").read_text()
    assert "Grüße &lt;script&gt;" in index and index.count(">PDF</a>") == 2

    again = fetch_account(account, tmp_path / "state", out, connection_factory=server.connect)
    assert again.archive.pdf_created == 0

    emls[0].with_suffix(".pdf").unlink()
    assert rebuild_account(account, tmp_path / "state", out).archive.pdf_created == 1
    assert rebuild_account(account, tmp_path / "state", out, force=True).archive.pdf_created == 2


def test_failed_pdf_is_only_retried_by_render(account, server, out, tmp_path, monkeypatch):
    server.add_folder("INBOX")
    server.add_message("INBOX", 1, make_message("one", "<1@example.org>"))

    def broken(data, target):
        raise RuntimeError("boom")

    monkeypatch.setattr("mailabruf.archive.render_pdf", broken)
    result = fetch_account(account, tmp_path / "state", out, connection_factory=server.connect)
    assert result.archive.pdf_failed == 1 and result.failed
    assert "boom" in out._stderr.getvalue()

    second = fetch_account(account, tmp_path / "state", out, connection_factory=server.connect)
    assert second.archive.pdf_failed == 0 and not second.failed
    assert rebuild_account(account, tmp_path / "state", out).archive.pdf_failed == 1


def test_old_database_is_migrated_and_backfilled(account, out, tmp_path):
    """Databases and files from version 0.1.0 get metadata, PDFs and an index."""
    eml = account.target_dir / "INBOX" / "2026-10-16_103000_1.eml"
    eml.parent.mkdir(parents=True)
    eml.write_bytes(make_message("old one", "<old@example.org>"))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db = sqlite3.connect(state_dir / "test.sqlite3")
    db.executescript("""
        CREATE TABLE folders (name TEXT PRIMARY KEY, uidvalidity INTEGER NOT NULL);
        CREATE TABLE messages (folder TEXT NOT NULL, uidvalidity INTEGER NOT NULL,
            uid INTEGER NOT NULL, message_id TEXT, path TEXT, status TEXT NOT NULL,
            fetched_at TEXT NOT NULL, PRIMARY KEY (folder, uidvalidity, uid));
    """)
    db.execute(
        "INSERT INTO messages VALUES ('INBOX', 1, 1, '<old@example.org>', ?, 'saved', '2026-10-17')",
        (str(eml),),
    )
    db.commit()
    db.close()

    result = rebuild_account(account, state_dir, out)

    assert result.archive.pdf_created == 1
    with State(state_dir / "test.sqlite3") as state:
        (message,) = state.saved_messages()
    assert message.subject == "old one"
    assert message.received.startswith("2026-10-16T10:30:00")
    assert "2026-10-16 10:30" in (account.target_dir / "index.html").read_text()
