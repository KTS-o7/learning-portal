#!/usr/bin/env python3
"""
Daily Byte — personal learning digest generator.

Fetches high-signal tech/CS/AI content from free sources (HN, arXiv, Lobsters,
engineering blogs), dedupes + scores, optionally curates byte-size summaries via
Kimi (OpenAI-compatible), and renders a static newspaper-style HTML edition.

Outputs (into OUT_DIR):
  index.html                 today's edition (served at /)
  archive/YYYY-MM-DD.html    self-contained per-day edition
  archive.json               list of all editions
  rss.xml                    per-item RSS of the latest edition

No secrets in this repo: KIMI_API_KEY is read from env only.
"""

import argparse
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
SITE_NAME = "Daily Byte"
SITE_TAGLINE = "A byte-size daily digest for engineers who value depth over noise."
BASE_URL = "https://learn.shenthar.me"
HOME_REPO = "https://github.com/KTS-o7/learning-portal"
OUT_DIR = Path(__file__).resolve().parent

IST = timezone(timedelta(hours=5, minutes=30))
FETCH_WINDOW_H = 26          # look back this far for "today's" items
HN_MIN_POINTS = 120          # HN stories must clear this to be considered
HN_ASK_MIN = 40
HN_SHOW_MIN = 40
LOBSTERS_MIN_SCORE = 30
ARXIV_MAX = 25
TIMEOUT = 20

# Per-section caps — a personal digest should stay ~25-30 stories, not 50+.
SECTION_CAPS = {"engineering": 5, "papers": 5, "tools": 4, "discussions": 4}
BLOG_PER_FEED = 4            # max entries taken from each blog feed
MAX_BLOG_AGE_H = 168         # skip blog posts older than 7 days (quiet feeds
                             # would otherwise recycle stale posts daily)

# Engineering blog feeds (title, url, feed). Extend freely.
BLOG_FEEDS = [
    ("Go Blog", "https://go.dev/blog/feed.atom"),
    ("Rust Blog", "https://blog.rust-lang.org/feed.xml"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("Arpit Bhayani", "https://arpitbhayani.me/rss.xml"),
    ("Julia Evans", "https://jvns.ca/atom.xml"),
    ("Sean Goedecke", "https://www.seangoedecke.com/rss.xml"),
]

UA = "Mozilla/5.0 (compatible; DailyByte/1.0; +https://learn.shenthar.me)"


def log(msg):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {"User-Agent": UA})
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r


def clean(text, limit=None):
    """Strip HTML tags/whitespace, collapse spaces, optionally truncate."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def to_ts(value, default_ts=None):
    """Normalize a timestamp (epoch int or ISO string) to epoch seconds."""
    if default_ts is None:
        default_ts = time.time()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            pass
    return float(default_ts)


def esc(s):
    return html.escape(str(s or ""), quote=True)


# ----------------------------------------------------------------------------
# Source fetchers — each returns a list of dicts:
#   {title, url, discuss_url, source, kind, points, snippet, age_h}
# ----------------------------------------------------------------------------
def fetch_hn():
    items = []
    now = int(time.time())
    since = now - FETCH_WINDOW_H * 3600
    queries = [
        ("story", f"tags=story&numericFilters=points>{HN_MIN_POINTS},created_at_i>{since}",
         "story"),
        ("ask_hn", f"tags=ask_hn&numericFilters=points>{HN_ASK_MIN},created_at_i>{since}",
         "discussion"),
        ("show_hn", f"tags=show_hn&numericFilters=points>{HN_SHOW_MIN},created_at_i>{since}",
         "discussion"),
    ]
    for tag, params, kind in queries:
        try:
            r = get(
                f"https://hn.algolia.com/api/v1/search?{params}&hitsPerPage=25"
            ).json()
        except Exception as e:
            log(f"  hn/{tag} failed: {e}")
            continue
        for h in r.get("hits", []):
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            items.append({
                "title": clean(h.get("title") or h.get("story_title")),
                "url": url,
                "discuss_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "source": "HN",
                "kind": "discussion" if kind == "discussion" else "story",
                "points": h.get("points") or 0,
                "snippet": "",
                "text": h.get("story_text") or "",
                "age_h": (now - h.get("created_at_i", now)) / 3600,
                "tag": tag,
            })
    enrich_hn_snippets(items)
    return items


def fetch_lobsters():
    items = []
    now = int(time.time())
    try:
        data = get("https://lobste.rs/hottest.json").json()
    except Exception as e:
        log(f"  lobsters failed: {e}")
        return items
    for s in data:
        pts = s.get("score") or 0
        if pts < LOBSTERS_MIN_SCORE:
            continue
        items.append({
            "title": clean(s.get("title")),
            "url": s.get("url") or f"https://lobste.rs/s/{s.get('short_id')}",
            "discuss_url": f"https://lobste.rs/s/{s.get('short_id')}",
            "source": "Lobsters",
            "kind": "discussion",
            "points": pts,
            "snippet": clean(s.get("description"), 4000),
            "age_h": max(0, (now - to_ts(s.get("created_at"), now)) / 3600),
            "tag": "lobsters",
        })
    return items


def fetch_arxiv():
    items = []
    now = int(time.time())
    cats = "cat:cs.AI OR cat:cs.SE OR cat:cs.LG OR cat:cs.OS OR cat:cs.DB OR cat:cs.CR OR cat:cs.PL"
    url = (
        "http://export.arxiv.org/api/query?"
        f"search_query={requests.utils.quote(cats)}"
        "&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={ARXIV_MAX}"
    )
    try:
        r = get(url)
        root = ET.fromstring(r.text)
    except Exception as e:
        log(f"  arxiv failed: {e}")
        return items
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall("a:entry", ns):
        try:
            published = e.find("a:published", ns).text
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            age_h = (now - dt.timestamp()) / 3600
            if age_h > FETCH_WINDOW_H:
                continue
            title = clean(e.find("a:title", ns).text, 160)
            summary = clean(e.find("a:summary", ns).text, 4000)
            link = e.find("a:id", ns).text
            items.append({
                "title": title,
                "url": link,
                "discuss_url": "",
                "source": "arXiv",
                "kind": "paper",
                "points": 0,
                "snippet": summary,
                "age_h": age_h,
                "tag": "paper",
            })
        except Exception:
            continue
    return items


def fetch_blogs():
    items = []
    now = int(time.time())
    for name, feed in BLOG_FEEDS:
        try:
            r = get(feed)
            root = ET.fromstring(r.text)
        except Exception as e:
            log(f"  blog {name} failed: {e}")
            continue
        entries = []
        if root.tag.endswith("feed"):  # Atom
            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        else:  # RSS 2.0
            entries = root.findall(".//item")
        for en in entries[:BLOG_PER_FEED]:
            try:
                title = clean(en.find("{http://www.w3.org/2005/Atom}title").text
                              if en.find("{http://www.w3.org/2005/Atom}title") is not None
                              else en.find("title").text, 160)
                link_el = en.find("{http://www.w3.org/2005/Atom}link")
                url = link_el.get("href") if link_el is not None else clean(en.find("link").text)
                pub = en.find("{http://www.w3.org/2005/Atom}published")
                if pub is None:
                    pub = en.find("{http://www.w3.org/2005/Atom}updated")
                if pub is None:
                    pub = en.find("pubDate")
                snippet = en.find("{http://www.w3.org/2005/Atom}summary")
                if snippet is None:
                    snippet = en.find("{http://www.w3.org/2005/Atom}content")
                if snippet is None:
                    snippet = en.find("description")
                if snippet is not None and snippet.text:
                    snippet = clean(snippet.text, 4000)
                age_h = None
                if pub is not None and pub.text:
                    try:
                        dt = datetime.fromisoformat(
                            pub.text.replace("Z", "+00:00"))
                        age_h = (now - dt.timestamp()) / 3600
                    except Exception:
                        try:
                            dt = datetime.strptime(
                                pub.text, "%a, %d %b %Y %H:%M:%S %z")
                            age_h = (now - dt.timestamp()) / 3600
                        except Exception:
                            age_h = None
                if not title or not url:
                    continue
                # Skip stale posts from quiet feeds.
                if age_h is not None and age_h > MAX_BLOG_AGE_H:
                    continue
                items.append({
                    "title": title,
                    "url": url,
                    "discuss_url": "",
                    "source": name,
                    "kind": "blog",
                    "points": 0,
                    "snippet": snippet or "",
                    "age_h": age_h,
                    "tag": "blog",
                })
            except Exception:
                continue
    return items


# ----------------------------------------------------------------------------
# Curation
# ----------------------------------------------------------------------------
def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = re.sub(r"[^a-z0-9]+", "", (it["title"] or "").lower())[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def assemble(items):
    """Bucket items into sections, apply per-section caps.

    Section order (and order in render): engineering -> papers -> tools ->
    discussions. High-signal engineering blogs first so the digest opens
    with depth; arXiv papers and tool releases follow; HN/Lobsters threads
    close it out so chatter doesn't dominate the page.

    For engineering (blog) items we apply a quality score that rewards
    recognized independent voices and penalises vendor marketing blogs
    (Cloudflare-style "announcing X" posts), so the slot is filled by
    writing from people, not press releases.
    """
    blogs = [i for i in items if i["kind"] == "blog"]
    # Score blogs: prefer independent voices over vendor blogs.
    vendor_markers = ("announcing ", "now generally available",
                      "introducing ", "is now available")
    for b in blogs:
        title_lc = b["title"].lower()
        b["blog_score"] = 0
        # Higher base score for independent voices
        if b["source"] in ("Arpit Bhayani", "Julia Evans", "Sean Goedecke"):
            b["blog_score"] += 10
        elif b["source"] in ("Go Blog", "Rust Blog"):
            b["blog_score"] += 8
        else:
            b["blog_score"] += 2
        # Penalise vendor marketing language
        if any(m in title_lc for m in vendor_markers):
            b["blog_score"] -= 5
        # Reward recency (last 24h gets a boost)
        if b.get("age_h") is not None and b["age_h"] < 24:
            b["blog_score"] += 3

    # Per-source diversity cap: no single blog may dominate the section,
    # even when it has multiple high-scoring posts in one feed (otherwise
    # the digest opens with one author four days in a row when they post).
    cap = SECTION_CAPS["engineering"]
    max_per_source = max(1, cap - 2)   # if cap is 5, max 3 posts per source
    selected = []
    seen_sources = {}
    for b in sorted(blogs, key=lambda i: -i["blog_score"]):
        src = b["source"]
        if seen_sources.get(src, 0) >= max_per_source:
            continue
        selected.append(b)
        seen_sources[src] = seen_sources.get(src, 0) + 1
        if len(selected) >= cap:
            break

    sections = {
        "engineering": selected,
        "papers": [i for i in items if i["kind"] == "paper"][:SECTION_CAPS["papers"]],
        "tools": [i for i in items if i["tag"] == "story"
                  and "github.com" in (i["url"] or "")][:SECTION_CAPS["tools"]],
        "discussions": [i for i in items if i["kind"] == "discussion"][:SECTION_CAPS["discussions"]],
    }
    return sections


# ----------------------------------------------------------------------------
# LLM curation pass (byte-size "$-100" summaries). Enabled with --curate.
#
# Uses the SAME model Hermes runs by default (provider opencode-zen,
# model deepseek-v4-flash-free). Override via env:
#   LLM_URL / LLM_API_KEY / LLM_MODEL   (OpenAI-compatible chat completions)
# Falls back to KIMI_API_KEY if OPENCODE_ZEN_API_KEY is unset.
# ----------------------------------------------------------------------------
DEFAULT_LLM_URL = "https://opencode.ai/zen/v1/chat/completions"
DEFAULT_LLM_MODEL = "deepseek-v4-flash-free"

# Free models on opencode-zen — batches run across these IN PARALLEL and fall
# over between them, giving speed + resilience to flaky free tiers. Ordered
# by observed reliability; deepseek-free is notoriously empty/5xx-heavy so it
# is the last resort.
FREE_MODELS = [
    "mimo-v2.5-free",
    "ling-3.0-flash-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
    "big-pickle",
    "deepseek-v4-flash-free",
]


def _llm_key():
    return (os.environ.get("MINIMAX_API_KEY")
            or os.environ.get("OPENCODE_ZEN_API_KEY")
            or os.environ.get("KIMI_API_KEY"))


def _llm_endpoint():
    """Return (url, key, model). Prefer Hermes's default model (MINIMAX/M3 via
    api.minimax.io); fall back to opencode-zen (free), then to Kimi. Explicit
    LLM_URL/LLM_API_KEY/LLM_MODEL env override everything."""
    if os.environ.get("LLM_URL") or os.environ.get("LLM_API_KEY") or os.environ.get("LLM_MODEL"):
        return (
            os.environ.get("LLM_URL", DEFAULT_LLM_URL),
            os.environ.get("LLM_API_KEY") or _llm_key(),
            os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL),
        )
    if os.environ.get("MINIMAX_API_KEY"):
        return "https://api.minimax.io/v1/chat/completions", os.environ["MINIMAX_API_KEY"], "MiniMax-M3"
    if os.environ.get("OPENCODE_ZEN_API_KEY"):
        return DEFAULT_LLM_URL, os.environ["OPENCODE_ZEN_API_KEY"], DEFAULT_LLM_MODEL
    if os.environ.get("KIMI_API_KEY"):
        return ("https://api.moonshot.cn/v1/chat/completions",
                os.environ["KIMI_API_KEY"], "moonshot-v1-8k")
    return None, None, None


STE100_RULES = """
Write each summary in STRICT ASD-STE100 (Simplified Technical English), the
standard used in aerospace maintenance manuals. STE100 reads CLEAR and SIMPLE,
never robotic: short declarative sentences, everyday technical words, active
voice. If a sentence sounds stiff, tangled, or machine-generated, rewrite it.

HARD RULES:
- 3-5 sentences per item, 60-90 words (never more than 100). Each sentence
  carries ONE idea and stays under 20 words.
- Active voice, simple present tense. Say "SQLite validates the data", not
  "validation is performed by SQLite". Avoid gerund-stacking: write "the
  engineer delegates the task" instead of "delegation by the engineer".
- Plain vocabulary: use the common word (use, check, show), not the fancy one
  (utilize, verify, demonstrate). Use the same term for the same thing.
- Start with the subject or the finding. NEVER start with "This article",
  "This post", "This paper", or any container reference.
- Use ONLY facts from the snippet/abstract. Never invent numbers, names,
  quotes, or claims. If the snippet has no facts, return "summary": "".
- No marketing words (amazing, must-read, huge), no exclamation marks, no
  hedging (maybe, seems, trailing question marks).

STRUCTURE: sentence 1 states what the item is or what happened. The middle
sentences carry the gist: the main claim, key findings, and notable numbers
from the snippet, in a natural order. The final sentence gives the takeaway
or why it matters to an engineer. Aim to convey about 90% of the article's
substance in under 100 words.

EXAMPLES — study the tone: simple, natural, factual. Facts below come from
the real page descriptions.
BAD (robotic, gerund-stacked): "LLMs enable non-experts to write acceptable
    CSS by delegation, reducing reliance on skilled colleagues for technical
    gaps."
GOOD: "In the 2010s, people without CSS skills asked a colleague or searched
    the web for answers. Today they delegate the task to an LLM and get
    acceptable results in seconds."

BAD (title echo): "This paper introduces a framework for power-systems AI
    education."
GOOD: "Most AI-for-power-systems material targets specialists and gives
    newcomers little to work with. This paper offers reusable courseware that
    runs labs in the browser, with no GPU setup needed."

Output STRICT JSON only: an array of objects {"title": <exact item title,
copied verbatim>, "summary": "<your STE100 text>"}, one per input item, in
the same order. No markdown, no extra text.
"""


CACHE_FILE = OUT_DIR / ".cache_summaries.json"
META_FILE = OUT_DIR / ".meta_cache.json"


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def _fetch_article_body(url, timeout=15, max_chars=12000):
    """Fetch the article's main prose: prefer <article> or <main>; fall back
    to all <p> paragraphs. Returns clean text, or \"\"."""
    if not url or not url.startswith("http"):
        return ""
    try:
        r = get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.text:
            return ""
    except Exception:
        return ""
    body = r.text
    candidates = []
    for sel in (
        r"<article[^>]*>(.*?)</article>",
        r"<main[^>]*>(.*?)</main>",
    ):
        m = re.search(sel, body, re.S | re.I)
        if m:
            candidates.append(m.group(1))
    section = max(candidates, key=len) if candidates else body
    section = re.sub(r"<script.*?</script>", "", section, flags=re.S | re.I)
    section = re.sub(r"<style.*?</style>", "", section, flags=re.S | re.I)
    section = re.sub(r"<nav.*?</nav>", "", section, flags=re.S | re.I)
    section = re.sub(r"<header.*?</header>", "", section, flags=re.S | re.I)
    section = re.sub(r"<footer.*?</footer>", "", section, flags=re.S | re.I)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", section, flags=re.S | re.I)
    text = " ".join(paras) if paras else re.sub(r"<[^>]+>", " ", section)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) < 200:
        return ""
    if _is_junk_description(text):
        return ""
    return text[:max_chars]


def _fetch_og(url, timeout=10):
    """Extract a page's meta description (og:description / description / <p>)."""
    try:
        r = get(url, timeout=timeout)
        if r.status_code != 200:
            return ""
    except Exception:
        return ""
    body = r.text or ""
    pats = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        r'<p[^>]*>([^<]{60,320})',
    ]
    for p in pats:
        m = re.search(p, body, re.I)
        if m:
            return clean(html.unescape(m.group(1)), 4000) or ""
    return ""


def _is_junk_description(d):
    if not d:
        return True
    low = d.lower()
    junk = ("contribute to", "development by creating an account",
            "mirror of https://", "this repository", "github repositories",
            '<svg', "javascript:", "the age of personalized software")
    return any(j in low for j in junk)


def enrich_hn_snippets(items):
    """Give HN items real page context; otherwise the LLM sees only a URL."""
    meta = _load_json(META_FILE, {})
    dirty = False
    targets = [it for it in items if it["source"] == "HN" and not it["snippet"]
               and it["url"].startswith("http")]

    def enrich(it):
        nonlocal dirty
        # Ask/Show HN stick with the Algolia body text (fast + accurate)
        if it.get("text"):
            it["snippet"] = clean(it["text"], 4000)
            return
        u = it["url"]
        hit = meta.get(u)
        if hit and isinstance(hit, dict) and hit.get("d"):
            it["snippet"] = "" if _is_junk_description(hit["d"]) else hit["d"]
            return
        if "news.ycombinator.com" in u:   # comment pages have no og:description
            return
        d = _fetch_og(u)
        if d and not _is_junk_description(d):
            meta[u] = {"d": d, "t": int(time.time())}
            it["snippet"] = d
            dirty = True

    if targets:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(enrich, targets))
        # prune entries older than 30 days so the cache stays bounded
        cutoff = time.time() - 30 * 86400
        stale = [k for k, v in meta.items()
                 if isinstance(v, dict) and v.get("t", 0) < cutoff]
        for k in stale:
            meta.pop(k, None)
        if dirty or stale:
            try:
                META_FILE.write_text(json.dumps(meta))
            except Exception as e:
                log(f"  meta cache write failed: {e}")


def _item_key(it):
    return re.sub(r"[^a-z0-9]+", "", (it.get("title") or "").lower())[:64]


def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _request_summaries(url, key, model, prompt):
    """One LLM call → list of {i, summary}. Empty list on any failure."""
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.2, "max_tokens": 8192},
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        got = json.loads(m.group(0))
    except Exception:
        return []
    return got if isinstance(got, list) else []


def _curate_batch(url, key, models, prompt):
    """Try models in order until one returns a list with real summaries."""
    for model in models:
        try:
            out = _request_summaries(url, key, model, prompt)
            if out and any((s.get("summary") or "").strip() for s in out):
                return out, model
            log(f"    {model}: no usable summaries")
        except Exception as e:
            log(f"    {model}: failed ({e})")
        time.sleep(1.5)
    return [], None


def curate_with_kimi(items, edition):
    """Per-article fetch + summarize — one LLM call per item, returns prose.

    Modeled on tdd.cat: fetch the full article body, send it to the LLM,
    get back a 4-6 sentence prose summary (no JSON). Minimizes parsed-shape
    complexity (no regex over JSON, no positional mapping); tolerates LLMs
    returning short fields without truncation-induced failure."""
    url, key, primary = _llm_endpoint()
    if not url or not key:
        log("  no LLM endpoint configured - skipping curation")
        return items
    if url.startswith("https://opencode.ai"):
        models = FREE_MODELS
        if primary not in models:
            models = [primary] + models
    else:
        models = [primary]
    models = [m for m in models if m]
    log(f"  curating one-article-at-a-time via {models[0]} (fallback pool of {len(models)})")

    cache = _load_cache()
    fresh, cached = [], 0
    for it in items:
        k = _item_key(it)
        if k in cache and cache[k]:
            it["snippet"] = "" if _is_junk_description(cache[k]) else cache[k]
            cached += 1
        else:
            fresh.append(it)
    if cached:
        log(f"  reused {cached} cached summaries; curating {len(fresh)} new")

    def _one_article_prompt(it, body):
        # Direct, single-shot ask: no planning scaffold (invites thinking).
        # Match tdd.cat: 100-150 words, 4-6 sentences, prose only.
        return (
            f"Title: {it['title']}\n\n"
            f"{body[:10000]}\n\n"
            "Summarize in 4-6 sentences (100-150 words). Use only facts "
            "from the article. Plain prose, no lists or headings. "
            "Don't start with 'This article'."
        )

    def _summarize_one(it):
        body = _fetch_article_body(it["url"]) if it.get("url") else ""
        if not body:
            body = (it.get("snippet") or "").strip()
        if not body:
            return it, "", "no-content"
        prompt = _one_article_prompt(it, body)
        for model in models:
            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 600, "temperature": 0.2,
                          "reasoning_split": True},
                    timeout=120,
                )
                r.raise_for_status()
                text = (r.json()["choices"][0]["message"]
                        .get("content") or "").strip()
                text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text,
                              flags=re.M).strip()
                # Strip <think>.../think blocks (MiniMax-M3 leaks these
                # into content occasionally).
                text = re.sub(r"<think>.*?</think>", "", text,
                              flags=re.S).strip()
                if len(text) >= 80:
                    return it, text, model
            except Exception as e:
                log(f"    {model}: {e}")
            time.sleep(0.5)
        return it, "", "all-failed"

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_summarize_one, it): it for it in fresh}
        for f in as_completed(futs):
            it, summary, used = f.result()
            if summary:
                it["snippet"] = summary
                cache[_item_key(it)] = summary
                ok += 1
                log(f"  ok {it['title'][:60]} via {used}")
            else:
                fail += 1
                log(f"  miss {it['title'][:60]} ({used})")

    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    except Exception as e:
        log(f"  cache write failed: {e}")
    log(f"  curation complete: {ok} summarized, {fail} left as source text")
    return items


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
CSS = """
/* ---------- Daily Byte: newspaper edition ---------- */
:root{
  --paper:#fbf1c7; --paper2:#f9f5d7; --rule:#d8c9a8; --ink:#3c3836;
  --mut:#504945; --mut2:#79756b; --ac:#b57614; --ln:#076678;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --pad:clamp(14px,5vw,26px);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);
font-family:var(--sans);font-size:16px;line-height:1.55;font-weight:400;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--ln);text-decoration:none;cursor:pointer}
a:hover{text-decoration:underline}
.wrap{max-width:920px;margin:0 auto;padding:0 22px}
button{cursor:pointer;font-family:var(--sans)}

/* top chrome */
.chrome{position:sticky;top:0;z-index:50;background:var(--paper);
border-bottom:1px solid var(--rule);backdrop-filter:blur(6px)}
.chrome-in{max-width:920px;margin:0 auto;padding:9px var(--pad);
display:flex;align-items:center;justify-content:space-between;gap:12px;
font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2)}
.chrome nav{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.chrome nav a{color:var(--mut);font-weight:600}
.chrome nav a:hover{color:var(--ln);text-decoration:none}
.chrome .left,.chrome .right,.chrome .nav{display:flex;gap:14px;align-items:center;white-space:nowrap}
.chrome .nav{gap:8px;color:var(--mut);font-weight:600}
.chrome .nav .d{color:var(--ink);font-weight:600;letter-spacing:.04em;text-transform:none;font-size:12px}
.chrome .nav-arrow{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
border:1px solid var(--rule);border-radius:50%;color:var(--mut);background:var(--paper2)}
.chrome .nav-arrow:hover{color:var(--paper);background:var(--ink);border-color:var(--ink);text-decoration:none}
.chrome .nav-arrow.disabled{opacity:.3;pointer-events:none}
.chrome .count{color:var(--ac);font-weight:700}

/* masthead / nameplate */
.mast{text-align:center;padding:40px var(--pad) 24px}
.mast h1{margin:0;font-family:var(--serif);font-weight:700;font-size:clamp(46px,9vw,82px);
letter-spacing:.5px;line-height:1}
.mast h1 .dot{color:var(--ac)}
.mast .tag{color:var(--mut);font-style:italic;font-family:var(--serif);
font-size:19px;margin-top:8px}
.mast .edition{color:var(--mut2);font-size:12px;letter-spacing:.22em;
text-transform:uppercase;margin-top:14px}
.nameplate{display:flex;align-items:center;gap:14px;margin:20px auto 0;max-width:560px}
.nameplate:before,.nameplate:after{content:"";flex:1;height:1px;background:var(--rule)}
.nameplate span{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--mut2)}

main{max-width:920px;margin:0 auto;padding:0 var(--pad)}
.story{padding:20px 0;border-bottom:1px solid var(--rule)}
.story:last-of-type{border-bottom:0}
.story .lbl{font-size:10.5px;letter-spacing:.22em;text-transform:uppercase;
color:var(--ac);font-weight:700;margin-bottom:6px}
.story .acts{display:flex;gap:16px;margin-bottom:7px;font-size:11px;
letter-spacing:.14em;text-transform:uppercase}
.story .acts a{color:var(--ln);font-weight:700}
.story h2{margin:0 0 7px;font-size:clamp(20px,2.6vw,26px);line-height:1.22;
font-weight:700;font-family:var(--serif);letter-spacing:.2px}
.story h2 a{color:var(--ink)}
.story h2 a:hover{color:var(--ln);text-decoration:none}
.story .sum{color:var(--mut);font-size:15px}
.story .smeta{margin-top:9px;font-size:12px;color:var(--mut2);
display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.pill{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.08em;
text-transform:uppercase;padding:2px 8px;border-radius:10px}
.p-HN{color:#9d0006;background:rgba(220,50,47,.09);border:1px solid rgba(220,50,47,.22)}
.p-Lobsters{color:#8f3f97;background:rgba(179,70,110,.09);border:1px solid rgba(179,70,110,.22)}
.p-arxiv{color:#076678;background:rgba(7,102,120,.09);border:1px solid rgba(7,102,120,.22)}
.p-blog{color:#427b58;background:rgba(66,123,88,.09);border:1px solid rgba(66,123,88,.22)}
.p-tool{color:#79740e;background:rgba(121,116,14,.09);border:1px solid rgba(121,116,14,.22)}
.story .smeta .pts{color:var(--ac);font-weight:600}

/* footer */
.foot{margin:40px 0 34px;text-align:center;color:var(--mut2);font-size:12.5px;
letter-spacing:.04em}
.foot a{color:var(--mut);text-decoration:underline}
.foot .ln{display:inline-block;margin:0 8px}

/* ---------- mobile ---------- */
.foot{margin:36px 0 30px;padding:0 var(--pad);text-align:center;
color:var(--mut2);font-size:12.5px;letter-spacing:.04em}
@media (max-width:640px){
  .chrome-in{flex-wrap:wrap;row-gap:4px}
  .chrome .right{flex:1;justify-content:flex-end}
  .chrome .nav{flex:1;justify-content:center;order:3}    /* on phones, push nav below the link row */
  .chrome .nav .d{font-size:11px}
  .chrome .count{font-size:10px}
  .mast{padding:30px var(--pad) 18px}
  .mast h1{font-size:clamp(38px,13vw,56px)}
  .mast .tag{font-size:16px}
  .mast .edition{letter-spacing:.14em}
  .story{padding:16px 0}
  .story h2{font-size:19.5px;line-height:1.3}
  .story .sum{font-size:14.5px}
  .story .acts{gap:14px}
  .story .smeta{font-size:11.5px;gap:10px}
  .nameplate{margin:22px auto 0}
}
"""

SCRIPT = ""


def item_html(it, label=None):
    src = it["source"]
    cls = {"HN": "p-HN", "Lobsters": "p-Lobsters", "arXiv": "p-arxiv"}.get(
        src, "p-blog" if it["kind"] == "blog" else "p-tool")
    acts = ""
    if it.get("url"):
        acts += f'<a href="{esc(it["url"])}">Read ›</a>'
    if it.get("discuss_url"):
        acts += f'<a href="{esc(it["discuss_url"])}">Discuss ›</a>'
    label_html = f'<div class="lbl">• {esc(label)}</div>' if label else ""
    pts = f'<span class="pts">{it["points"]} pts</span>' if it["points"] else ""
    age = (f'{it["age_h"]:.0f}h ago'
           if it.get("age_h") is not None and it["age_h"] >= 0 else "")
    sum_html = (f'<div class="sum">{esc(it["snippet"])}</div>'
                if it.get("snippet") else "")
    target = it.get("url") or it.get("discuss_url") or "#"
    return f"""<article class="story">
  {label_html}
  <div class="acts">{acts}</div>
  <h2><a href="{esc(target)}">{esc(it['title'])}</a></h2>
  {sum_html}
  <div class="smeta"><span class="pill {cls}">{esc(src)}</span>{pts}
  {f'<span>{age}</span>' if age else ''}</div>
</article>"""


def render_page(edition, sections, all_days):
    # Build a continuous newspaper stream with thin section dividers.
    stream = []
    sections_order = [
        ("engineering", "ENGINEERING"),
        ("papers", "PAPERS"),
        ("tools", "TOOLS & PROJECTS"),
        ("discussions", "DISCUSSIONS"),
    ]
    first = True
    for key, sec_label in sections_order:
        items = sections.get(key) or []
        if not items:
            continue
        if not first:
            stream.append(
                f'<div class="nameplate" style="margin:30px auto"><span>'
                f'{sec_label}</span></div>')
        first = False
        for i, it in enumerate(items):
            lb = "Top story" if (key == "engineering" and i == 0) else None
            stream.append(item_html(it, lb))

    body = "\n".join(stream)

    # Build prev/next navigation from the archive list (descending).
    # all_days is sorted newest-first; current edition sits at index 0.
    past_days = [d for d in all_days if d != edition]
    prev_day = None
    next_day = None
    if edition in all_days:
        idx = all_days.index(edition)
        if idx + 1 < len(all_days):
            prev_day = all_days[idx + 1]   # older (newest-first ordering)
        if idx > 0:
            next_day = all_days[idx - 1]   # newer
    # Today is the newest; if next_day exists it points to it (today == edition).
    # After cron runs once per day, the live page is always the newest -> next_day = None.

    arrow_svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" '
                 'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="2.5" stroke-linecap="round" '
                 'stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>')
    arrow_svg_r = ('<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" '
                   'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                   'stroke-width="2.5" stroke-linecap="round" '
                   'stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>')
    prev_btn = (f'<a class="nav-arrow" href="{BASE_URL}/{prev_day}" '
                f'aria-label="Previous edition ({prev_day})">'
                f'{arrow_svg}</a>') if prev_day else (
                f'<span class="nav-arrow disabled" aria-hidden="true">'
                f'{arrow_svg}</span>')
    next_btn = (f'<a class="nav-arrow" href="{BASE_URL}/{next_day}" '
                f'aria-label="Next edition ({next_day})">'
                f'{arrow_svg_r}</a>') if next_day else (
                f'<span class="nav-arrow disabled" aria-hidden="true">'
                f'{arrow_svg_r}</span>')

    archive_links = "".join(
        f'<a style="margin:0 10px" href="{BASE_URL}/{d}">{d}</a>'
        for d in past_days)
    archive_html = (f'<div style="margin-top:14px">📚 Past editions:{archive_links}</div>'
                    if archive_links else "")

    total = sum(len(v) for v in sections.values())
    edition_label = datetime.strptime(edition, "%Y-%m-%d").strftime("%A, %B %-d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_NAME)} — {edition}</title>
<meta name="description" content="{esc(SITE_TAGLINE)}">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_NAME)}"
      href="{BASE_URL}/rss.xml">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📚</text></svg>">
<style>{CSS}</style>
</head>
<body>
<div class="chrome"><div class="chrome-in">
  <div class="left"><a href="{BASE_URL}/"><strong>{esc(SITE_NAME)}</strong></a>
    <a href="{HOME_REPO}">Source</a><a href="{BASE_URL}/archive/">Archive</a>
    <a href="{BASE_URL}/rss.xml">RSS</a></div>
  <div class="nav">{prev_btn}<span class="d">{edition_label}</span>{next_btn}</div>
  <div class="right"><span class="count">{total} STORIES</span></div>
</div></div>

<header class="mast">
  <h1>{esc(SITE_NAME)}<span class="dot">.</span></h1>
  <div class="tag">{esc(SITE_TAGLINE)}</div>
  <div class="edition">{edition_label} · Edition {edition}</div>
  <div class="nameplate"><span>Engineering</span></div>
</header>

<main>
  {body}
</main>

<footer class="foot">
  <div>{archive_html}</div>
  <div style="margin-top:18px">Curated daily by your <strong>Hermes</strong> agent ·
     <a href="{HOME_REPO}">open source</a> · served from learn.shenthar.me<br>
     Sources: Hacker News · arXiv · Lobsters · engineering blogs</div>
  <div style="margin-top:8px"><a href="{BASE_URL}/rss.xml">Subscribe via RSS</a></div>
</footer>

</body>
</html>"""


def render_rss(edition, sections):
    items = []
    for key in ("engineering", "papers", "tools", "discussions"):
        items.extend(sections.get(key) or [])
    entries = ""
    for it in items[:40]:
        entries += f"""<item>
  <title>{esc(it['title'])}</title>
  <link>{esc(it['url'])}</link>
  <guid isPermaLink="false">{esc(it['url'])}</guid>
  <description>{esc(it.get('snippet') or '')}</description>
  <pubDate>{datetime.now(IST).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>
  <source>{esc(it['source'])}</source>
</item>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>{esc(SITE_NAME)}</title>
  <link>{BASE_URL}/</link>
  <description>{esc(SITE_TAGLINE)} — edition {edition}</description>
{entries}
</channel></rss>"""


def render_archive_index(all_days):
    """Generate /archive/index.html — a single-page list of every edition.

    Reads each archive/<day>.html, extracts the edition label + count, and
    presents them newest-first. Lets visitors jump to any past edition
    without needing to paginate through them sequentially.
    """
    rows = []
    for d in sorted(all_days, reverse=True):
        path = OUT_DIR / "archive" / f"{d}.html"
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except Exception:
            continue
        # Pull edition label and story count from the existing HTML.
        m_label = re.search(r'<div class="edition">([^<·]+)·', text)
        m_count = re.search(r'<span class="count">(\d+)\s*STORIES</span>', text)
        label = m_label.group(1).strip() if m_label else d
        count = m_count.group(1) if m_count else "?"
        # Featured (first story under Engineering) for that day, if any.
        m_title = re.search(r'<h2><a[^>]*>([^<]+)</a></h2>', text)
        lead = m_title.group(1) if m_title else ""
        rows.append(
            f'<li><a href="{BASE_URL}/{d}"><span class="d">{esc(d)}</span>'
            f'<span class="lbl">{esc(label)}</span>'
            f'<span class="cnt">{count} stories</span>'
            f'<span class="lead">{esc(lead[:80])}</span></a></li>'
        )

    body = "\n".join(rows) or "<li>No editions yet.</li>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(SITE_NAME)} — Archive</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Past editions of {esc(SITE_NAME)}.">
<link rel="canonical" href="{BASE_URL}/archive/">
<style>
  body{{margin:0;background:var(--paper,#fbf1c7);color:var(--ink,#3c3836);
    font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    font-size:16px;line-height:1.55}}
  main{{max-width:680px;margin:40px auto;padding:0 24px}}
  h1{{font-size:24px;letter-spacing:.04em;margin:0 0 6px}}
  .sub{{color:#79756b;font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin:0 0 24px}}
  ul{{list-style:none;padding:0;margin:0}}
  li{{border-top:1px solid #d8c9a8;padding:14px 0}}
  li:last-child{{border-bottom:1px solid #d8c9a8}}
  li a{{display:grid;grid-template-columns:90px 1fr 90px;gap:14px;align-items:baseline;
    color:#3c3836;text-decoration:none}}
  li a:hover .d{{color:#076678}}
  .d{{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
    font-weight:700;font-size:18px;color:#b57614}}
  .lbl{{font-size:14px}}
  .cnt{{font-size:11px;color:#79756b;text-align:right;letter-spacing:.06em;text-transform:uppercase}}
  .lead{{grid-column:2/4;font-size:13px;color:#79756b;font-style:italic;margin-top:4px}}
  .back{{margin-top:28px;font-size:12px;letter-spacing:.1em;text-transform:uppercase}}
  .back a{{color:#076678;text-decoration:none}}
  .back a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<main>
  <h1>{esc(SITE_NAME)} — Archive</h1>
  <p class="sub">{len(rows)} editions, newest first</p>
  <ul>
{body}
  </ul>
  <p class="back"><a href="{BASE_URL}/">← Today</a></p>
</main>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--curate", action="store_true",
                    help="run Kimi byte-size summary pass (needs KIMI_API_KEY)")
    ap.add_argument("--date", help="edition date override YYYY-MM-DD (default: today IST)")
    args = ap.parse_args()

    now = datetime.now(IST)
    edition = args.date or now.strftime("%Y-%m-%d")

    log(f"{SITE_NAME} — edition {edition}")
    log("fetching sources…")
    hn, lob, arx, blogs = fetch_hn(), fetch_lobsters(), fetch_arxiv(), fetch_blogs()
    log(f"  hn={len(hn)} lobsters={len(lob)} arxiv={len(arx)} blogs={len(blogs)}")

    items = dedupe(hn + lob + arx + blogs)
    log(f"after dedupe: {len(items)}")

    # Bucket FIRST so curation only touches stories that make the cut.
    sections = assemble(items)

    if args.curate:
        to_curate, seen = [], set()
        for key in ("engineering", "papers", "tools", "discussions"):
            for it in sections.get(key) or []:
                k = _item_key(it)
                if k not in seen:
                    seen.add(k)
                    to_curate.append(it)
        log(f"kimi curation pass — {len(to_curate)} stories (capped)…")
        curate_with_kimi(to_curate, edition)  # mutates item dicts in place

    counts = {k: len(v) for k, v in sections.items()}
    log(f"sections: {counts}")

    # archive.json: read existing, append today
    archive_path = OUT_DIR / "archive.json"
    all_days = []
    if archive_path.exists():
        try:
            all_days = json.loads(archive_path.read_text())
        except Exception:
            all_days = []
    all_days = [d for d in all_days if d != edition]
    all_days.append(edition)
    all_days.sort(reverse=True)
    archive_path.write_text(json.dumps(all_days, indent=2))

    # render
    page = render_page(edition, sections, all_days)
    (OUT_DIR / "index.html").write_text(page)
    arch_dir = OUT_DIR / "archive"
    arch_dir.mkdir(exist_ok=True)
    (arch_dir / f"{edition}.html").write_text(page)
    (OUT_DIR / "rss.xml").write_text(render_rss(edition, sections))
    (arch_dir / "index.html").write_text(render_archive_index(all_days))

    log(f"wrote index.html, archive/{edition}.html, archive/index.html, rss.xml, archive.json")
    log(f"done — {sum(counts.values())} items total")


if __name__ == "__main__":
    sys.exit(main())
