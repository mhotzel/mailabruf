# SPDX-License-Identifier: GPL-2.0-or-later
"""index.html of an account: a sortable, searchable table of all saved messages."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from mailabruf import storage


@dataclass(frozen=True)
class IndexEntry:
    received: datetime | None
    folder: str
    subject: str
    sender: str
    recipients: str
    eml: Path
    pdf: Path | None


def write_index(account_name: str, target_dir: Path, entries: list[IndexEntry]) -> Path:
    path = target_dir / "index.html"
    storage.write_atomic(path, render_index(account_name, target_dir, entries).encode())
    return path


def render_index(account_name: str, target_dir: Path, entries: list[IndexEntry]) -> str:
    entries = sorted(entries, key=_sort_key, reverse=True)
    folders = sorted({e.folder for e in entries}, key=str.casefold)
    options = "".join(f'<option value="{_attr(f)}">{_esc(f)}</option>' for f in folders)
    rows = "\n".join(_row(e, target_dir) for e in entries)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        _TEMPLATE.replace("__TITLE__", _esc(f"Mail archive: {account_name}"))
        .replace("__GENERATED__", _esc(generated))
        .replace("__TOTAL__", str(len(entries)))
        .replace("__OPTIONS__", options)
        .replace("__ROWS__", rows)
    )


def _sort_key(entry: IndexEntry) -> tuple:
    return (entry.received.timestamp() if entry.received else 0.0, str(entry.eml))


def _row(entry: IndexEntry, target_dir: Path) -> str:
    if entry.received:
        local = entry.received.astimezone()
        date_cell = f'<td data-sort="{local:%Y-%m-%dT%H:%M:%S}">{local:%Y-%m-%d %H:%M}</td>'
    else:
        date_cell = '<td data-sort="">–</td>'
    links = f'<a href="{_href(entry.eml, target_dir)}">EML</a>'
    if entry.pdf is not None:
        links += f' <a href="{_href(entry.pdf, target_dir)}" target="_blank">PDF</a>'
    return (
        f'<tr data-folder="{_attr(entry.folder)}">{date_cell}'
        f"<td>{_esc(entry.folder)}</td>"
        f"<td>{_esc(entry.subject) or '<em>(no subject)</em>'}</td>"
        f"<td>{_esc(entry.sender)}</td>"
        f"<td>{_esc(entry.recipients)}</td>"
        f'<td class="links">{links}</td></tr>'
    )


def _href(path: Path, target_dir: Path) -> str:
    relative = Path(os.path.relpath(path, target_dir))
    return _attr("/".join(quote(part) for part in relative.parts))


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e2e2e6; --stripe: #f6f6f8; --accent: #0a62c9; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1c1c1e; --fg: #f2f2f7; --muted: #a1a1a6; --line: #3a3a3c; --stripe: #242426; --accent: #64a8ff; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
       font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; }
.meta { color: var(--muted); margin: 0 0 12px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.controls input, .controls select { font: inherit; padding: 6px 8px; color: var(--fg); background: var(--bg);
       border: 1px solid var(--line); border-radius: 6px; }
.controls input { flex: 1 1 260px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--line); }
th { position: sticky; top: 0; background: var(--bg); cursor: pointer; user-select: none; white-space: nowrap; }
th[aria-sort="ascending"]::after { content: " ▲"; }
th[aria-sort="descending"]::after { content: " ▼"; }
tbody tr:nth-child(even) { background: var(--stripe); }
td:nth-child(1), td.links { white-space: nowrap; }
td { overflow-wrap: anywhere; }
td:nth-child(2) { overflow-wrap: normal; }
a { color: var(--accent); }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="meta">Generated __GENERATED__ · <span id="count">__TOTAL__ messages</span></p>
<div class="controls">
  <input id="search" type="search" placeholder="Search subject, sender, recipients …" autofocus>
  <select id="folder"><option value="">All folders</option>__OPTIONS__</select>
</div>
<table id="mails">
<thead><tr>
  <th data-col="0" aria-sort="descending">Date</th><th data-col="1">Folder</th><th data-col="2">Subject</th>
  <th data-col="3">From</th><th data-col="4">To</th><th>Files</th>
</tr></thead>
<tbody>
__ROWS__
</tbody>
</table>
<script>
(() => {
  const table = document.getElementById("mails");
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const search = document.getElementById("search");
  const folder = document.getElementById("folder");
  const count = document.getElementById("count");
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

  function filter() {
    const query = search.value.trim().toLowerCase();
    const selected = folder.value;
    let shown = 0;
    for (const row of rows) {
      const visible = (!selected || row.dataset.folder === selected) &&
                      (!query || row.textContent.toLowerCase().includes(query));
      row.hidden = !visible;
      if (visible) shown++;
    }
    count.textContent = shown === rows.length ? rows.length + " messages"
                                              : shown + " of " + rows.length + " messages";
  }

  let sortCol = 0, sortDir = -1;
  table.tHead.addEventListener("click", (event) => {
    const th = event.target.closest("th[data-col]");
    if (!th) return;
    const col = Number(th.dataset.col);
    sortDir = col === sortCol ? -sortDir : (col === 0 ? -1 : 1);
    sortCol = col;
    const key = (row) => {
      const cell = row.cells[col];
      return cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent;
    };
    rows.sort((a, b) => collator.compare(key(a), key(b)) * sortDir);
    const fragment = document.createDocumentFragment();
    for (const row of rows) fragment.appendChild(row);
    tbody.appendChild(fragment);
    for (const header of table.tHead.querySelectorAll("th[data-col]")) header.removeAttribute("aria-sort");
    th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
  });

  search.addEventListener("input", filter);
  folder.addEventListener("change", filter);
  filter();
})();
</script>
</body>
</html>
"""
