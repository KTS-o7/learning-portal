#!/usr/bin/env python3
"""Build the per-day archive snapshot for a given edition.

Usage: build_snapshot.py <DATE>
Reads data/<DATE>.json, inlines it into the snapshot shell template,
writes archive/<DATE>.html. Uses str.replace ONLY (never .format()).
"""
import json, pathlib, sys

if len(sys.argv) != 2:
    print("usage: build_snapshot.py <YYYY-MM-DD>")
    sys.exit(2)

date = sys.argv[1]
root = pathlib.Path("/opt/learning-portal")
tmpl = (root / "../root/.hermes/skills/learning-portal-curator/templates/snapshot_shell.html.tmpl")
# Fallback: skill lives under ~/.hermes/skills/
if not tmpl.exists():
    tmpl = pathlib.Path("/root/.hermes/skills/learning-portal-curator/templates/snapshot_shell.html.tmpl")
tmpl_text = tmpl.read_text()

data_path = root / "data" / f"{date}.json"
if not data_path.exists():
    print(f"missing {data_path}")
    sys.exit(1)

data = json.load(open(data_path))
digest_json = json.dumps(data, ensure_ascii=False)

out = tmpl_text.replace("{EDITION}", date).replace("{DIGEST_JSON}", digest_json)
assert "{{" not in out and "}}" not in out, "doubled-brace bug detected"

out_path = root / "archive" / f"{date}.html"
out_path.write_text(out)
print(f"wrote {out_path} ({len(out)} bytes)")