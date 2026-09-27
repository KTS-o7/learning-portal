#!/usr/bin/env python3
"""Regenerate archive/index.html from archive.json and data/<DATE>.json.

Usage: build_archive_index.py [ignored-date]
Static HTML, no JS. Each row shows the date, weekday label, story count and
the editor's note when the edition has one.
"""
import json, pathlib, html
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Byte — Archive</title>
<meta name="description" content="Every past edition of Daily Byte.">
<link rel="alternate" type="application/rss+xml" title="Daily Byte" href="../rss.xml">
<link rel="canonical" href="https://learn.shenthar.me/archive/">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📚</text></svg>">
<link rel="stylesheet" href="../style.css?v=5">
</head>
<body data-page="archive">
<div class="archive">
  <h1>Daily Byte archive</h1>
  <p class="sub">__COUNT__ editions</p>
  <ul>
__ROWS__
  </ul>
  <p class="back"><a href="../">← Today's edition</a></p>
</div>
</body>
</html>
"""


def row(date: str) -> str:
    path = ROOT / "data" / f"{date}.json"
    d = {}
    if path.exists():
        try:
            d = json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    label = d.get("label") or datetime.strptime(date, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    total = d.get("total")
    note = d.get("editor_note") or ""
    cnt = f"{total} stories" if total else ""
    lead = f'<span class="lead">{html.escape(note)}</span>' if note else ""
    return (f'    <li><a href="{date}.html"><span class="d">{date[5:]}</span>'
            f'<span class="lbl">{html.escape(label)}</span><span class="cnt">{cnt}</span>{lead}</a></li>')


def main():
    dates = json.loads((ROOT / "archive.json").read_text())
    dates = [d for d in dates if (ROOT / "archive" / f"{d}.html").exists()]
    out = PAGE.replace("__COUNT__", str(len(dates))).replace("__ROWS__", "\n".join(row(d) for d in dates))
    (ROOT / "archive" / "index.html").write_text(out)
    print(f"wrote archive/index.html ({len(dates)} editions)")


if __name__ == "__main__":
    main()
