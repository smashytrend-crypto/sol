"""Pobieranie danych cenowych SOL (bez kluczy API).

* Historia: świece 1-minutowe z GeckoTerminal (pula SOL/USDC na Orca – największa płynność).
* Cena live: Jupiter Price API, awaryjnie CoinGecko.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from dataclasses import dataclass, field
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
    ctx: dict = field(default_factory=dict)  # dodatkowe obserwacje: ask/bid SOL, BTC, ETH, JUP, BONK…


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


CTX_KEYS = ["ask", "bid", "BTC", "ETH", "JUP", "BONK", "sol_chg24h"]


def bar_row(b: Bar, ctx_keys=CTX_KEYS) -> list:
    return [b.ts, f"{b.open:.6f}", f"{b.high:.6f}", f"{b.low:.6f}", f"{b.close:.6f}", f"{b.volume:.2f}"] + \
        [("" if b.ctx.get(k) is None else f"{b.ctx[k]:.10g}") for k in ctx_keys]


BAR_HEADER = ["ts", "open", "high", "low", "close", "volume"] + [f"ctx_{k}" for k in CTX_KEYS]


def save_bars(bars: list[Bar], path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(BAR_HEADER)
        for b in bars:
            w.writerow(bar_row(b))


def load_bars(path: str | Path) -> list[Bar]:
    out = []
    with open(path, newline="") as f:
        for x in csv.DictReader(f):
            ctx = {k[4:]: float(v) for k, v in x.items() if k and k.startswith("ctx_") and v not in ("", None)}
            out.append(Bar(int(x["ts"]), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]),
                           float(x.get("volume") or 0), ctx))
    return out


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


# ---- obserwacja rynku (wszystko na żywo, bez historii z zewnątrz) -------------
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CONTEXT_MINTS = {
    "BTC": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",   # cbBTC
    "ETH": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",   # WETH (Wormhole)
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
}
JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote?inputMint={i}&outputMint={o}&amount={a}&slippageBps=50"


def fetch_market(quote_usd: float = 1000.0) -> dict:
    """Jedna obserwacja rynku:
      price – cena SOL (Jupiter), ask/bid – REALNA cena kupna/sprzedaży SOL wg notowania swapu Jupitera
      dla pozycji ~quote_usd (zawiera opłatę puli i wpływ na cenę), BTC/ETH/JUP/BONK – kontekst rynku."""
    ids = ",".join([SOL_MINT] + list(CONTEXT_MINTS.values()))
    out: dict = {"source": "jupiter"}
    try:
        d = _get_json(f"https://lite-api.jup.ag/price/v3?ids={ids}", timeout=8)
        out["price"] = float(d[SOL_MINT]["usdPrice"])
        out["sol_chg24h"] = float(d[SOL_MINT].get("priceChange24h") or 0)
        for k, m in CONTEXT_MINTS.items():
            if m in d:
                out[k] = float(d[m]["usdPrice"])
    except Exception:
        out["price"], out["source"] = fetch_price()
    price = out["price"]
    try:
        q = _get_json(JUP_QUOTE.format(i=USDC_MINT, o=SOL_MINT, a=int(quote_usd * 1e6)), timeout=8)
        out["ask"] = (int(q["inAmount"]) / 1e6) / (int(q["outAmount"]) / 1e9)
        sol_amt = quote_usd / price
        q = _get_json(JUP_QUOTE.format(i=SOL_MINT, o=USDC_MINT, a=int(sol_amt * 1e9)), timeout=8)
        out["bid"] = (int(q["outAmount"]) / 1e6) / (int(q["inAmount"]) / 1e9)
        # odrzucamy notowania wyraźnie błędne
        if not (price * 0.97 < out["bid"] <= out["ask"] < price * 1.03):
            out.pop("ask"), out.pop("bid")
    except Exception:
        pass
    return out


class BarAggregator:
    """Składa ticki (cena co kilka sekund) w świece o zadanej długości."""

    def __init__(self, step: int = 60):
        self.step = step
        self.cur: Bar | None = None

    def update(self, ts: float, price: float, ctx: dict | None = None) -> Bar | None:
        """Zwraca zamkniętą świecę, jeśli tick otworzył nową. ctx = ostatnie wartości dodatkowych obserwacji."""
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
        if ctx:
            self.cur.ctx.update({k: v for k, v in ctx.items() if isinstance(v, (int, float))})
        self.cur.volume += 1  # liczba obserwacji w świecy
        return closed
