#!/usr/bin/env bash
# Build today's Daily Byte edition, validate it, then commit and push.
# Run daily by cron (Hermes no_agent job). Any failure exits non-zero before
# anything is committed, so the last good edition stays live.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only -q origin main
python3 scripts/build_edition.py >/tmp/dailybyte-build.log 2>&1 || { tail -n 30 /tmp/dailybyte-build.log; exit 1; }

DATE=$(date -u +%Y-%m-%d)
TOTAL=$(python3 - "$DATE" <<'PY'
import json, re, sys, pathlib
date = sys.argv[1]
d = json.load(open(f"data/{date}.json"))
assert d["date"] == date, "date mismatch"
total = sum(len(s["stories"]) for s in d["sections"])
assert total == d["total"] and 5 <= total <= 14, f"bad story count {total}"
assert all(st.get("summary") for s in d["sections"] for st in s["stories"]), "empty summary"
html = pathlib.Path(f"archive/{date}.html").read_text()
assert "{{" not in html, "doubled braces in snapshot"
m = re.search(r'<script type="application/json" id="digest-data">([\s\S]*?)</script>', html)
assert m and json.loads(m.group(1).replace("<\\/", "</"))["total"] == total, "snapshot JSON mismatch"
assert json.load(open("data/latest.json"))["date"] == date, "latest.json not updated"
assert f"{date}.html" in pathlib.Path("archive/index.html").read_text(), "archive index missing today"
print(total)
PY
)

git add "data/$DATE.json" data/latest.json rss.xml "archive/$DATE.html" archive/index.html archive.json
git -c user.email='hermes@nous.local' -c user.name='Hermes' commit -q -m "edition $DATE — $TOTAL stories"
git push -q origin main
echo "📚 Daily Byte · $DATE · $TOTAL stories · https://learn.shenthar.me"
