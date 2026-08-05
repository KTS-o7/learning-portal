#!/bin/bash
# Test-launch the learning-portal-curator skill via hermes chat -q.
set -u
cd /opt/learning-portal || exit 1
export MINIMAX_API_KEY="$(grep '^MINIMAX_API_KEY=' /root/.hermes/.env | cut -d= -f2-)"
export OPENCODE_ZEN_API_KEY="$(grep '^OPENCODE_ZEN_API_KEY=' /root/.hermes/.env | cut -d= -f2-)"
export KIMI_API_KEY="$(grep '^KIMI_API_KEY=' /root/.hermes/.env | cut -d= -f2-)"

# Delete old curate artifacts so we generate fresh.
rm -f /tmp/curate_today.py /tmp/check.html

prompt="$(cat /tmp/hermes_prompt.txt)"

# Run hermes as a one-shot. --yolo because git commit/push and curl need no prompts.
timeout 540 hermes chat -q "$prompt" --yolo --skills learning-portal-curator 2>&1 | tail -120
