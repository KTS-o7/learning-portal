#!/usr/bin/env python3
"""Build the Daily Byte edition for TODAY (UTC).

Pipeline:
  1. Fetch engineering blogs, arXiv (systems/PL/SE categories), HN front page, Lobsters.
  2. Drop anything already published in an earlier edition (no reruns).
  3. Select: engineering 3 (max 1 per source, max 1 vendor/project blog),
     papers 2 and discussions 2 + 1 offbeat (LLM-ranked against READER),
     tools 2 (HN repo links by points).
  4. Summarise each story into why / gist / takeaway (+ comment-thread debate
     for discussions) via MiniMax-M3.
  5. One editorial call: editor's note, "start here" pick, per-section intros
     written from the day's actual stories.
  6. Emit data/<DATE>.json, data/latest.json, rss.xml, archive/<DATE>.html,
     archive/index.html and update archive.json.

Usage: build_edition.py [--dry-run | --re-edit YYYY-MM-DD]
  --dry-run   write only /tmp/dailybyte-preview/<DATE>.json, touch nothing in the repo.
  --re-edit   redo only the editor's note / start-here / intros of an existing edition.
"""
from __future__ import annotations
import json, pathlib, re, os, sys, html, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
DRY_RUN = "--dry-run" in sys.argv

# Load API keys (cron context doesn't source .env). Never override real env vars.
for env_file in (os.environ.get("DAILYBYTE_ENV_FILE"), "/root/.hermes/.env"):
    if env_file and pathlib.Path(env_file).is_file():
        for line in pathlib.Path(env_file).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY", "")

UA = "Mozilla/5.0 (compatible; DailyByteBot/1.0; +https://learn.shenthar.me)"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

READER = (
    "a backend/infrastructure software engineer who reads books like Designing Data-Intensive "
    "Applications, OSTEP, SICP and Crafting Interpreters. Interested in distributed systems, "
    "databases, operating systems, performance, programming languages and compilers, developer "
    "tooling, and practical AI/LLM engineering. Not interested in marketing, funding news, "
    "product launch announcements, or ML theory without a systems angle."
)

# Section caps — ~10 stories keeps the edition a genuinely short daily read.
CAPS = {"engineering": 3, "papers": 2, "tools": 2, "discussions": 2, "offbeat": 1}

# (name, feed url, vendor/project blog?). Vendor blogs are capped at one per edition.
ENGINEERING_FEEDS = [
    ("Sean Goedecke", "https://www.seangoedecke.com/rss.xml", False),
    ("Julia Evans", "https://jvns.ca/atom.xml", False),
    ("Arpit Bhayani", "https://arpitbhayani.me/rss.xml", False),
    ("Marc Brooker", "https://brooker.co.za/blog/rss.xml", False),
    ("Murat Demirbas", "https://muratbuffalo.blogspot.com/feeds/posts/default", False),
    ("Phil Eaton", "https://notes.eatonphil.com/rss.xml", False),
    ("Hillel Wayne", "https://www.hillelwayne.com/index.xml", False),
    ("Simon Willison", "https://simonwillison.net/atom/entries/", False),
    ("Brendan Gregg", "https://www.brendangregg.com/blog/rss.xml", False),
    ("Eli Bendersky", "https://eli.thegreenplace.net/feeds/all.atom.xml", False),
    ("Dan Luu", "https://danluu.com/atom.xml", False),
    ("Aleksey Charapko", "http://charap.co/feed/", False),
    ("Mitchell Hashimoto", "https://mitchellh.com/feed.xml", False),
    ("Go Blog", "https://go.dev/blog/feed.atom", True),
    ("Rust Blog", "https://blog.rust-lang.org/feed.xml", True),
    ("GitHub Blog", "https://github.blog/feed/", True),
]
ENGINEERING_MAX_AGE_DAYS = 10  # independent blogs post weekly-ish; history check prevents reruns

ARXIV_CATS = ["cs.DC", "cs.DB", "cs.OS", "cs.PL", "cs.SE", "cs.PF"]

SECTION_META = {
    "engineering": ("Engineering", "blog", "eng"),
    "papers": ("Papers", "paper", "pap"),
    "tools": ("Tools", "repo", "tool"),
    "discussions": ("Discussions", "discussion", "disc"),
    "offbeat": ("Off the clock", "discussion", "off"),
}

# ---- HTTP helpers ----------------------------------------------------------

def fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"  fetch fail {url}: {e}", file=sys.stderr)
        return None


def fetch_json(url: str, timeout: int = 20):
    raw = fetch(url, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def plain(s: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", s or ""))).strip()


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def norm_url(url: str) -> str:
    u = urllib.parse.urlsplit((url or "").strip())
    host = u.netloc.lower().removeprefix("www.")
    path = re.sub(r"v\d+$", "", u.path.rstrip("/")) if "arxiv.org" in host else u.path.rstrip("/")
    return f"{host}{path}"


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())[:60]


# ---- Feed parsers ----------------------------------------------------------

def parse_feed(url: str, source: str, vendor: bool) -> list[dict]:
    raw = fetch(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        link_el = entry.find("atom:link[@rel='alternate']", ATOM_NS)
        if link_el is None:
            link_el = entry.find("atom:link", ATOM_NS)
        link = link_el.get("href") if link_el is not None else ""
        title = plain(_text(entry.find("atom:title", ATOM_NS)))
        if not link or not title:
            continue
        items.append({
            "title": title, "url": link, "source": source, "vendor": vendor,
            "published_at": _text(entry.find("atom:published", ATOM_NS)) or _text(entry.find("atom:updated", ATOM_NS)),
            "snippet": plain(_text(entry.find("atom:summary", ATOM_NS)) or _text(entry.find("atom:content", ATOM_NS)))[:600],
        })
    for item in root.findall(".//item"):
        title, link = plain(_text(item.find("title"))), _text(item.find("link"))
        if not title or not link:
            continue
        items.append({
            "title": title, "url": link, "source": source, "vendor": vendor,
            "published_at": _text(item.find("pubDate")),
            "snippet": plain(_text(item.find("description")))[:600],
        })
    return items


def fetch_blogs() -> list[dict]:
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(parse_feed, u, n, v) for n, u, v in ENGINEERING_FEEDS]):
            out.extend(f.result())
    return out


ARXIV_CACHE = ROOT / ".cache" / "arxiv_pool.json"


def _first_author(names: str) -> str:
    authors = [a.strip() for a in names.split(",") if a.strip()]
    return (authors[0] + (" et al." if len(authors) > 1 else "")) if authors else "arXiv"


def fetch_arxiv() -> list[dict]:
    """Papers from the last 7 days.

    rss.arxiv.org lists only the latest announcement (empty on weekends), and the
    export API answers 406 to uncached queries from this server's IP. So each run
    merges the day's RSS items into a rolling 7-day cache and selects from that.
    """
    fresh: list[dict] = []
    raw = fetch("https://rss.arxiv.org/rss/" + "+".join(ARXIV_CATS), timeout=40)
    try:
        root = ET.fromstring(raw) if raw else None
    except ET.ParseError:
        root = None
    for item in (root.findall(".//item") if root is not None else []):
        if (_text(item.find("{http://arxiv.org/schemas/atom}announce_type")) or "new") not in ("new", "cross"):
            continue
        link = _text(item.find("link")).replace("http://", "https://")
        abstract = re.sub(r"^.*?Abstract:\s*", "", plain(_text(item.find("description"))))
        fresh.append({
            "title": plain(_text(item.find("title"))), "url": re.sub(r"v\d+$", "", link),
            "source": _first_author(_text(item.find("{http://purl.org/dc/elements/1.1/}creator"))),
            "published_at": _text(item.find("pubDate")), "snippet": abstract[:1500],
        })
    if not fresh:  # fallback: export API (works when the query is cached at arXiv's CDN)
        q = "+OR+".join(f"cat:{c}" for c in ARXIV_CATS)
        raw = fetch(f"https://export.arxiv.org/api/query?search_query={q}"
                    "&sortBy=submittedDate&sortOrder=descending&max_results=120", timeout=40)
        try:
            root = ET.fromstring(raw) if raw else None
        except ET.ParseError:
            root = None
        for entry in (root.findall("atom:entry", ATOM_NS) if root is not None else []):
            fresh.append({
                "title": plain(_text(entry.find("atom:title", ATOM_NS))),
                "url": re.sub(r"v\d+$", "", _text(entry.find("atom:id", ATOM_NS))).replace("http://", "https://"),
                "source": _first_author(", ".join(_text(a) for a in entry.findall("atom:author/atom:name", ATOM_NS))),
                "published_at": _text(entry.find("atom:published", ATOM_NS)),
                "snippet": plain(_text(entry.find("atom:summary", ATOM_NS)))[:1500],
            })
    pool = {p["url"]: p for p in (json.loads(ARXIV_CACHE.read_text()) if ARXIV_CACHE.exists() else [])}
    for p in fresh:
        if p["title"] and p["url"]:
            pool[p["url"]] = {**p, "cached_at": pool.get(p["url"], {}).get("cached_at", NOW.isoformat())}
    pool_list = [p for p in pool.values()
                 if (NOW - datetime.fromisoformat(p["cached_at"])).total_seconds() <= 7 * 86400]
    if not DRY_RUN:
        ARXIV_CACHE.parent.mkdir(exist_ok=True)
        ARXIV_CACHE.write_text(json.dumps(pool_list, ensure_ascii=False))
    return sorted(pool_list, key=lambda p: p["cached_at"], reverse=True)


def fetch_hn() -> list[dict]:
    d = fetch_json("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60")
    out = []
    for h in (d or {}).get("hits", []):
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({
            "title": h.get("title") or "", "url": h.get("url") or hn_url, "source": "Hacker News",
            "hn_id": h["objectID"], "thread_url": hn_url,
            "published_at": h.get("created_at", ""),
            "snippet": plain(h.get("story_text") or "")[:800],
            "score": h.get("points") or 0, "comments": h.get("num_comments") or 0,
        })
    return out


def fetch_lobsters() -> list[dict]:
    d = fetch_json("https://lobste.rs/hottest.json")
    out = []
    for s in d or []:
        out.append({
            "title": s.get("title") or "", "url": s.get("url") or s.get("comments_url"),
            "source": "Lobsters", "thread_url": s.get("comments_url"), "lobsters_id": s.get("short_id"),
            "published_at": s.get("created_at", ""), "snippet": plain(s.get("description") or "")[:800],
            "score": s.get("score") or 0, "comments": s.get("comment_count") or 0,
            "tags": s.get("tags") or [],
        })
    return out


def fetch_thread_comments(item: dict, limit: int = 8) -> str:
    """Top comments of the discussion thread, as plain text."""
    comments: list[str] = []
    if item.get("hn_id"):
        d = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item['hn_id']}.json")
        for kid in (d or {}).get("kids", [])[:limit]:
            c = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{kid}.json")
            if c and not c.get("dead") and not c.get("deleted") and c.get("text"):
                comments.append(plain(c["text"])[:500])
    elif item.get("lobsters_id"):
        d = fetch_json(f"https://lobste.rs/s/{item['lobsters_id']}.json")
        for c in (d or {}).get("comments", [])[:limit]:
            if c.get("depth", 0) == 0 and c.get("comment_plain"):
                comments.append(plain(c["comment_plain"])[:500])
    return "\n".join(f"- {c}" for c in comments)


# ---- Article body extraction ----------------------------------------------

SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


def extract_body(url: str) -> str:
    raw = fetch(url, timeout=15)
    if not raw:
        return ""
    text = STYLE_RE.sub(" ", SCRIPT_RE.sub(" ", raw.decode("utf-8", errors="replace")))
    best = ""
    for pattern in (r"<article[^>]*>(.*?)</article>", r"<main[^>]*>(.*?)</main>"):
        for block in re.findall(pattern, text, re.DOTALL | re.IGNORECASE):
            p = plain(block)
            if len(p) > len(best):
                best = p
        if len(best) >= 400:
            break
    if len(best) < 400:
        p = plain(" ".join(P_RE.findall(text)))
        if len(p) > len(best):
            best = p
    return best[:12_000]


# ---- LLM -------------------------------------------------------------------

def llm(prompt: str, max_tokens: int = 1800, temperature: float = 0.3) -> str:
    if not MINIMAX_KEY:
        return ""
    payload = {
        "model": "MiniMax-M3",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Keeps reasoning out of `content`. M3 can spend 600+ tokens reasoning, so leave headroom.
        "reasoning_split": True,
    }
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://api.minimax.io/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {MINIMAX_KEY}", "Content-Type": "application/json",
                         "User-Agent": UA},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                msg = json.loads(r.read())["choices"][0]["message"]
            text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.DOTALL).strip()
            if text:
                return text
        except Exception as e:
            print(f"  llm fail (attempt {attempt + 1}): {e}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return ""


def llm_json(prompt: str, attempts: int = 2, **kw) -> dict | None:
    for _ in range(attempts):
        text = llm(prompt, **kw)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    return None


STYLE_RULES = (
    "Rules: use only facts from the text. Plain, direct English; explain jargon in simple words. "
    "Never start a field with \"The article\", \"The paper\", \"The author\", \"This\", \"In this\" or "
    "\"Researchers\". No marketing words (revolutionary, game-changing, powerful, seamless, cutting-edge). "
    "No markdown, no lists, no emoji."
)

BAD_OPENERS = ("the article", "the paper", "the author", "this article", "this paper", "this post",
               "the provided", "as an ai", "i'm unable", "i cannot", "here is", "here's", "based on the")


def _clean(s) -> str:
    return WS_RE.sub(" ", str(s or "")).strip().strip('"')


def rank(candidates: list[dict], want: int, what: str, offbeat: bool = False) -> tuple[list[int], int | None]:
    """Ask the LLM to pick the `want` candidates most useful to READER. Returns indices."""
    if not candidates:
        return [], None
    lines = [f"{i}. {c['title']} — {c.get('snippet', '')[:220]}" for i, c in enumerate(candidates)]
    extra = ('Also pick one "offbeat" item: the most interesting non-software story in the list '
             '(science, history, craft), or null if none. ') if offbeat else ""
    prompt = (
        f"You pick {what} for Daily Byte, a daily digest read by {READER}\n\n"
        f"Candidates:\n" + "\n".join(lines) + "\n\n"
        f"Pick the {want} candidates this reader would learn the most from. Prefer depth, "
        f"practical insight and clear explanations over hype, launches and narrow benchmarks. {extra}"
        'Return ONLY JSON: {"picks": [index, ...]' + (', "offbeat": index or null' if offbeat else "") + "}"
    )
    d = llm_json(prompt, attempts=3, max_tokens=3000, temperature=0.2) or {}
    picks =[i for i in d.get("picks", []) if isinstance(i, int) and 0 <= i < len(candidates)]
    ob = d.get("offbeat")
    ob = ob if isinstance(ob, int) and 0 <= ob < len(candidates) and ob not in picks else None
    return list(dict.fromkeys(picks))[:want], ob


def summarise(item: dict, section: str, body: str, comments: str = "") -> dict:
    text = body if len(body) > 300 else item.get("snippet", "")
    if len(text) < 150:
        print(f"  no text to summarise ({len(body)} chars body): {item['title']}", file=sys.stderr)
        return {}
    debate_key = ('"debate": 1-2 sentences (max 45 words): what commenters argue about or disagree on, '
                  "based only on the comments.\n") if comments else ""
    why_rule = ('"why": one sentence (max 20 words): what makes this fascinating. Do not force a link to '
                "software.\n") if section == "offbeat" else (
                '"why": one sentence (max 20 words): why this reader should care. Concrete, no hype.\n')
    prompt = (
        f"You write for Daily Byte, a daily digest read by {READER}\n\n"
        f"Title: {item['title']}\nSource: {item['source']}\n\nText:\n{text}\n\n"
        + (f"Top comments from the discussion thread:\n{comments}\n\n" if comments else "")
        + "Return ONLY a JSON object with these keys:\n"
        + why_rule
        + '"gist": 2-3 sentences (max 65 words): the key facts, numbers or argument.\n'
        '"takeaway": one sentence (max 22 words): the idea, lesson or action worth keeping.\n'
        '"level": "beginner", "intermediate" or "advanced".\n'
        + debate_key + STYLE_RULES
        + (" Write for a strong engineer outside this subfield: replace formal terms with what they mean "
           "in practice, and say what problem the work solves before how." if section == "papers" else "")
    )
    d = llm_json(prompt, attempts=3, max_tokens=3000, temperature=0.3) or {}
    if str(d.get("gist", "")).lower().startswith(BAD_OPENERS):
        d = llm_json(prompt + '\nThe "gist" must start with the subject itself, never with "The article" '
                     'or "The paper".', max_tokens=3000, temperature=0.3) or d
    out = {k: _clean(d.get(k)) for k in ("why", "gist", "takeaway", "level", "debate") if d.get(k)}
    # Last resort: drop a leading "The article/paper/post" instead of losing the story.
    out["gist"] = re.sub(r"^(?:the|this) (?:article|paper|post|piece|author)\s+(\w)",
                         lambda m: m.group(1).upper(), out.get("gist", ""), flags=re.IGNORECASE)
    gist = out["gist"]
    problem = ("no JSON from model" if not d else "no gist" if not gist
               else "bad opener" if gist.lower().startswith(BAD_OPENERS) else "gist too short" if len(gist) < 80 else "")
    if problem:
        print(f"  summary rejected ({problem}): {item['title']} | {json.dumps(d)[:300]}", file=sys.stderr)
        return {}
    if out.get("level") not in ("beginner", "intermediate", "advanced"):
        out.pop("level", None)
    return out


def editorial(stories: list[dict]) -> dict:
    lines = [f"{s['id']} [{s['section']}] {s['title']} — {s.get('why', '')}" for s in stories]
    sections = sorted({s["section"] for s in stories})
    prompt = (
        f"You are the editor of Daily Byte, a daily digest read by {READER}\n\n"
        "Today's stories:\n" + "\n".join(lines) + "\n\n"
        "Return ONLY JSON with keys:\n"
        '"editor_note": 1-2 sentences (max 45 words) on what connects today\'s edition. Specific, no hype, '
        'do not start with "Today".\n'
        '"lede_id": the id of the single story most worth reading first.\n'
        '"lede_reason": one sentence (max 20 words) on why to start there.\n'
        '"intros": an object with one sentence (max 22 words) per section in ' + json.dumps(sections)
        + " describing that section's actual stories. The offbeat section is a non-software curiosity: "
        "describe it on its own terms, without tying it to software.\n"
        "Respect the word limits strictly. Do not address the reader as \"you\" and do not comment on the "
        "editing process.\n" + STYLE_RULES
    )
    # M3 sometimes returns empty content or runs long enough to truncate the JSON; give it room and retries.
    d = llm_json(prompt, attempts=3, max_tokens=4000, temperature=0.4) or {}
    if not d:
        print("  editorial call failed — falling back to first story as lede", file=sys.stderr)
    ids = {s["id"] for s in stories}
    lede_id = d.get("lede_id") if d.get("lede_id") in ids else None
    return {
        "editor_note": _clean(d.get("editor_note")),
        "lede_id": lede_id or stories[0]["id"],
        "lede_reason": _clean(d.get("lede_reason")) if lede_id else "",
        "intros": {k: _clean(v) for k, v in (d.get("intros") or {}).items() if k in sections and v},
    }


# ---- Selection helpers -----------------------------------------------------

def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def age_hours(item: dict) -> float | None:
    d = parse_dt(item.get("published_at", ""))
    return (NOW - d).total_seconds() / 3600 if d else None


def load_published() -> tuple[set[str], set[str]]:
    """URLs and title keys of every story in earlier editions (today's file excluded so reruns work)."""
    urls, titles = set(), set()
    for f in (ROOT / "data").glob("20*.json"):
        if f.stem == TODAY:
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for sec in d.get("sections", []):
            for st in sec.get("stories", []):
                urls.add(norm_url(st.get("url", "")))
                titles.add(title_key(st.get("title", "")))
    return urls, titles


REPO_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "sr.ht")


def is_repo(item: dict) -> bool:
    return any(h in (item.get("url") or "").lower() for h in REPO_HOSTS)


def reading_minutes(body: str) -> int | None:
    words = len(body.split())
    return max(1, round(words / 230)) if words >= 150 else None


# ---- Pipeline --------------------------------------------------------------

def reedit(date: str):
    """Re-run only the editorial pass on an existing edition (e.g. after the call failed)."""
    path = ROOT / "data" / f"{date}.json"
    d = json.loads(path.read_text())
    stories = [{**st, "section": s["name"]} for s in d["sections"] for st in s["stories"]]
    ed = editorial(stories)
    lede = next(s for s in stories if s["id"] == ed["lede_id"])
    d["editor_note"] = ed["editor_note"]
    d["lede"] = {"id": lede["id"], "title": lede["title"], "reason": ed["lede_reason"]}
    for s in d["sections"]:
        s["intro"] = ed["intros"].get(s["name"], s.get("intro", ""))
    payload = json.dumps(d, indent=2, ensure_ascii=False)
    path.write_text(payload)
    if json.loads((ROOT / "data" / "latest.json").read_text()).get("date") == date:
        (ROOT / "data" / "latest.json").write_text(payload)
    for script in ("build_rss.py", "build_snapshot.py", "build_archive_index.py"):
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / script), date], cwd=ROOT)
    print(f"re-edited {date}: note={bool(ed['editor_note'])} intros={len(ed['intros'])}")


def main():
    if "--re-edit" in sys.argv:
        return reedit(sys.argv[sys.argv.index("--re-edit") + 1])
    print(f"Edition for {TODAY}{' (dry run)' if DRY_RUN else ''}")
    if not MINIMAX_KEY:
        print("ERROR: MINIMAX_API_KEY missing", file=sys.stderr)
        sys.exit(1)

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_blogs, f_pap, f_hn, f_lob = (ex.submit(fn) for fn in (fetch_blogs, fetch_arxiv, fetch_hn, fetch_lobsters))
        blogs, papers, hn, lobs = f_blogs.result(), f_pap.result(), f_hn.result(), f_lob.result()
    print(f"  fetched: blogs={len(blogs)} papers={len(papers)} hn={len(hn)} lobsters={len(lobs)}")

    pub_urls, pub_titles = load_published()
    seen: set[str] = set()

    def fresh(items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            u, t = norm_url(it["url"]), title_key(it["title"])
            if not t or u in pub_urls or t in pub_titles or u in seen or t in seen:
                continue
            seen.update((u, t))
            out.append(it)
        return out

    # Engineering: recent posts, independent voices first, one per source, max one vendor blog.
    def eng_score(it):
        a = age_hours(it)
        s = (ENGINEERING_MAX_AGE_DAYS * 24 - a) / 24 if a is not None else 0
        s += -2 if it["vendor"] else 3
        if re.match(r"(announcing|introducing|now available|github copilot)", it["title"].lower()):
            s -= 4
        return s

    eng_pool = [x for x in fresh(blogs) if (age_hours(x) or 0) <= ENGINEERING_MAX_AGE_DAYS * 24]
    eng_pool.sort(key=eng_score, reverse=True)
    eng, used_sources, vendor_used = [], set(), False
    for it in eng_pool:
        if it["source"] in used_sources or (it["vendor"] and vendor_used):
            continue
        eng.append(it)
        used_sources.add(it["source"])
        vendor_used |= it["vendor"]
        if len(eng) == CAPS["engineering"]:
            break

    def split(pool: list[dict], picks: list[int], cap: int, exclude=()) -> tuple[list[dict], list[dict]]:
        """Ranked picks first, then the rest of the pool: (selected, backups)."""
        ranked = [pool[i] for i in picks]
        order = [x for x in ranked + [x for x in pool if x not in ranked] if x not in exclude]
        return order[:cap], order[cap:]

    # Papers: last week's systems/PL/SE papers, ranked for this reader.
    pap_pool = fresh(papers)[:60]
    picks, _ = rank(pap_pool, CAPS["papers"] + 2, "research papers")
    pap, pap_backup = split(pap_pool, picks, CAPS["papers"])

    # Tools: repo links on the HN front page, by points.
    hn_fresh = fresh(hn)
    tools_pool = sorted([x for x in hn_fresh if is_repo(x)], key=lambda x: x["score"], reverse=True)
    tools, tools_backup = tools_pool[:CAPS["tools"]], tools_pool[CAPS["tools"]:]

    # Discussions: busy HN/Lobsters threads ranked for this reader, plus one offbeat pick.
    disc_pool = [x for x in hn_fresh if not is_repo(x) and x["comments"] >= 30]
    disc_pool += [x for x in fresh(lobs) if x["comments"] >= 5]
    disc_pool.sort(key=lambda x: x["score"] + 0.5 * x["comments"], reverse=True)
    disc_pool = disc_pool[:40]
    picks, ob = rank(disc_pool, CAPS["discussions"] + 2, "discussion threads", offbeat=True)
    offbeat = [disc_pool[ob]] if ob is not None else []
    disc, disc_backup = split(disc_pool, picks, CAPS["discussions"], exclude=offbeat)
    # When a pick can't be summarised (PDF, bot wall, JS-only page), the next backup takes its slot.
    backups = {"engineering": [x for x in eng_pool if x not in eng], "papers": pap_backup,
               "tools": tools_backup, "discussions": disc_backup, "offbeat": []}

    work = ([(x, "engineering") for x in eng] + [(x, "papers") for x in pap] + [(x, "tools") for x in tools]
            + [(x, "discussions") for x in disc] + [(x, "offbeat") for x in offbeat])
    print(f"Summarising {len(work)} stories...")

    def process(item: dict, section: str) -> dict | None:
        body = extract_body(item["url"]) if section != "papers" else ""
        comments = fetch_thread_comments(item) if section in ("discussions", "offbeat") else ""
        s = summarise(item, section, body, comments)
        if not s:
            print(f"  dropped (no usable summary): {item['title']}", file=sys.stderr)
            return None
        return {**item, **s, "section": section,
                "minutes": reading_minutes(body) if section in ("engineering", "discussions", "offbeat") else None}

    done: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(process, it, sec): n for n, (it, sec) in enumerate(work)}
        for f in as_completed(futures):
            try:
                r = f.result()
            except Exception as e:
                print(f"  process fail: {e}", file=sys.stderr)
                r = None
            if r:
                done[futures[f]] = r
    enriched = [done[n] for n in sorted(done)]

    # Refill dropped slots from each section's backups (a few tries per section).
    for section, cap in CAPS.items():
        tries = 0
        while sum(s["section"] == section for s in enriched) < cap and backups[section] and tries < 3:
            cand = backups[section].pop(0)
            if section == "engineering":
                chosen = [s for s in enriched if s["section"] == "engineering"]
                if cand["source"] in {s["source"] for s in chosen} or (cand["vendor"] and any(s["vendor"] for s in chosen)):
                    continue
            tries += 1
            print(f"  refilling {section} with: {cand['title']}", file=sys.stderr)
            r = process(cand, section)
            if r:
                enriched.append(r)

    # Stable ids, then the editorial pass.
    counters: dict[str, int] = {}
    for s in enriched:
        prefix = SECTION_META[s["section"]][2]
        counters[prefix] = counters.get(prefix, 0) + 1
        s["id"] = f"{prefix}-{counters[prefix]:02d}"
    total = len(enriched)
    if total < 5:
        print(f"ERROR: only {total} usable stories — keeping the last good edition", file=sys.stderr)
        sys.exit(1)
    ed = editorial(enriched)

    sections = []
    for name in CAPS:
        label, kind, _ = SECTION_META[name]
        stories = []
        for s in (x for x in enriched if x["section"] == name):
            st = {
                "id": s["id"], "title": s["title"], "url": s["url"], "source": s["source"],
                "source_kind": kind, "published_at": s.get("published_at", ""),
                "summary": s["gist"],  # kept for RSS and older renderers
                "why": s.get("why", ""), "takeaway": s.get("takeaway", ""),
            }
            for k in ("debate", "level", "minutes", "thread_url"):
                if s.get(k):
                    st[k] = s[k]
            stories.append(st)
        if stories:
            sections.append({"name": name, "label": label, "intro": ed["intros"].get(name, ""), "stories": stories})

    archive = json.loads((ROOT / "archive.json").read_text())
    archive_no_today = [d for d in archive if d != TODAY]
    lede = next((s for s in enriched if s["id"] == ed["lede_id"]), None)
    digest = {
        "date": TODAY,
        "label": NOW.strftime("%A, %B %-d, %Y"),
        "edition_tag": f"vol-2-no-{34 + (NOW - datetime(2026, 9, 9, tzinfo=timezone.utc)).days}",
        "total": total,
        "prev_day": archive_no_today[0] if archive_no_today else None,
        "next_day": None,
        "editor_note": ed["editor_note"],
        "lede": {"id": lede["id"], "title": lede["title"], "reason": ed["lede_reason"]} if lede else None,
        "site": {
            "name": "Daily Byte",
            "tagline": "A byte-size daily digest for engineers who value depth over noise.",
            "base_url": "https://learn.shenthar.me",
            "repo": "https://github.com/KTS-o7/learning-portal",
        },
        "sections": sections,
    }
    payload = json.dumps(digest, indent=2, ensure_ascii=False)

    if DRY_RUN:
        out = pathlib.Path("/tmp/dailybyte-preview") / f"{TODAY}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload)
        print(f"DRY RUN wrote {out} total={total}")
        return

    (ROOT / "data" / f"{TODAY}.json").write_text(payload)
    (ROOT / "data" / "latest.json").write_text(payload)
    (ROOT / "archive.json").write_text(json.dumps([TODAY] + archive_no_today, indent=2) + "\n")
    for script in ("build_rss.py", "build_snapshot.py", "build_archive_index.py"):
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / script), TODAY], cwd=ROOT)
    print(f"OK {TODAY} total={total}")


if __name__ == "__main__":
    main()
