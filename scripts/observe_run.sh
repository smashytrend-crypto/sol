#!/usr/bin/env bash
# Uruchamia obserwatora (ok. 2 h) z danymi poza repozytorium; wznawia od zapisanego stanu.
cd "$(dirname "$0")/.."
LIVE=${SOLBOT_LIVE:-/home/user/solbot-live}
if [ ! -d "$LIVE" ] && [ -d data/live ]; then cp -r data/live "$LIVE"; fi
mkdir -p "$LIVE"
PYTHONUNBUFFERED=1 python3 -m solbot --data "$LIVE" observe --interval 10 --duration "${1:-7080}" --port 8050 >> "$LIVE/observe.log" 2>&1
tail -5 "$LIVE/observe.log"
