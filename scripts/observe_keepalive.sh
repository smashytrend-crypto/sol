#!/usr/bin/env bash
# Bot zapisuje obserwacje poza repozytorium ($LIVE), a ten skrypt co godzinę kopiuje migawkę do data/live,
# robi commit i push. Dzięki temu repozytorium jest czyste między zapisami.
set -u
cd "$(dirname "$0")/.."
BR=claude/solana-trading-bot-j661h4
LIVE=${SOLBOT_LIVE:-/home/user/solbot-live}
if [ ! -d "$LIVE" ] && [ -d data/live ]; then cp -r data/live "$LIVE"; fi   # nowy kontener: wznów z repo
mkdir -p data/live
if [ -d "$LIVE" ]; then
  for f in ticks.csv live_bars.csv paper_state.json paper_snapshot.json learn_report.json best_config.json observe.log; do
    [ -f "$LIVE/$f" ] && cp "$LIVE/$f" "data/live/$f.tmp" && mv "data/live/$f.tmp" "data/live/$f"
  done
fi
git add data/live >/dev/null 2>&1
if ! git diff --cached --quiet; then
  BARS=$(($(wc -l < data/live/live_bars.csv 2>/dev/null || echo 1) - 1))
  git commit -qm "Obserwacja SOL: ${BARS} świec 1m zebranych na żywo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XTkCBguhbBwqLuiskDKkmU"
fi
for d in 2 4 8 16; do git push -q -u origin "$BR" 2>/dev/null && break; sleep $d; done
pgrep -f "python3 -m solbot --data" >/dev/null && echo "obserwator: działa" || echo "obserwator: NIE działa"
