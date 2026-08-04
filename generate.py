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
SECTION_CAPS = {"featured": 4, "papers": 6, "discussions": 8, "tools": 5, "blogs": 6}
BLOG_PER_FEED = 4            # max entries taken from each blog feed

# Engineering blog feeds (title, url, feed). Extend freely.
BLOG_FEEDS = [
    ("Go Blog", "https://go.dev/blog/feed.atom"),
    ("Rust Blog", "https://blog.rust-lang.org/feed.xml"),
    ("Cloudflare", "https://blog.cloudflare.com/rss/"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("Arpit Bhayani", "https://arpitbhayani.me/rss.xml"),
    ("Julia Evans", "https://jvns.ca/atom.xml"),
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
                "age_h": (now - h.get("created_at_i", now)) / 3600,
                "tag": tag,
            })
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
            "snippet": clean(s.get("description"), 240),
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
            summary = clean(e.find("a:summary", ns).text, 260)
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
                    snippet = clean(snippet.text, 240)
                age_h = 0
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
                            pass
                if not title or not url:
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
    """Bucket items into sections, pick Featured, apply per-section caps."""
    featured = sorted(
        [i for i in items if i["points"] >= 150 or (i["tag"] == "story" and i["points"] >= 120)],
        key=lambda i: i["points"], reverse=True)[:SECTION_CAPS["featured"]]
    sections = {
        "featured": featured,
        "papers": [i for i in items if i["kind"] == "paper"][:SECTION_CAPS["papers"]],
        "discussions": [i for i in items if i["kind"] == "discussion"][:SECTION_CAPS["discussions"]],
        "blogs": [i for i in items if i["kind"] == "blog"][:SECTION_CAPS["blogs"]],
    }
    # Tools & Projects: GitHub URLs from HN stories
    tools = [i for i in items if i["tag"] == "story" and "github.com" in (i["url"] or "")]
    sections["tools"] = tools[:SECTION_CAPS["tools"]]
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
# over between them, giving speed + resilience to flaky free tiers.
FREE_MODELS = [
    "deepseek-v4-flash-free",
    "mimo-v2.5-free",
    "ling-3.0-flash-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
]


def _llm_key():
    return (os.environ.get("OPENCODE_ZEN_API_KEY")
            or os.environ.get("KIMI_API_KEY"))


def _llm_endpoint():
    """Return (url, key, model). Prefer Hermes's default model (deepseek-free
    via opencode-zen); fall back to Kimi. Explicit LLM_URL/LLM_API_KEY/LLM_MODEL
    env override everything."""
    if os.environ.get("LLM_URL") or os.environ.get("LLM_API_KEY") or os.environ.get("LLM_MODEL"):
        return (
            os.environ.get("LLM_URL", DEFAULT_LLM_URL),
            os.environ.get("LLM_API_KEY") or _llm_key(),
            os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL),
        )
    if os.environ.get("OPENCODE_ZEN_API_KEY"):
        return DEFAULT_LLM_URL, os.environ["OPENCODE_ZEN_API_KEY"], DEFAULT_LLM_MODEL
    if os.environ.get("KIMI_API_KEY"):
        return ("https://api.moonshot.cn/v1/chat/completions",
                os.environ["KIMI_API_KEY"], "moonshot-v1-8k")
    return None, None, None


STE100_RULES = """
Write each summary in Simplified Technical English (ASD-STE100):
- ONE sentence, active voice, at most 24 words.
- Do NOT start with "This article", "This post", "This paper", or similar
  container references. Start directly with the subject ("SQLite validates
  data with...", "The paper introduces...") or an imperative takeaway.
- Use only standard technical terms and approved vocabulary; avoid jargon, idioms,
  contractions, and subjective opinion ("amazing", "huge", "must-read").
- State plainly what the item is and the single most useful takeaway for an
  engineer. Prefer specific numbers and nouns over adjectives.
- Formal, concise, matter-of-fact tone. No marketing language, no exclamation marks.
"""


CACHE_FILE = OUT_DIR / ".cache_summaries.json"


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
              "temperature": 0.2, "max_tokens": 4000},
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
    """Try models in order until one returns a non-empty JSON array."""
    for model in models:
        try:
            out = _request_summaries(url, key, model, prompt)
            if out:
                return out, model
            log(f"    {model}: empty response")
        except Exception as e:
            log(f"    {model}: failed ({e})")
        time.sleep(1.5)
    return [], None


def curate_with_kimi(items, edition):
    url, key, primary = _llm_endpoint()
    if not url or not key:
        log("  no LLM endpoint configured (OPENCODE_ZEN_API_KEY/KIMI_API_KEY) — skipping curation")
        return items
    if url.startswith("https://opencode.ai"):
        models = FREE_MODELS
        if primary not in models:
            models = [primary] + models
    else:
        models = [primary]
    models = [m for m in models if m]
    log(f"  curating in parallel across {len(models)} models: {', '.join(models[:3])}…")

    cache = _load_cache()
    fresh, cached = [], 0
    for it in items:
        k = _item_key(it)
        if k in cache:
            it["snippet"] = cache[k]
            cached += 1
        else:
            fresh.append(it)
    if cached:
        log(f"  reused {cached} cached summaries; curating {len(fresh)} new")

    BATCH = 10
    batches = [fresh[i : i + BATCH] for i in range(0, len(fresh), BATCH)]
    results = [None] * len(batches)

    def build_prompt(batch):
        payload = [
            {"title": it["title"], "snippet": (it["snippet"] or "")[:300], "url": it["url"]}
            for it in batch
        ]
        return (
            "You curate a byte-size daily learning digest for a software engineer. "
            + STE100_RULES
            + "Return STRICT JSON: an array of objects "
            "{\"title\": <exact item title, copied verbatim>, \"summary\": \"...\"} "
            "matching the input order. No markdown, no extra text.\n\n"
            f"Edition: {edition}. Items:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    def work(idx, batch):
        out, used = _curate_batch(url, key, models, build_prompt(batch))
        if used:
            log(f"  batch {idx} via {used} ({len(batch)} items)")
        else:
            log(f"  batch {idx}: all models failed — leaving source text")
        return out

    workers = min(len(batches), len(models)) if batches else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(work, i, b): i for i, b in enumerate(batches)}
            for f in as_completed(futs):
                results[futs[f]] = f.result()

    ok = fail = 0
    for idx, batch in enumerate(batches):
        for s in results[idx] or []:
            summary = (s.get("summary") or "").strip()
            if not summary:
                fail += 1
                continue
            # Match by title (robust against models returning 0/1-based indices).
            t = _item_key({"title": s.get("title")})
            if t:
                hit = False
                for it in batch:
                    if _item_key(it) == t:
                        it["snippet"] = summary
                        ok += 1
                        hit = True
                        break
                if not hit:
                    fail += 1
            else:
                i = s.get("i")
                if isinstance(i, int) and 0 <= i < len(batch):
                    batch[i]["snippet"] = summary
                    ok += 1
                else:
                    fail += 1

    for it in fresh:
        cache[_item_key(it)] = it.get("snippet", "")
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
  --paper:#0f1113; --paper2:#14171c; --rule:#2a2f37; --ink:#e8eaed;
  --mut:#9aa3b2; --mut2:#6b7484; --ac:#f0b429; --ln:#7aa2f7;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:light){
  :root{--paper:#fdfcfa; --paper2:#ffffff; --rule:#d8d6d0; --ink:#17191d;
  --mut:#545b66; --mut2:#8a8f99; --ac:#b45309; --ln:#1d4ed8;}
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
.chrome-in{max-width:920px;margin:0 auto;padding:9px 22px;
display:flex;align-items:center;justify-content:space-between;gap:14px;
font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut2)}
.chrome nav{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.chrome nav a{color:var(--mut);font-weight:600}
.chrome nav a:hover{color:var(--ln);text-decoration:none}
.chrome .left,.chrome .right{display:flex;gap:14px;align-items:center;white-space:nowrap}
.chrome .count{color:var(--ac);font-weight:700}
.toggle{border:1px solid var(--rule);background:transparent;color:var(--mut);
border-radius:14px;padding:3px 10px;font-size:11px;letter-spacing:.06em;
text-transform:uppercase}
.toggle:hover{color:var(--ink);border-color:var(--mut2)}

/* masthead / nameplate */
.mast{text-align:center;padding:44px 0 26px}
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

main{max-width:920px;margin:0 auto}
.story{padding:22px 0;border-bottom:1px solid var(--rule)}
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
.p-HN{color:#ff8c3b;background:rgba(255,102,0,.10);border:1px solid rgba(255,102,0,.25)}
.p-Lobsters{color:#ff7a66;background:rgba(233,86,63,.10);border:1px solid rgba(233,86,63,.25)}
.p-arxiv{color:#e58a8a;background:rgba(179,27,27,.10);border:1px solid rgba(179,27,27,.25)}
.p-blog{color:#8fb0f5;background:rgba(91,141,239,.10);border:1px solid rgba(91,141,239,.25)}
.p-tool{color:#56d364;background:rgba(46,160,67,.10);border:1px solid rgba(46,160,67,.25)}
@media (prefers-color-scheme:light){
  .p-HN{color:#c2410c}.p-Lobsters{color:#b91c1c}.p-arxiv{color:#991b1b}
  .p-blog{color:#1e40af}.p-tool{color:#166534}
}
.story .smeta .pts{color:var(--ac);font-weight:600}

/* footer */
.foot{margin:40px 0 34px;text-align:center;color:var(--mut2);font-size:12.5px;
letter-spacing:.04em}
.foot a{color:var(--mut);text-decoration:underline}
.foot .ln{display:inline-block;margin:0 8px}
"""

SCRIPT = """
(function(){var t=document.querySelector('.toggle');
function set(a){document.documentElement.dataset.theme=a;
try{localStorage.setItem('theme',a)}catch(e){}}
function upd(){t.textContent=(document.documentElement.dataset.theme==='light')
?'Night':'Day';}
document.documentElement.dataset.theme=
localStorage.getItem('theme')||(matchMedia('(prefers-color-scheme:light)').matches?'light':'dark');
upd();
function flip(){var cur=document.documentElement.dataset.theme;
set(cur==='light'?'dark':'light');upd();}
t.addEventListener('click',flip);t.addEventListener('touchend',flip);
})();
"""


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
        ("featured", "TOP STORIES"),
        ("papers", "PAPERS"),
        ("discussions", "DISCUSSIONS"),
        ("tools", "TOOLS & PROJECTS"),
        ("blogs", "ENGINEERING BLOGS"),
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
            lb = "Top story" if (key == "featured" and i == 0) else None
            stream.append(item_html(it, lb))

    body = "\n".join(stream)

    archive_links = "".join(
        f'<a style="margin:0 10px" href="{BASE_URL}/{d}">{d}</a>'
        for d in all_days if d != edition)
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
    <a href="{HOME_REPO}">Source</a><a href="{BASE_URL}/rss.xml">RSS</a></div>
  <div class="right"><span>{edition_label}</span>
    <span class="count">{total} STORIES</span>
    <button class="toggle" type="button"></button></div>
</div></div>

<header class="mast">
  <h1>{esc(SITE_NAME)}<span class="dot">.</span></h1>
  <div class="tag">{esc(SITE_TAGLINE)}</div>
  <div class="edition">{edition_label} · Edition {edition}</div>
  <div class="nameplate"><span>Depth over noise</span></div>
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

<script>{SCRIPT}</script>
</body>
</html>"""


def render_rss(edition, sections):
    items = []
    for key in ("featured", "papers", "discussions", "tools", "blogs"):
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
        for key in ("featured", "papers", "discussions", "tools", "blogs"):
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

    log(f"wrote index.html, archive/{edition}.html, rss.xml, archive.json")
    log(f"done — {sum(counts.values())} items total")


if __name__ == "__main__":
    sys.exit(main())
