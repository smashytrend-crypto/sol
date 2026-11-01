"""„Nauka” handlu: przeszukiwanie parametrów strategii na danych historycznych.

Aby uniknąć dopasowania do szumu (overfittingu) dane dzielimy chronologicznie:
  * część TRENINGOWA (domyślnie pierwsze 70%) – tu losujemy i oceniamy kombinacje parametrów,
  * część TESTOWA (ostatnie 30%) – najlepsze kandydaty sprawdzamy na danych, których „nie widziały”.
Wybieramy konfigurację, która zarabia na OBU częściach (po opłatach). Jeśli żadna nie zarabia,
uczciwą rekomendacją jest… nie handlować (trzymać USDC) – bot to zgłosi.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .backtest import Result, format_result, run_backtest
from .data import Bar
from .fees import FeeModel
from .strategies import STRATEGIES, param_space


def _sample(space: dict, rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in space.items()}


def _valid(params: dict, fees: FeeModel) -> bool:
    # take-profit musi pokrywać koszt pełnego cyklu z zapasem – inaczej każda „wygrana” jest stratą
    rt = fees.round_trip_pct()
    if params.get("take_profit") and params["take_profit"] < rt * 1.5:
        return False
    if params.get("trail_pct") and params["trail_pct"] < rt:
        return False
    if "fast" in params and params["fast"] >= params["slow"]:
        return False
    if "low" in params and params["low"] >= params["high"]:
        return False
    return True


def score(r: Result, min_trades: int) -> float:
    """Zwrot dzienny z karą za obsunięcie i za zbyt małą liczbę transakcji."""
    if r.trades < min_trades:
        return -1e9
    return r.return_per_day_pct - 0.1 * r.max_drawdown_pct / max(r.days, 1e-9)


def optimize(bars: list[Bar], fees: FeeModel, strategies: list[str] | None = None, samples: int = 200,
             train_frac: float = 0.7, top_k: int = 8, seed: int = 42, min_trades: int = 3,
             start_cash: float = 1000.0, verbose: bool = True, fill: str = "close") -> dict:
    strategies = strategies or list(STRATEGIES)
    rng = random.Random(seed)
    cut = int(len(bars) * train_frac)
    train, test = bars[:cut], bars[cut:]
    t0 = time.time()
    leaderboard = []
    for name in strategies:
        space = param_space(name)
        seen, cands = set(), []
        tries = 0
        while len(cands) < samples and tries < samples * 20:
            tries += 1
            p = _sample(space, rng)
            key = tuple(sorted(p.items()))
            if key in seen or not _valid(p, fees):
                continue
            seen.add(key)
            r = run_backtest(train, name, fees, p, start_cash, fill=fill)
            cands.append((score(r, min_trades), p, r))
        cands.sort(key=lambda x: x[0], reverse=True)
        for sc, p, rtrain in cands[:top_k]:
            if sc <= -1e8:
                continue
            rtest = run_backtest(test, name, fees, p, start_cash, fill=fill)
            combined = min(score(rtrain, min_trades), score(rtest, 1))
            leaderboard.append(dict(strategy=name, params=p, train=rtrain.summary(), test=rtest.summary(),
                                    robust_score=combined))
        if verbose:
            best = next((x for x in sorted(leaderboard, key=lambda x: -x["robust_score"]) if x["strategy"] == name), None)
            if best:
                print(f"  {name:15s} najlepszy: trening {best['train']['return_pct']:+.2f}% | "
                      f"test {best['test']['return_pct']:+.2f}% | transakcje {best['train']['trades']}+{best['test']['trades']}")
            else:
                print(f"  {name:15s} brak kandydatów z wystarczającą liczbą transakcji")
    leaderboard.sort(key=lambda x: x["robust_score"], reverse=True)
    best = leaderboard[0] if leaderboard else None
    profitable = bool(best and best["train"]["return_pct"] > 0 and best["test"]["return_pct"] > 0)
    full = run_backtest(bars, best["strategy"], fees, best["params"], start_cash, fill=fill) if best else None
    report = dict(
        created=int(time.time()), bars=len(bars), from_ts=bars[0].ts, to_ts=bars[-1].ts,
        train_bars=len(train), test_bars=len(test), fee_model=fees.to_dict(),
        round_trip_cost_pct=fees.round_trip_pct() * 100, fill=fill,
        best=best, profitable=profitable, full_period=full.summary() if full else None,
        leaderboard=leaderboard[:25], seconds=round(time.time() - t0, 1),
        buy_hold_pct=(bars[-1].close / bars[0].open - 1) * 100,
    )
    return report


def save_config(report: dict, path: str | Path):
    best = report["best"]
    cfg = dict(strategy=best["strategy"], params=best["params"], fees=report["fee_model"],
               trade_enabled=report["profitable"],
               learned_from=dict(from_ts=report["from_ts"], to_ts=report["to_ts"], bars=report["bars"],
                                 train_return_pct=best["train"]["return_pct"], test_return_pct=best["test"]["return_pct"]))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return cfg
