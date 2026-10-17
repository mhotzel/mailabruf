"""Rendering of messages to PDF with WeasyPrint.

Only resources contained in the message itself (cid: parts, data: URIs) are
loaded; remote and local file URLs are blocked.
"""

from __future__ import annotations

import html
import logging
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import unquote

from weasyprint import CSS, HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

from mailabruf import storage
from mailabruf.mailinfo import address_text, header_text

# WeasyPrint logs every unsupported CSS property of HTML mails; keep the console clean.
for _logger in ("weasyprint", "fontTools"):
    logging.getLogger(_logger).setLevel(logging.CRITICAL)

_BODY_TAG_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

_STYLESHEET = """
@page {
  size: A4;
  margin: 14mm 12mm 16mm;
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-family: sans-serif; font-size: 8pt; color: #666;
  }
}
#mailabruf-header {
  font-family: sans-serif !important; font-size: 9.5pt !important; color: #000 !important;
  background: #f2f2f2 !important; border: 1px solid #ccc !important;
  padding: 6pt 8pt !important; margin: 0 0 10pt !important;
}
#mailabruf-header table { border-collapse: collapse; width: 100%; }
#mailabruf-header th {
  text-align: left; vertical-align: top; white-space: nowrap; width: 1%;
  padding: 1pt 8pt 1pt 0; font-weight: bold;
}
#mailabruf-header td { padding: 1pt 0; overflow-wrap: anywhere; }
#mailabruf-plain {
  font-family: monospace; font-size: 9pt; margin: 0;
  white-space: pre-wrap; overflow-wrap: anywhere;
}
img { max-width: 100%; }
"""
_css: CSS | None = None


class MailURLFetcher(URLFetcher):
    """Serves cid: parts of the message and data: URIs; everything else is refused."""

    def __init__(self, parts: dict[str, tuple[bytes, str]]) -> None:
        super().__init__(allowed_protocols=("data",))
        self._parts = parts

    def fetch(self, url, headers=None):
        if url[:4].lower() == "cid:":
            key = unquote(url[4:]).strip().strip("<>")
            if key not in self._parts:
                raise ValueError(f"unknown content id: {url}")
            data, content_type = self._parts[key]
            return URLFetcherResponse(url, data, {"Content-Type": content_type})
        return super().fetch(url, headers)


def render_pdf(data: bytes, target: Path) -> None:
    global _css
    if _css is None:
        _css = CSS(string=_STYLESHEET)
    message = BytesParser(policy=policy.default).parsebytes(data)
    document = HTML(string=build_html(message), url_fetcher=MailURLFetcher(content_parts(message)))
    storage.write_atomic(target, document.write_pdf(stylesheets=[_css]))


def build_html(message: EmailMessage) -> str:
    body_part = _body_part(message)
    header = _header_html(message, body_part)
    if body_part is None:
        return _page(header + "<p><em>(no text content)</em></p>")
    body = _text_content(body_part)
    if body_part.get_content_type() == "text/html":
        match = _BODY_TAG_RE.search(body)
        if match:
            return body[: match.end()] + header + body[match.end() :]
        return header + body
    return _page(f'{header}<pre id="mailabruf-plain">{html.escape(body)}</pre>')


def content_parts(message: EmailMessage) -> dict[str, tuple[bytes, str]]:
    """Parts with a Content-ID, keyed by the id without angle brackets."""
    parts = {}
    for part in message.walk():
        if part.is_multipart():
            continue
        content_id = header_text(part, "Content-ID").strip("<>")
        if not content_id:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            parts[content_id] = (payload, part.get_content_type())
    return parts


def _page(content: str) -> str:
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{content}</body></html>'


def _body_part(message: EmailMessage) -> EmailMessage | None:
    try:
        return message.get_body(preferencelist=("html", "plain"))
    except Exception:
        return None


def _text_content(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except Exception:  # unknown charset, broken encoding, ...
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def _attachments(message: EmailMessage, body_part: EmailMessage | None) -> list[str]:
    result = []
    for part in message.walk():
        if part.is_multipart() or part is body_part:
            continue
        try:
            filename = part.get_filename()
        except Exception:
            filename = None
        if not filename and part.get_content_disposition() != "attachment":
            continue
        size = len(part.get_payload(decode=True) or b"")
        result.append(f"{filename or '(unnamed)'} ({_format_size(size)})")
    return result


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _header_html(message: EmailMessage, body_part: EmailMessage | None) -> str:
    rows = [
        ("From", address_text(message, "From")),
        ("To", address_text(message, "To")),
        ("Cc", address_text(message, "Cc")),
        ("Date", header_text(message, "Date")),
        ("Subject", header_text(message, "Subject")),
        ("Attachments", ", ".join(_attachments(message, body_part))),
    ]
    cells = "".join(
        f"<tr><th>{label}</th><td>{html.escape(value)}</td></tr>" for label, value in rows if value
    )
    return f'<div id="mailabruf-header"><table>{cells}</table></div>'
