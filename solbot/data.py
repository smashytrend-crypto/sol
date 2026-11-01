"""Pobieranie danych cenowych SOL (bez kluczy API).

* Historia: świece 1-minutowe z GeckoTerminal (pula SOL/USDC na Orca – największa płynność).
* Cena live: Jupiter Price API, awaryjnie CoinGecko.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SOL_MINT = "So11111111111111111111111111111111111111112"
# Pula SOL/USDC na Meteora – jej świece mają najmniej szumu „bid-ask bounce” spośród dużych pul
# (Orca Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE ma większy wolumen, ale mocno „poszarpane” świece 1m)
DEFAULT_POOL = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"
GT_BASE = "https://api.geckoterminal.com/api/v2/networks/solana/pools"
JUP_PRICE = f"https://lite-api.jup.ag/price/v3?ids={SOL_MINT}"
CG_PRICE = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
UA = {"User-Agent": "solbot/0.1 (paper trading demo)", "Accept": "application/json"}


@dataclass
class Bar:
    ts: int          # początek świecy (unix s)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---- historia ----------------------------------------------------------------
def fetch_history(days: float = 7.0, pool: str = DEFAULT_POOL, timeframe: str = "minute",
                  aggregate: int = 1, verbose: bool = True) -> list[Bar]:
    """Pobiera świece z ostatnich `days` dni (stronicowanie po 1000 świec)."""
    need_from = int(time.time() - days * 86400)
    bars: dict[int, Bar] = {}
    before = None
    while True:
        url = f"{GT_BASE}/{pool}/ohlcv/{timeframe}?aggregate={aggregate}&limit=1000&currency=usd"
        if before:
            url += f"&before_timestamp={before}"
        for attempt in range(5):
            try:
                data = _get_json(url)
                break
            except Exception as e:  # limit zapytań (30/min) lub chwilowy błąd
                wait = 3 * (attempt + 1)
                if verbose:
                    print(f"  błąd pobierania ({e}); ponawiam za {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError("Nie udało się pobrać danych z GeckoTerminal")
        rows = data["data"]["attributes"]["ohlcv_list"]
        if not rows:
            break
        for ts, o, h, l, c, v in rows:
            bars[int(ts)] = Bar(int(ts), float(o), float(h), float(l), float(c), float(v))
        oldest = min(int(r[0]) for r in rows)
        if verbose:
            print(f"  pobrano {len(bars)} świec (do {time.strftime('%Y-%m-%d %H:%M', time.gmtime(oldest))} UTC)")
        if oldest <= need_from or len(rows) < 1000:
            break
        before = oldest
        time.sleep(2.2)  # szanujemy limit API
    out = [b for b in sorted(bars.values(), key=lambda b: b.ts) if b.ts >= need_from]
    return fill_gaps(out, 60 * aggregate if timeframe == "minute" else 3600 * aggregate)


def fill_gaps(bars: list[Bar], step: int) -> list[Bar]:
    """GeckoTerminal pomija minuty bez transakcji – uzupełniamy je płaską świecą."""
    if not bars:
        return bars
    out = [bars[0]]
    for b in bars[1:]:
        prev = out[-1]
        t = prev.ts + step
        while t < b.ts:
            out.append(Bar(t, prev.close, prev.close, prev.close, prev.close, 0.0))
            t += step
        out.append(b)
    return out


def save_bars(bars: list[Bar], path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.ts, f"{b.open:.6f}", f"{b.high:.6f}", f"{b.low:.6f}", f"{b.close:.6f}", f"{b.volume:.2f}"])


def load_bars(path: str | Path) -> list[Bar]:
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        return [Bar(int(x["ts"]), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]),
                    float(x.get("volume") or 0)) for x in r]


def merge_bars(*series: list[Bar]) -> list[Bar]:
    d: dict[int, Bar] = {}
    for s in series:
        for b in s:
            d[b.ts] = b
    return [d[k] for k in sorted(d)]


# ---- cena live ---------------------------------------------------------------
def fetch_price() -> tuple[float, str]:
    """Zwraca (cena SOL w USD, źródło)."""
    try:
        d = _get_json(JUP_PRICE, timeout=8)
        return float(d[SOL_MINT]["usdPrice"]), "jupiter"
    except Exception:
        d = _get_json(CG_PRICE, timeout=8)
        return float(d["solana"]["usd"]), "coingecko"


class BarAggregator:
    """Składa ticki (cena co kilka sekund) w świece o zadanej długości."""

    def __init__(self, step: int = 60):
        self.step = step
        self.cur: Bar | None = None

    def update(self, ts: float, price: float) -> Bar | None:
        """Zwraca zamkniętą świecę, jeśli tick otworzył nową."""
        start = int(ts // self.step * self.step)
        closed = None
        if self.cur is None:
            self.cur = Bar(start, price, price, price, price)
        elif start != self.cur.ts:
            closed = self.cur
            self.cur = Bar(start, price, price, price, price)
        else:
            c = self.cur
            c.high, c.low, c.close = max(c.high, price), min(c.low, price), price
        return closed
