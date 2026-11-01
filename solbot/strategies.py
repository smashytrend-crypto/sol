"""Strategie nastawione na szybkie ruchy ceny SOL.

Każda strategia dostaje świece (zamknięcie świecy) i zwraca sygnał "BUY" / "SELL" / None.
Wspólne parametry wyjścia (obsługiwane przez silnik):
  take_profit  – zysk (ułamek), przy którym zamykamy pozycję (0 = wyłączone)
  stop_loss    – strata (ułamek), przy której tniemy pozycję (0 = wyłączone)
  trail_pct    – trailing stop od szczytu od wejścia (0 = wyłączone)
  max_hold     – maks. liczba świec w pozycji (0 = bez limitu)
  cooldown     – ile świec odczekać po zamknięciu pozycji
  position_pct – jaka część kapitału idzie w pozycję
"""
from __future__ import annotations

from .data import Bar
from .indicators import EMA, RSI, RollingStats, Window

COMMON_DEFAULTS = dict(take_profit=0.01, stop_loss=0.01, trail_pct=0.0, max_hold=0, cooldown=0, position_pct=0.95)
COMMON_SPACE = dict(
    take_profit=[0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05],
    stop_loss=[0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05],
    trail_pct=[0.0, 0.0, 0.004, 0.0075, 0.01, 0.02],
    max_hold=[0, 30, 60, 120, 240, 720],
    cooldown=[0, 5, 15, 30, 60],
)


class Strategy:
    name = "base"
    DEFAULTS: dict = {}
    SPACE: dict = {}
    WARMUP_KEYS: tuple = ()

    def __init__(self, **params):
        p = {**COMMON_DEFAULTS, **self.DEFAULTS}
        unknown = set(params) - set(p)
        if unknown:
            raise ValueError(f"{self.name}: nieznane parametry {unknown}")
        p.update(params)
        self.p = p
        self.in_pos = False
        self.entry_price = 0.0
        self.peak = 0.0
        self.held = 0
        self.cool = 0
        self.setup()

    # --- do nadpisania ---------------------------------------------------------
    def setup(self):
        pass

    def update(self, bar: Bar):
        """Aktualizacja wskaźników."""

    def entry_signal(self, bar: Bar) -> bool:
        return False

    def exit_signal(self, bar: Bar) -> bool:
        return False

    @property
    def warmup(self) -> int:
        return max([int(self.p.get(k, 0)) for k in self.WARMUP_KEYS] + [1]) + 1

    # --- logika wspólna --------------------------------------------------------
    def on_bar(self, bar: Bar) -> str | None:
        self.update(bar)
        if self.in_pos:
            self.held += 1
            self.peak = max(self.peak, bar.high)
            if self.p["max_hold"] and self.held >= self.p["max_hold"]:
                return "SELL"
            if self.exit_signal(bar):
                return "SELL"
            return None
        if self.cool > 0:
            self.cool -= 1
            return None
        return "BUY" if self.entry_signal(bar) else None

    def levels(self) -> tuple[float | None, float | None]:
        """(cena take-profit, cena stop-loss) dla otwartej pozycji."""
        tp = self.entry_price * (1 + self.p["take_profit"]) if self.p["take_profit"] else None
        sl = self.entry_price * (1 - self.p["stop_loss"]) if self.p["stop_loss"] else None
        if self.p["trail_pct"]:
            tsl = self.peak * (1 - self.p["trail_pct"])
            sl = tsl if sl is None else max(sl, tsl)
        return tp, sl

    def on_entry(self, price: float):
        self.in_pos = True
        self.entry_price = price
        self.peak = price
        self.held = 0

    def on_exit(self):
        self.in_pos = False
        self.cool = int(self.p["cooldown"])

    def describe(self) -> str:
        return f"{self.name}(" + ", ".join(f"{k}={v}" for k, v in self.p.items()) + ")"


class DipBuy(Strategy):
    """Łapanie szybkich spadków: kupno gdy cena spadła o `drop_pct` w ciągu `lookback` świec
    (opcjonalnie po pierwszym odbiciu), sprzedaż na TP/SL/trailing."""
    name = "dip_buy"
    DEFAULTS = dict(lookback=15, drop_pct=0.01, confirm=1)
    SPACE = dict(lookback=[3, 5, 10, 15, 30, 60, 120], drop_pct=[0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03],
                 confirm=[0, 1])
    WARMUP_KEYS = ("lookback",)

    def setup(self):
        self.win = Window(int(self.p["lookback"]) + 1)
        self.prev_close = None

    def update(self, bar):
        self._pc = self.prev_close
        self.win.push(bar.high)
        self.prev_close = bar.close

    def entry_signal(self, bar):
        if not self.win.ready:
            return False
        dropped = bar.close <= self.win.max() * (1 - self.p["drop_pct"])
        if not dropped:
            return False
        return (not self.p["confirm"]) or (self._pc is not None and bar.close > self._pc)


class MeanReversion(Strategy):
    """Odchylenie od średniej (z-score / wstęgi Bollingera): kupno poniżej -entry_z, sprzedaż powyżej exit_z."""
    name = "mean_reversion"
    DEFAULTS = dict(window=60, entry_z=2.0, exit_z=0.0)
    SPACE = dict(window=[15, 30, 60, 120, 240], entry_z=[1.5, 2.0, 2.5, 3.0, 3.5], exit_z=[-0.5, 0.0, 0.5, 1.0, 2.0])
    WARMUP_KEYS = ("window",)

    def setup(self):
        self.rs = RollingStats(int(self.p["window"]))
        self.z = 0.0

    def update(self, bar):
        self.rs.push(bar.close)
        sd = self.rs.std
        self.z = (bar.close - self.rs.mean) / sd if sd > 0 else 0.0

    def entry_signal(self, bar):
        return self.rs.ready and self.z <= -self.p["entry_z"]

    def exit_signal(self, bar):
        return self.z >= self.p["exit_z"]


class RSIStrategy(Strategy):
    """RSI: kupno przy wyprzedaniu, sprzedaż przy wykupieniu."""
    name = "rsi"
    DEFAULTS = dict(period=14, low=25, high=70)
    SPACE = dict(period=[7, 14, 21, 30], low=[15, 20, 25, 30, 35], high=[55, 60, 65, 70, 75, 80])
    WARMUP_KEYS = ("period",)

    def setup(self):
        self.rsi = RSI(int(self.p["period"]))
        self.v = None

    def update(self, bar):
        self.v = self.rsi.push(bar.close)

    def entry_signal(self, bar):
        return self.v is not None and self.v <= self.p["low"]

    def exit_signal(self, bar):
        return self.v is not None and self.v >= self.p["high"]


class Breakout(Strategy):
    """Momentum: kupno, gdy cena wybija ponad maksimum z `lookback` świec (+ próg), wyjście trailing stopem."""
    name = "breakout"
    DEFAULTS = dict(lookback=60, threshold=0.001)
    SPACE = dict(lookback=[15, 30, 60, 120, 240], threshold=[0.0, 0.001, 0.002, 0.004, 0.008])
    WARMUP_KEYS = ("lookback",)

    def setup(self):
        self.win = Window(int(self.p["lookback"]))
        self.prev_max = None

    def update(self, bar):
        self.prev_max = self.win.max() if self.win.ready else None
        self.win.push(bar.high)

    def entry_signal(self, bar):
        return self.prev_max is not None and bar.close > self.prev_max * (1 + self.p["threshold"])


class EMACross(Strategy):
    """Przecięcie średnich EMA (trend)."""
    name = "ema_cross"
    DEFAULTS = dict(fast=12, slow=48)
    SPACE = dict(fast=[5, 9, 12, 20, 30], slow=[30, 48, 60, 120, 240])
    WARMUP_KEYS = ("slow",)

    def setup(self):
        self.f, self.s = EMA(int(self.p["fast"])), EMA(int(self.p["slow"]))
        self.diff = self.prev_diff = None
        self.n = 0

    def update(self, bar):
        self.prev_diff = self.diff
        self.diff = self.f.push(bar.close) - self.s.push(bar.close)
        self.n += 1

    def entry_signal(self, bar):
        return (self.n > self.p["slow"] and self.prev_diff is not None
                and self.prev_diff <= 0 < self.diff)

    def exit_signal(self, bar):
        return self.diff is not None and self.diff < 0


STRATEGIES: dict[str, type[Strategy]] = {c.name: c for c in (DipBuy, MeanReversion, RSIStrategy, Breakout, EMACross)}


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"Nieznana strategia {name}. Dostępne: {', '.join(STRATEGIES)}")
    return STRATEGIES[name](**(params or {}))


def param_space(name: str) -> dict:
    cls = STRATEGIES[name]
    return {**COMMON_SPACE, **cls.SPACE}
