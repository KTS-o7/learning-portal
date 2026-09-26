# Daily Byte 📚

A byte-size daily learning digest — curated tech/CS/AI papers, discussions,
and engineering blog posts, published as a static newspaper-style HTML page.

**Live at:** [learn.shenthar.me](https://learn.shenthar.me)
**Generated daily by:** Hermes agent (cron, 01:30 UTC = 07:00 IST)

## How it works

Every day at 01:30 UTC (07:00 IST) a cron job runs `scripts/publish.sh`
(a Hermes `no_agent` job — plain script, no LLM agent with shell access):

1. **Fetch** engineering blogs (independent writers first, one vendor/project
   blog at most), arXiv (cs.DC/DB/OS/PL/SE/PF), the Hacker News front page and
   Lobsters. Sources live in `ENGINEERING_FEEDS` / `ARXIV_CATS` in
   `scripts/build_edition.py`.
2. **Never rerun a story**: anything already published in `data/*.json` is skipped.
3. **Select ~10 stories**: engineering 3, papers 2, tools 2, discussions 2 and
   one "Off the clock" pick. Papers and discussions are ranked by MiniMax-M3
   against the reader profile (`READER`).
4. **Summarise** each story as *why read* / *gist* / *takeaway* (+ what the
   comment thread argues about, for discussions), with reading time and level.
5. **Edit**: one call writes the editor's note, the "Start here" pick and the
   section intros from the day's actual stories.
6. **Publish**: `data/<DATE>.json`, `data/latest.json`, `rss.xml`,
   `archive/<DATE>.html`, `archive/index.html`, `archive.json`, validated, then
   committed and pushed. nginx serves `/opt/learning-portal/`.

`index.html` is a static shell: `assets/app.js` fetches `data/latest.json`, so
the homepage never needs regenerating. Archive pages inline their JSON via
`templates/snapshot_shell.html.tmpl`.

## Manual run

```bash
python3 scripts/build_edition.py --dry-run   # preview → /tmp/dailybyte-preview/<DATE>.json
scripts/publish.sh                          # build, validate, commit, push
```

**Security:** this repo is public. It contains only generated content and
templates — no API keys, no nginx configs, no infra files. The skill lives in
the user's `~/.hermes/skills/` (private).
