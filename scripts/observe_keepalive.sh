#!/usr/bin/env bash
# Zapisuje postęp obserwacji w repozytorium (git commit + push danych z data/live).
set -u
cd "$(dirname "$0")/.."
BR=claude/solana-trading-bot-j661h4
git add data/live >/dev/null 2>&1
if ! git diff --cached --quiet; then
  BARS=$(($(wc -l < data/live/live_bars.csv 2>/dev/null || echo 1) - 1))
  git commit -qm "Obserwacja SOL: ${BARS} świec 1m zebranych na żywo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XTkCBguhbBwqLuiskDKkmU"
  for d in 2 4 8 16; do git push -q -u origin "$BR" && break; sleep $d; done
fi
pgrep -f "solbot observe" >/dev/null && echo "obserwator: działa" || echo "obserwator: NIE działa"
