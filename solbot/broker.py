"""Symulowany broker (fikcyjne pieniądze) – wspólny dla backtestu i bota live-paper."""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .fees import FeeModel


@dataclass
class Trade:
    ts: int              # unix seconds
    side: str            # "BUY" / "SELL"
    ref_price: float     # cena rynkowa
    exec_price: float    # cena po poślizgu
    sol: float           # ilość SOL
    usd: float           # USD wydane (BUY) lub otrzymane (SELL) – netto po wszystkich opłatach
    fee_pct_usd: float   # prowizja procentowa
    slippage_usd: float  # koszt poślizgu
    network_usd: float   # opłata sieciowa (SOL przeliczony na USD)
    reason: str = ""
    pnl_usd: float = 0.0 # tylko dla SELL: zysk netto pozycji (po opłatach obu stron)

    @property
    def total_fees_usd(self) -> float:
        return self.fee_pct_usd + self.slippage_usd + self.network_usd

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_fees_usd"] = self.total_fees_usd
        return d


class PaperBroker:
    def __init__(self, fees: FeeModel, cash_usd: float = 1000.0, sol: float = 0.0):
        self.fees = fees
        self.start_cash = cash_usd
        self.cash = cash_usd
        self.sol = sol
        self.trades: list[Trade] = []
        self.fees_paid_usd = 0.0
        self._one_time_paid = False
        self._position_cost = 0.0   # ile USD (z opłatami) kosztowała otwarta pozycja
        self.entry_price = None     # referencyjna cena wejścia

    # -------------------------------------------------------------------------
    def equity(self, price: float) -> float:
        return self.cash + self.sol * price

    @property
    def in_position(self) -> bool:
        return self.sol > 1e-12

    def buy(self, price: float, usd_amount: float, ts: int, reason: str = "") -> Trade | None:
        f = self.fees
        usd_amount = min(usd_amount, self.cash)
        net_fee_usd = f.network_fee_usd(price)
        if not self._one_time_paid and f.one_time_sol:
            net_fee_usd += f.one_time_sol * price
        spend = usd_amount - net_fee_usd            # na sam swap
        if spend <= 0 or spend * f.pct_fee >= spend:
            return None
        pct_fee = spend * f.pct_fee
        exec_price = price * (1 + f.slippage_pct)
        sol_out = (spend - pct_fee) / exec_price
        slip_usd = sol_out * (exec_price - price)
        self._one_time_paid = True
        self.cash -= usd_amount
        self.sol += sol_out
        self._position_cost += usd_amount
        self.entry_price = price if self.entry_price is None else self.entry_price
        t = Trade(ts, "BUY", price, exec_price, sol_out, usd_amount, pct_fee, slip_usd, net_fee_usd, reason)
        self._record(t)
        return t

    def sell(self, price: float, sol_amount: float | None, ts: int, reason: str = "") -> Trade | None:
        f = self.fees
        sol_amount = self.sol if sol_amount is None else min(sol_amount, self.sol)
        if sol_amount <= 0:
            return None
        exec_price = price * (1 - f.slippage_pct)
        gross = sol_amount * exec_price
        pct_fee = gross * f.pct_fee
        net_fee_usd = f.network_fee_usd(price)
        usd_in = gross - pct_fee - net_fee_usd
        slip_usd = sol_amount * (price - exec_price)
        frac = sol_amount / self.sol
        cost = self._position_cost * frac
        self.sol -= sol_amount
        self.cash += usd_in
        self._position_cost -= cost
        if self.sol <= 1e-12:
            self.sol = 0.0
            self._position_cost = 0.0
            self.entry_price = None
        t = Trade(ts, "SELL", price, exec_price, sol_amount, usd_in, pct_fee, slip_usd, net_fee_usd, reason,
                  pnl_usd=usd_in - cost)
        self._record(t)
        return t

    def _record(self, t: Trade):
        self.trades.append(t)
        self.fees_paid_usd += t.total_fees_usd

    # ---- serializacja (dla bota live) ----------------------------------------
    def to_dict(self) -> dict:
        return {
            "fees": self.fees.to_dict(), "start_cash": self.start_cash, "cash": self.cash, "sol": self.sol,
            "fees_paid_usd": self.fees_paid_usd, "one_time_paid": self._one_time_paid,
            "position_cost": self._position_cost, "entry_price": self.entry_price,
            "trades": [t.to_dict() for t in self.trades],
        }

    @classmethod
    def from_dict(cls, d: dict, fees: FeeModel) -> "PaperBroker":
        b = cls(fees, d["start_cash"], 0.0)
        b.cash, b.sol = d["cash"], d["sol"]
        b.fees_paid_usd = d.get("fees_paid_usd", 0.0)
        b._one_time_paid = d.get("one_time_paid", False)
        b._position_cost = d.get("position_cost", 0.0)
        b.entry_price = d.get("entry_price")
        for td in d.get("trades", []):
            td = {k: v for k, v in td.items() if k in Trade.__dataclass_fields__}
            b.trades.append(Trade(**td))
        return b
