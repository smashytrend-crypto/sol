"""Backtest strategii na świecach historycznych z pełnym modelem opłat.

Zasady symulacji (konserwatywne, bez zaglądania w przyszłość):
  * sygnał z zamknięcia świecy i jest wykonywany po cenie OTWARCIA świecy i+1,
  * TP/SL sprawdzane wewnątrz świecy (high/low); gdy w jednej świecy dotknięte są oba – liczymy SL,
  * jeśli świeca otwiera się z luką poniżej SL – sprzedaż po cenie otwarcia (gorszej),
  * każda transakcja płaci prowizję %, poślizg i opłatę sieciową (patrz fees.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .broker import PaperBroker, Trade
from .data import Bar
from .fees import FeeModel
from .strategies import Strategy, make_strategy


@dataclass
class Result:
    strategy: str
    params: dict
    fee_model: str
    start_equity: float
    end_equity: float
    return_pct: float
    buy_hold_pct: float
    trades: int
    wins: int
    win_rate: float
    fees_usd: float
    max_drawdown_pct: float
    profit_factor: float
    exposure_pct: float
    days: float
    trade_log: list = field(default_factory=list, repr=False)
    equity_curve: list = field(default_factory=list, repr=False)

    @property
    def return_per_day_pct(self) -> float:
        return self.return_pct / self.days if self.days else 0.0

    def summary(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("trade_log", "equity_curve")}
        d["return_per_day_pct"] = self.return_per_day_pct
        if d["profit_factor"] == float("inf"):
            d["profit_factor"] = None  # JSON nie obsługuje nieskończoności (None = brak strat)
        return d


def run_backtest(bars: list[Bar], strategy: Strategy | str, fees: FeeModel, params: dict | None = None,
                 start_cash: float = 1000.0, keep_log: bool = False, curve_every: int = 15,
                 fill: str = "close") -> Result:
    """fill="close"   – (domyślnie, konserwatywnie) sygnał wykonany po ZAMKNIĘCIU następnej świecy,
                        TP/SL sprawdzane tylko na zamknięciach (odporne na szum i „knoty” w danych DEX);
       fill="intrabar" – (optymistycznie) wykonanie po otwarciu następnej świecy, TP/SL na high/low."""
    if fill not in ("close", "intrabar"):
        raise ValueError("fill musi być 'close' albo 'intrabar'")
    strat = make_strategy(strategy, params) if isinstance(strategy, str) else strategy
    broker = PaperBroker(fees, start_cash)
    pending: str | None = None
    peak_eq = start_cash
    max_dd = 0.0
    bars_in_pos = 0
    curve = []

    def do_sell(price, ts, reason, quote=None):
        broker.sell(price, None, ts, reason, quote=quote)
        strat.on_exit()

    for i, bar in enumerate(bars):
        px = bar.close if fill == "close" else bar.open
        # realne notowania kupna/sprzedaży (jeśli bot je obserwował) – tylko przy wykonaniu na zamknięciu
        ask = bar.ctx.get("ask") if fill == "close" else None
        bid = bar.ctx.get("bid") if fill == "close" else None
        # 1) wykonanie sygnału z poprzedniej świecy
        entered_now = False
        if pending == "BUY" and not broker.in_position:
            t = broker.buy(px, broker.equity(px) * strat.p["position_pct"], bar.ts, "signal", quote=ask)
            if t:
                strat.on_entry(px)
                entered_now = True
        elif pending == "SELL" and broker.in_position:
            do_sell(px, bar.ts, "signal", bid)
        pending = None

        # 2) TP / SL
        if broker.in_position:
            bars_in_pos += 1
            tp, sl = strat.levels()
            if fill == "close":
                if not entered_now:
                    if sl is not None and bar.close <= sl:
                        do_sell(bar.close, bar.ts, "stop_loss", bid)
                    elif tp is not None and bar.close >= tp:
                        do_sell(bar.close, bar.ts, "take_profit", bid)
            elif sl is not None and bar.open <= sl:
                do_sell(bar.open, bar.ts, "stop_loss(gap)")
            elif sl is not None and bar.low <= sl:
                do_sell(sl, bar.ts, "stop_loss")
            elif tp is not None and bar.high >= tp:
                do_sell(max(tp, bar.open), bar.ts, "take_profit")

        # 3) sygnał na zamknięciu
        pending = strat.on_bar(bar)
        if pending == "BUY" and broker.in_position:
            pending = None
        if pending == "SELL" and not broker.in_position:
            pending = None

        eq = broker.equity(bar.close)
        peak_eq = max(peak_eq, eq)
        max_dd = max(max_dd, (peak_eq - eq) / peak_eq)
        if keep_log and i % curve_every == 0:
            curve.append((bar.ts, round(eq, 4), bar.close))

    if broker.in_position and bars:
        do_sell(bars[-1].close, bars[-1].ts, "end_of_test", bars[-1].ctx.get("bid") if fill == "close" else None)
    end_eq = broker.cash

    sells = [t for t in broker.trades if t.side == "SELL"]
    wins = sum(1 for t in sells if t.pnl_usd > 0)
    gp = sum(t.pnl_usd for t in sells if t.pnl_usd > 0)
    gl = -sum(t.pnl_usd for t in sells if t.pnl_usd < 0)
    days = (bars[-1].ts - bars[0].ts) / 86400 if len(bars) > 1 else 0.0
    bh = (bars[-1].close / bars[0].open - 1) * 100 if bars else 0.0
    if keep_log and bars:
        curve.append((bars[-1].ts, round(end_eq, 4), bars[-1].close))
    return Result(
        strategy=strat.name, params=dict(strat.p), fee_model=fees.name, start_equity=start_cash, end_equity=end_eq,
        return_pct=(end_eq / start_cash - 1) * 100, buy_hold_pct=bh, trades=len(sells), wins=wins,
        win_rate=wins / len(sells) * 100 if sells else 0.0, fees_usd=broker.fees_paid_usd,
        max_drawdown_pct=max_dd * 100, profit_factor=(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        exposure_pct=bars_in_pos / len(bars) * 100 if bars else 0.0, days=days,
        trade_log=[t.to_dict() for t in broker.trades] if keep_log else [], equity_curve=curve,
    )


def format_result(r: Result) -> str:
    pf = "∞" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
    return (f"{r.strategy:15s} zwrot {r.return_pct:+7.2f}% ({r.return_per_day_pct:+.2f}%/dzień) | "
            f"kup&trzymaj {r.buy_hold_pct:+6.2f}% | transakcje {r.trades:3d} | trafność {r.win_rate:5.1f}% | "
            f"PF {pf} | opłaty ${r.fees_usd:7.2f} | maxDD {r.max_drawdown_pct:5.2f}%")
