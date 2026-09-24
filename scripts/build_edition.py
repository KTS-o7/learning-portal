#!/usr/bin/env python3
"""Build the Daily Byte edition for TODAY (UTC).

Pipeline (matches learning-portal-curator SKILL.md):
  1. Fetch HN, arXiv, Lobsters, engineering blogs.
  2. Dedupe, score, apply caps (eng 5 / papers 5 / tools 4 / discussions 4).
  3. Summarise each selected article via MiniMax-M3 (max_tokens=1500).
  4. Emit data/<DATE>.json, data/latest.json, rss.xml, archive/<DATE>.html.
  5. Update archive.json (prepend today).
"""
from __future__ import annotations
import json, pathlib, re, os, sys, html, subprocess, hashlib, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path("/opt/learning-portal")
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
NOW = datetime.now(timezone.utc)

# Load .env (cron context doesn't source it).
for line in pathlib.Path("/root/.hermes/.env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

MINIMAX_KEY = os.environ["MINIMAX_API_KEY"]

UA = "Mozilla/5.0 (compatible; DailyByteBot/1.0)"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# ---- HTTP helpers ----------------------------------------------------------

def fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"  fetch fail {url}: {e}", file=sys.stderr)
        return None


def fetch_text(url: str, timeout: int = 15, max_bytes: int = 200_000) -> str:
    b = fetch(url, timeout)
    if not b:
        return ""
    return b[:max_bytes].decode("utf-8", errors="replace")


# ---- Feed parsers ----------------------------------------------------------

ENGINEERING_FEEDS = [
    ("Go Blog", "https://go.dev/blog/feed.atom"),
    ("Rust Blog", "https://blog.rust-lang.org/feed.xml"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("Arpit Bhayani", "https://arpitbhayani.me/rss.xml"),
    ("Julia Evans", "https://jvns.ca/atom.xml"),
    ("Sean Goedecke", "https://www.seangoedecke.com/rss.xml"),
]


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_atom_or_rss(url: str, source_name: str) -> list[dict]:
    raw = fetch(url)
    if not raw:
        return []
    items: list[dict] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    # Atom
    for entry in root.findall("atom:entry", ATOM_NS):
        title = _text(entry.find("atom:title", ATOM_NS))
        link_el = entry.find("atom:link[@rel='alternate']", ATOM_NS)
        if link_el is None:
            link_el = entry.find("atom:link", ATOM_NS)
        link = link_el.get("href") if link_el is not None else ""
        if not link:
            continue
        published = _text(entry.find("atom:published", ATOM_NS)) or _text(entry.find("atom:updated", ATOM_NS))
        summary = _text(entry.find("atom:summary", ATOM_NS)) or _text(entry.find("atom:content", ATOM_NS))
        # Strip HTML if any leaked through
        summary_clean = re.sub(r"<[^>]+>", " ", summary)
        summary_clean = re.sub(r"&[a-z]+;", " ", summary_clean)
        summary_clean = re.sub(r"\s+", " ", summary_clean).strip()
        items.append({
            "title": title,
            "url": link,
            "source": source_name,
            "source_kind": "blog",
            "published_at": published,
            "snippet": summary_clean[:600],
        })
    # RSS
    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub = _text(item.find("pubDate"))
        desc = _text(item.find("description"))
        desc_clean = re.sub(r"<[^>]+>", " ", desc)
        desc_clean = re.sub(r"&[a-z]+;", " ", desc_clean)
        desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
        if not title or not link:
            continue
        items.append({
            "title": title,
            "url": link,
            "source": source_name,
            "source_kind": "blog",
            "published_at": pub,
            "snippet": desc_clean[:600],
        })
    return items


def fetch_blogs() -> list[dict]:
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(parse_atom_or_rss, url, name) for name, url in ENGINEERING_FEEDS]
        for f in as_completed(futures):
            try:
                out.extend(f.result())
            except Exception as e:
                print(f"  blog fail: {e}", file=sys.stderr)
    return out


def fetch_arxiv() -> list[dict]:
    cats = ["cs.AI", "cs.LG", "cs.PL"]
    out: list[dict] = []
    for cat in cats:
        url = (
            f"http://export.arxiv.org/api/query?search_query=cat:{cat}"
            "&sortBy=submittedDate&sortOrder=descending&max_results=15"
        )
        raw = fetch(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for entry in root.findall("atom:entry", ATOM_NS):
            title = _text(entry.find("atom:title", ATOM_NS))
            link_el = entry.find("atom:id", ATOM_NS)
            link = _text(link_el)
            # Strip arxiv version link to abs page
            link = re.sub(r"v\d+$", "", link)
            pub = _text(entry.find("atom:published", ATOM_NS))
            author = _text(entry.find("atom:author/atom:name", ATOM_NS))
            summary = _text(entry.find("atom:summary", ATOM_NS))
            summary_clean = re.sub(r"<[^>]+>", " ", summary)
            summary_clean = re.sub(r"&[a-z]+;", " ", summary_clean)
            summary_clean = re.sub(r"\s+", " ", summary_clean).strip()
            if not title or not link:
                continue
            out.append({
                "title": re.sub(r"\s+", " ", title),
                "url": link,
                "source": author or "arXiv",
                "source_kind": "paper",
                "published_at": pub,
                "snippet": summary_clean[:800],
            })
    return out


def fetch_hn() -> list[dict]:
    raw = fetch("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not raw:
        return []
    try:
        ids = json.loads(raw)[:80]
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for i in ids:
        item_raw = fetch(f"https://hacker-news.firebaseio.com/v0/item/{i}.json")
        if not item_raw:
            continue
        try:
            d = json.loads(item_raw)
        except json.JSONDecodeError:
            continue
        if not d or d.get("dead") or d.get("deleted"):
            continue
        # HN text posts: strip HTML for the snippet
        text = (d.get("text") or "").strip()
        text_plain = re.sub(r"<[^>]+>", " ", text)
        text_plain = re.sub(r"&#x2F;", "/", text_plain)
        text_plain = re.sub(r"&amp;", "&", text_plain)
        text_plain = re.sub(r"\s+", " ", text_plain).strip()
        out.append({
            "title": d.get("title", ""),
            "url": d.get("url") or f"https://news.ycombinator.com/item?id={i}",
            "source": "Hacker News",
            "source_kind": "hn",
            "published_at": datetime.fromtimestamp(d.get("time", 0), tz=timezone.utc).isoformat() if d.get("time") else "",
            "snippet": text_plain[:800],
            "score": d.get("score", 0),
            "comments": d.get("descendants", 0),
        })
    return out


def fetch_lobsters() -> list[dict]:
    out: list[dict] = []
    for url in ("https://lobste.rs/rss", "https://lobste.rs/page/2.rss"):
        raw = fetch(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for item in root.findall(".//item"):
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            pub = _text(item.find("pubDate"))
            desc = _text(item.find("description"))
            desc_clean = re.sub(r"<[^>]+>", " ", desc)
            desc_clean = re.sub(r"&[a-z]+;", " ", desc_clean)
            desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
            if not title or not link:
                continue
            out.append({
                "title": title,
                "url": link,
                "source": "Lobsters",
                "source_kind": "lobsters",
                "published_at": pub,
                "snippet": desc_clean[:600],
            })
    return out


# ---- Article body extraction ----------------------------------------------

SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL)
STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


def extract_body(url: str) -> str:
    raw = fetch(url, timeout=15)
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = SCRIPT_RE.sub(" ", text)
    text = STYLE_RE.sub(" ", text)
    # Try <article> blocks
    articles = re.findall(r"<article[^>]*>(.*?)</article>", text, re.DOTALL | re.IGNORECASE)
    best = ""
    for a in articles:
        plain = TAG_RE.sub(" ", a)
        plain = WS_RE.sub(" ", plain).strip()
        if len(plain) > len(best):
            best = plain
    if len(best) < 400:
        mains = re.findall(r"<main[^>]*>(.*?)</main>", text, re.DOTALL | re.IGNORECASE)
        for m in mains:
            plain = TAG_RE.sub(" ", m)
            plain = WS_RE.sub(" ", plain).strip()
            if len(plain) > len(best):
                best = plain
    if len(best) < 400:
        paras = P_RE.findall(text)
        joined = " ".join(TAG_RE.sub(" ", p) for p in paras)
        plain = WS_RE.sub(" ", joined).strip()
        if len(plain) > len(best):
            best = plain
    return best[:10_000]


# ---- LLM summarisation -----------------------------------------------------

BAD_PREFIXES = (
    "this article", "the provided text", "the provided article", "the provided snippet",
    "the provided content",
    "as an ai model", "i'm unable", "i cannot", "i don't have", "i do not have",
    "the user wants", "the user is asking", "the user has asked",
    "let me analyze", "let me start", "let me write", "let me draft", "let me check",
    "let me first", "let me think", "let me see",
    "based on the prompt", "based on the article", "based on the provided",
    "based on the title", "based on the snippet", "you've provided only",
    "you have provided only", "you only provided", "i can see the", "i see that the",
    "the title and author", "the text provided", "here is a summary",
    "here's a summary", "in summary, the", "to summarize", "summary of the",
    "i need to", "i should",
)


def summarise(title: str, body: str, fallback_snippet: str = "") -> str:
    body = body or ""
    fb = (fallback_snippet or "").strip()
    prompt_body = body if len(body) > 200 else fb
    if len(prompt_body) < 200:
        # Hard fallback — derive from title/snippet only.
        return (fb or title)[:1200]
    prompt = (
        f"Title: {title}\n\n{prompt_body}\n\n"
        "Summarize in 4-6 sentences (100-150 words). Use only facts from the article. "
        "Plain prose, no lists or headings. Don't start with 'This article'."
    )
    payload = {
        "model": "MiniMax-M3",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
        "temperature": 0.2,
        "reasoning_split": True,
    }
    try:
        req = urllib.request.Request(
            "https://api.minimax.io/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {MINIMAX_KEY}",
                "Content-Type": "application/json",
                "User-Agent": UA,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        msg = resp["choices"][0]["message"]
        text = msg.get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if not text:
            return (fb or title)[:1200]
        low = text.lower().lstrip()
        for bad in BAD_PREFIXES:
            if low.startswith(bad):
                return (fb or title)[:1200]
        return text
    except Exception as e:
        print(f"  summarise fail: {e}", file=sys.stderr)
        return (fb or title)[:1200]


# ---- Scoring & selection -------------------------------------------------

def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())[:60]


def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # ISO with offset
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # ISO with Z
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # RFC 822
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def within_48h(item: dict) -> bool:
    dt = parse_dt(item.get("published_at", ""))
    if not dt:
        return True
    return abs((NOW - dt).total_seconds()) <= 48 * 3600


def score_blog(item: dict) -> float:
    s = 0.0
    dt = parse_dt(item.get("published_at", ""))
    if dt:
        age_h = (NOW - dt).total_seconds() / 3600
        s += max(0.0, 48 - age_h) / 12  # recent items win
    title = item["title"].lower()
    if any(p in title for p in ("announcing ", "introducing ")):
        s -= 3
    if item.get("source_kind") == "blog":
        s += 1
    return s


def score_paper(item: dict) -> float:
    s = 0.0
    dt = parse_dt(item.get("published_at", ""))
    if dt:
        age_h = (NOW - dt).total_seconds() / 3600
        s += max(0.0, 48 - age_h) / 12
    return s


def score_tool(item: dict) -> float:
    s = float(item.get("score", 0))
    title = item["title"].lower()
    if "github.com" in item.get("url", "").lower():
        s += 50
    if any(p in title for p in ("announcing ", "introducing ")):
        s -= 20
    return s


def score_disc(item: dict) -> float:
    s = float(item.get("score", 0)) + float(item.get("comments", 0)) * 0.3
    if item.get("source_kind") == "lobsters":
        s += 5
    return s


# ---- Pipeline --------------------------------------------------------------

def main():
    print(f"Edition for {TODAY}")

    print("Fetching engineering blogs...")
    blogs = fetch_blogs()
    print(f"  blogs: {len(blogs)}")

    print("Fetching arXiv...")
    papers = fetch_arxiv()
    print(f"  papers: {len(papers)}")

    print("Fetching HN...")
    hn = fetch_hn()
    print(f"  hn: {len(hn)}")

    print("Fetching Lobsters...")
    lobs = fetch_lobsters()
    print(f"  lobsters: {len(lobs)}")

    # ---- Dedupe by title ----
    seen: set[str] = set()

    def add(item: dict, store: list[dict]):
        k = _title_key(item["title"])
        if k in seen:
            return
        seen.add(k)
        store.append(item)

    blogs_pool: list[dict] = []
    papers_pool: list[dict] = []
    for it in blogs:
        add(it, blogs_pool)
    for it in papers:
        add(it, papers_pool)

    hn_pool: list[dict] = []
    for it in hn:
        add(it, hn_pool)
    lobs_pool: list[dict] = []
    for it in lobs:
        add(it, lobs_pool)

    # ---- Primary pass: 48h ----
    eng_recent = [x for x in blogs_pool if within_48h(x)]
    pap_recent = [x for x in papers_pool if within_48h(x)]

    # ---- Backfill if sparse ----
    if len(eng_recent) < 3:
        backfill = [x for x in blogs_pool if x not in eng_recent]
        backfill.sort(key=score_blog, reverse=True)
        eng_recent.extend(backfill[: 5 - len(eng_recent)])
    if len(pap_recent) < 3:
        backfill = [x for x in papers_pool if x not in pap_recent]
        backfill.sort(key=score_paper, reverse=True)
        pap_recent.extend(backfill[: 5 - len(pap_recent)])

    # ---- Score & cap ----
    eng_recent.sort(key=score_blog, reverse=True)
    pap_recent.sort(key=score_paper, reverse=True)
    # Engineering blog diversity: max 3 per source
    selected_eng: list[dict] = []
    src_count: dict[str, int] = {}
    for it in eng_recent:
        s = it["source"]
        if src_count.get(s, 0) >= 3:
            continue
        selected_eng.append(it)
        src_count[s] = src_count.get(s, 0) + 1
        if len(selected_eng) >= 5:
            break
    selected_pap = pap_recent[:5]

    # Tools: HN items pointing at github.com repos
    tools_pool = [x for x in hn_pool if "github.com" in x.get("url", "").lower()]
    tools_pool.sort(key=score_tool, reverse=True)
    selected_tools = tools_pool[:4]

    # Discussions: HN non-github + Lobsters
    hn_disc = [x for x in hn_pool if "github.com" not in x.get("url", "").lower()]
    hn_disc.sort(key=score_disc, reverse=True)
    lobs_sorted = sorted(lobs_pool, key=score_disc, reverse=True)
    disc_pool = hn_disc + lobs_sorted
    disc_pool.sort(key=score_disc, reverse=True)
    selected_disc = disc_pool[:4]

    # ---- Summarise ----
    def process(item: dict, section: str) -> dict:
        out = dict(item)
        out["section"] = section
        body = extract_body(item["url"])
        out["summary"] = summarise(item["title"], body, item.get("snippet", ""))
        return out

    work: list[tuple[dict, str]] = []
    for it in selected_eng:
        work.append((it, "engineering"))
    for it in selected_pap:
        work.append((it, "papers"))
    for it in selected_tools:
        work.append((it, "tools"))
    for it in selected_disc:
        work.append((it, "discussions"))

    print(f"Summarising {len(work)} stories...")
    enriched: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(process, it, sec): (it, sec) for it, sec in work}
        for f in as_completed(futures):
            try:
                enriched.append(f.result())
            except Exception as e:
                it, sec = futures[f]
                print(f"  process fail {it['title']}: {e}", file=sys.stderr)

    # ---- Group back by section ----
    by_section: dict[str, list[dict]] = {
        "engineering": [], "papers": [], "tools": [], "discussions": [],
    }
    for e in enriched:
        sec = e.pop("section")
        by_section[sec].append(e)

    # Stable ordering within section (preserve selection order)
    for sec, items in by_section.items():
        if sec == "engineering":
            order = selected_eng
        elif sec == "papers":
            order = selected_pap
        elif sec == "tools":
            order = selected_tools
        else:
            order = selected_disc
        pos = {id(x): i for i, x in enumerate(order)}
        items.sort(key=lambda x: pos.get(id(x), 999))

    # ---- Build final JSON ----
    # Drop slots where both summary and snippet are empty — the renderer
    # can't display an empty card, and Cloudflare will cache the bad card.
    # Also drop slots whose summary is just a title echo or a URL (fetch
    # failure with body-extract fallback) — those render as nearly-blank
    # cards too. Real summaries are 100-150 words (>=600 chars at M3's pace).
    def _is_junk_summary(item: dict) -> bool:
        s = (item.get("summary") or "").strip()
        if not s:
            return True
        # Title echo: summary equals (or starts with) the original title
        title = (item.get("title") or "").strip()
        if title and s.lower().startswith(title.lower()[:60]):
            return True
        # URL echo: summary is just a URL pasted in
        if s.startswith("http://") or s.startswith("https://"):
            return True
        # Far too short to be a real summary
        if len(s) < 200:
            return True
        return False

    for sec_items in by_section.values():
        sec_items[:] = [
            x for x in sec_items
            if not _is_junk_summary(x) or len((x.get("snippet") or "").strip()) >= 200
        ]
    total = sum(len(v) for v in by_section.values())
    edition_tag = f"vol-2-no-{34 + (NOW - datetime(2026, 9, 9, tzinfo=timezone.utc)).days}"

    archive = json.load(open(ROOT / "archive.json"))
    archive_no_today = [d for d in archive if d != TODAY]
    prev_day = archive_no_today[0] if archive_no_today else None

    label = NOW.strftime("%A, %B %-d, %Y")

    sections = [
        {
            "name": "engineering",
            "label": "Engineering",
            "intro": "Independent engineering blogs lead with concrete trade-offs and post-mortems rather than product launches.",
            "stories": [
                {
                    "id": f"eng-{i+1:02d}",
                    "title": x["title"],
                    "url": x["url"],
                    "source": x["source"],
                    "source_kind": "blog",
                    "published_at": x.get("published_at", ""),
                    "snippet": x.get("snippet", ""),
                    "summary": x["summary"],
                } for i, x in enumerate(by_section["engineering"])
            ],
        },
        {
            "name": "papers",
            "label": "Papers",
            "intro": "Recent arXiv work across machine learning, programming languages, and applied mathematics — research that informs how engineers build.",
            "stories": [
                {
                    "id": f"pap-{i+1:02d}",
                    "title": x["title"],
                    "url": x["url"],
                    "source": x["source"],
                    "source_kind": "paper",
                    "published_at": x.get("published_at", ""),
                    "snippet": x.get("snippet", ""),
                    "summary": x["summary"],
                } for i, x in enumerate(by_section["papers"])
            ],
        },
        {
            "name": "tools",
            "label": "Tools",
            "intro": "Open-source releases trending on Hacker News — small focused tools and active projects worth a look.",
            "stories": [
                {
                    "id": f"tool-{i+1:02d}",
                    "title": x["title"],
                    "url": x["url"],
                    "source": x["source"],
                    "source_kind": "repo",
                    "published_at": x.get("published_at", ""),
                    "snippet": x.get("snippet", ""),
                    "summary": x["summary"],
                } for i, x in enumerate(by_section["tools"])
            ],
        },
        {
            "name": "discussions",
            "label": "Discussions",
            "intro": "Active threads from Hacker News and Lobsters — the meta-conversation around this week's tooling and craft.",
            "stories": [
                {
                    "id": f"disc-{i+1:02d}",
                    "title": x["title"],
                    "url": x["url"],
                    "source": x["source"],
                    "source_kind": "discussion" if x["source_kind"] == "lobsters" else "blog",
                    "published_at": x.get("published_at", ""),
                    "snippet": x.get("snippet", ""),
                    "summary": x["summary"],
                } for i, x in enumerate(by_section["discussions"])
            ],
        },
    ]

    digest = {
        "date": TODAY,
        "label": label,
        "edition_tag": edition_tag,
        "total": total,
        "prev_day": prev_day,
        "next_day": None,
        "site": {
            "name": "Daily Byte",
            "tagline": "A byte-size daily digest for engineers who value depth over noise.",
            "base_url": "https://learn.shenthar.me",
            "repo": "https://github.com/KTS-o7/learning-portal",
        },
        "sections": sections,
    }

    if total == 0:
        print("ERROR: zero stories selected", file=sys.stderr)
        sys.exit(1)

    # ---- Write artifacts ----
    out_data = ROOT / "data" / f"{TODAY}.json"
    out_data.write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    (ROOT / "data" / "latest.json").write_text(json.dumps(digest, indent=2, ensure_ascii=False))

    # RSS
    subprocess.check_call(["python3", str(ROOT / "scripts" / "build_rss.py"), TODAY],
                          cwd=ROOT)

    # Archive snapshot
    subprocess.check_call(["python3", str(ROOT / "scripts" / "build_snapshot.py"), TODAY],
                          cwd=ROOT)

    # Archive.json
    new_archive = [TODAY] + archive_no_today
    (ROOT / "archive.json").write_text(json.dumps(new_archive, indent=2) + "\n")

    print(f"OK {TODAY} total={total}")


if __name__ == "__main__":
    main()