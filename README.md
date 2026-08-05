# Daily Byte 📚

A byte-size daily learning digest — curated tech/CS/AI papers, discussions,
and engineering blog posts, published as a static newspaper-style HTML page.

**Live at:** [learn.shenthar.me](https://learn.shenthar.me)
**Generated daily by:** Hermes agent (cron, 01:30 UTC = 07:00 IST)

## How it works

Every day at 01:30 UTC, a Hermes cron job loads the
`learning-portal-curator` skill (at `~/.hermes/skills/learning-portal-curator/`)
and runs the full curation at runtime:

1. **Read SKILL.md** — workflow + section rules
2. **Fetch** high-signal items from free sources via `feeds.md`:
   - Hacker News (top stories, ≥120 pts)
   - arXiv (cs.AI/LG/PL, last 24h)
   - Lobsters (recent + page 2)
   - Engineering blogs (Rust, Go, GitHub, Arpit Bhayani, Julia Evans,
     Sean Goedecke) — vendor marketing feeds deliberately excluded
3. **Dedupe, score, bucket** into a continuous newspaper stream:
   `Engineering (top story) · Papers · Tools & Projects · Discussions`
   Per-section caps: engineering 5, papers 5, tools 4, discussions 4
   Per-source diversity: no single source > 3 in engineering
4. **Summarise** each story via MiniMax-M3 (parallel,
   `reasoning_split=True` to keep thinking out of `content`).
   Same model Hermes uses — `~/.hermes/.env` provides `MINIMAX_API_KEY`,
   with `OPENCODE_ZEN_API_KEY` and `KIMI_API_KEY` as fallbacks.
5. **Render** `index.html` + `archive/<EDITION>.html` + `archive/index.html`
   + `rss.xml` using the literal templates in the skill.
6. **Commit + push** to `main`; nginx serves the site from `/opt/learning-portal/`.

## Layout

```
/opt/learning-portal
├── index.html                   ← today's edition
├── archive/YYYY-MM-DD.html      ← per-day editions (history)
├── archive/index.html           ← archive listing
├── rss.xml
├── archive.json                 ← ordered list of editions (newest first)
├── generate.py.deprecated       ← old pipeline, kept for rollback only
└── run_hermes_curator.sh.*      ← old test wrapper, kept for rollback only
```

## Manual run (debugging)

```bash
# Simulate what the cron job does, manually:
hermes chat -q "$(cat /tmp/hermes_prompt.txt)" \
  --yolo --skills learning-portal-curator
```

Or run the skill's self-contained python script directly:
```bash
python3 /tmp/curate_today.py
```

## Adding a source

Edit `feeds.md` in the skill directory — the next run picks it up
automatically. No code change needed.

**Security:** this repo is public. It contains only generated content and
templates — no API keys, no nginx configs, no infra files. The skill lives in
the user's `~/.hermes/skills/` (private).
