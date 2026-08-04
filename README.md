# Daily Byte 📚

A byte-size daily learning digest — curated tech/CS/AI papers, discussions,
and engineering blog posts, published as a static newspaper-style HTML page.

**Live at:** [learn.shenthar.me](https://learn.shenthar.me)
**Generated daily by:** Hermes agent (cron, 7:00 IST)

## How it works

1. `generate.py` fetches high-signal items from free sources:
   - **Hacker News** (Algolia API — stories ≥120 pts, Ask/Show HN ≥40)
   - **arXiv** (cs.AI/SE/LG/OS/DB/CR/PL, last 24h, capped)
   - **Lobsters** (score ≥30)
   - **Engineering blogs** (Go, Rust, Cloudflare, GitHub, Arpit Bhayani, Julia Evans)
2. Dedupes, scores, and buckets into a continuous newspaper stream:
   `TOP STORIES · PAPERS · DISCUSSIONS · TOOLS & PROJECTS · ENGINEERING BLOGS`
   Per-section caps keep a daily edition at ~25-30 stories (tune `SECTION_CAPS`).
3. Optional `--curate` pass writes **Simplified Technical English (STE100)**
   byte-size "why it matters" summaries using the **same default model Hermes
   runs** (deepseek-v4-flash-free via opencode-zen; falls back to Kimi).
   Summaries are cached in `.cache_summaries.json` (gitignored) so same-day
   re-runs are instant.
4. Renders `index.html` + `archive/YYYY-MM-DD.html` + `rss.xml` + `archive.json`.

Every night the generated files are committed + pushed here, so the whole
history of the digest is version-controlled.

## Usage

```bash
# plain generation (no LLM)
python3 generate.py

# with STE100 byte-size summaries (default model)
OPENCODE_ZEN_API_KEY=... python3 generate.py --curate

# regenerate a specific edition
python3 generate.py --date 2026-08-04
```

## Layout

```
/opt/learning-portal        ← this repo == nginx web root for learn.shenthar.me
├── generate.py             ← the generator (no secrets inside)
├── index.html              ← today's edition
├── archive/YYYY-MM-DD.html ← per-day editions (history)
├── rss.xml
├── archive.json
```

**Security:** this repo is public. It contains only the generator and generated
content — no API keys, no nginx configs, no infra files.
