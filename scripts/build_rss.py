#!/usr/bin/env python3
"""Generate rss.xml from data/latest.json (or data/<DATE>.json).

Usage: build_rss.py [YYYY-MM-DD]   (defaults to latest.json)
Writes rss.xml at the repo root.
"""
import json, pathlib, sys, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_NAME = "Daily Byte"
SITE_TAGLINE = "A byte-size daily digest for engineers who value depth over noise."
BASE_URL = "https://learn.shenthar.me"


def describe(s):
    parts = [s.get("why"), s.get("summary") or s.get("snippet"), s.get("debate"),
             ("Takeaway: " + s["takeaway"]) if s.get("takeaway") else None]
    return " ".join(p.strip() for p in parts if p and p.strip())


def build_rss(data):
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0"><channel>')
    out.append(f"  <title>{SITE_NAME}</title>")
    out.append(f"  <link>{BASE_URL}/</link>")
    note = data.get("editor_note") or f"{SITE_TAGLINE} — edition {data['date']}"
    out.append(f"  <description>{html.escape(note)}</description>")
    for sec in data.get("sections", []):
        for s in sec.get("stories", []):
            out.append("  <item>")
            out.append(f"    <title>{html.escape(s.get('title',''))}</title>")
            out.append(f"    <link>{html.escape(s.get('url',''))}</link>")
            out.append(f"    <guid isPermaLink=\"false\">{html.escape(s.get('url',''))}</guid>")
            out.append(f"    <description>{html.escape(describe(s))}</description>")
            if s.get("published_at"):
                out.append(f"    <pubDate>{html.escape(s['published_at'])}</pubDate>")
            if s.get("source"):
                out.append(f"    <source>{html.escape(s['source'])}</source>")
            out.append("  </item>")
    out.append("</channel></rss>")
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) == 2:
        date = sys.argv[1]
        data = json.load(open(ROOT / "data" / f"{date}.json"))
    else:
        data = json.load(open(ROOT / "data" / "latest.json"))
    rss = build_rss(data)
    (ROOT / "rss.xml").write_text(rss)
    print(f"wrote rss.xml ({len(rss)} bytes)")


if __name__ == "__main__":
    main()
