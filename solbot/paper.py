"""Bot demo (paper trading) na żywych cenach SOL – fikcyjne pieniądze, prawdziwe opłaty w symulacji.

Co robi w pętli (domyślnie co 10 s):
  1. pobiera cenę SOL (Jupiter, awaryjnie CoinGecko) i zapisuje ją (obserwacja rynku → data/ticks.csv),
  2. składa ticki w świece 1-minutowe (data/live_bars.csv),
  3. na każdym ticku pilnuje take-profit / stop-loss otwartej pozycji,
  4. na zamknięciu świecy pyta strategię o sygnał i wykonuje symulowaną transakcję z opłatami,
  5. co `relearn_hours` godzin ponownie „uczy się” na ostatnich 7 dniach (historia + własne obserwacje)
     i – jeśli znajdzie lepszą, zyskowną po opłatach konfigurację – przełącza się na nią.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
import traceback
from pathlib import Path

from .broker import PaperBroker
from .data import BarAggregator, fetch_history, fetch_price, load_bars, merge_bars, save_bars, Bar
from .fees import get_fee_model
from .optimize import optimize, save_config
from .strategies import make_strategy

DEFAULT_CONFIG = dict(
    strategy="breakout",
    params=dict(take_profit=0.03, stop_loss=0.02, trail_pct=0.01, max_hold=120, cooldown=30, lookback=240,
                threshold=0.001),
    fees="jupiter",
    trade_enabled=True,
)


def atomic_write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False))
    os.replace(tmp, path)


def load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return dict(DEFAULT_CONFIG)


class PaperBot:
    def __init__(self, data_dir: str | Path = "data", config_path: str | Path | None = None,
                 start_cash: float = 1000.0, interval: float = 10.0, relearn_hours: float = 24.0,
                 learn_samples: int = 120, reset: bool = False, log=print):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else self.dir / "best_config.json"
        self.state_path = self.dir / "paper_state.json"
        self.ticks_path = self.dir / "ticks.csv"
        self.bars_path = self.dir / "live_bars.csv"
        self.interval = interval
        self.relearn_hours = relearn_hours
        self.learn_samples = learn_samples
        self.log_fn = log
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.equity_curve: list[list] = []
        self.price_curve: list[list] = []
        self.last_price = None
        self.last_source = None
        self.agg = BarAggregator(60)
        self.cfg = load_config(self.config_path)
        self.fees = get_fee_model(self.cfg.get("fees", "jupiter"))
        self.strategy = make_strategy(self.cfg["strategy"], self.cfg.get("params"))
        self.trade_enabled = bool(self.cfg.get("trade_enabled", True))
        self.started = int(time.time())
        self.last_learn = 0.0
        self.pending_cfg = None

        if self.state_path.exists() and not reset:
            st = json.loads(self.state_path.read_text())
            self.broker = PaperBroker.from_dict(st["broker"], self.fees)
            self.events = st.get("events", [])[-300:]
            self.equity_curve = st.get("equity_curve", [])
            self.price_curve = st.get("price_curve", [])
            self.started = st.get("started", self.started)
            self.last_learn = st.get("last_learn", 0.0)
            self.log(f"Wznowiono stan: gotówka ${self.broker.cash:.2f}, SOL {self.broker.sol:.6f}")
        else:
            self.broker = PaperBroker(self.fees, start_cash)
            self.log(f"Nowe konto demo: ${start_cash:.2f} fikcyjnych USD")
        if not self.last_learn and self.config_path.exists():
            self.last_learn = self.config_path.stat().st_mtime

    # ------------------------------------------------------------------------
    def log(self, msg: str, kind: str = "info"):
        e = dict(ts=int(time.time()), kind=kind, msg=msg)
        self.events.append(e)
        self.events = self.events[-300:]
        self.log_fn(time.strftime("%H:%M:%S") + " " + msg)

    def warmup(self):
        """Rozgrzewa wskaźniki ostatnimi świecami z rynku, żeby nie czekać godzinami."""
        need = max(self.strategy.warmup * 2, 300)
        try:
            bars = fetch_history(need / 1440 + 0.01, verbose=False)
        except Exception as e:
            self.log(f"Rozgrzewka nieudana ({e}) – strategia rozgrzeje się na żywych danych", "warn")
            return
        for b in bars[:-1]:
            self.strategy.on_bar(b)
        self.strategy.in_pos = False
        if self.broker.in_position:
            self.strategy.on_entry(self.broker.entry_price or bars[-1].close)
        self.log(f"Rozgrzewka: {len(bars) - 1} świec, strategia {self.strategy.describe()}")

    def _record_tick(self, ts: float, price: float):
        new = not self.ticks_path.exists()
        with open(self.ticks_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "price", "source"])
            w.writerow([int(ts), f"{price:.6f}", self.last_source])

    def _record_bar(self, b: Bar):
        new = not self.bars_path.exists()
        with open(self.bars_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "open", "high", "low", "close", "volume"])
            w.writerow([b.ts, f"{b.open:.6f}", f"{b.high:.6f}", f"{b.low:.6f}", f"{b.close:.6f}", "0"])

    # ------------------------------------------------------------------------
    def _buy(self, price: float, ts: int, reason: str):
        if not self.trade_enabled:
            self.log(f"Sygnał KUP przy ${price:.3f} pominięty – brak zyskownej konfiguracji (handel wstrzymany)", "warn")
            return
        t = self.broker.buy(price, self.broker.equity(price) * self.strategy.p["position_pct"], ts, reason)
        if t:
            self.strategy.on_entry(price)
            self.log(f"KUP {t.sol:.4f} SOL @ ${t.exec_price:.3f} za ${t.usd:.2f} (opłaty ${t.total_fees_usd:.3f}) [{reason}]",
                     "trade")

    def _sell(self, price: float, ts: int, reason: str):
        t = self.broker.sell(price, None, ts, reason)
        if t:
            self.strategy.on_exit()
            self.log(f"SPRZEDAJ {t.sol:.4f} SOL @ ${t.exec_price:.3f} → ${t.usd:.2f}, wynik {t.pnl_usd:+.2f} USD "
                     f"(opłaty ${t.total_fees_usd:.3f}) [{reason}]", "trade")
            if self.pending_cfg:
                self._apply_cfg(self.pending_cfg)

    def _apply_cfg(self, cfg: dict):
        self.cfg = cfg
        self.pending_cfg = None
        self.trade_enabled = bool(cfg.get("trade_enabled", True))
        self.strategy = make_strategy(cfg["strategy"], cfg["params"])
        self.log(f"Nowa konfiguracja: {self.strategy.describe()} | handel {'WŁ' if self.trade_enabled else 'WSTRZYMANY'}",
                 "learn")
        self.warmup()

    def step(self):
        price, src = fetch_price()
        now = time.time()
        with self.lock:
            self.last_price, self.last_source = price, src
            self._record_tick(now, price)
            # stop-loss / take-profit na każdym ticku
            if self.broker.in_position and self.strategy.in_pos:
                tp, sl = self.strategy.levels()
                if sl is not None and price <= sl:
                    self._sell(price, int(now), "stop_loss")
                elif tp is not None and price >= tp:
                    self._sell(price, int(now), "take_profit")
            closed = self.agg.update(now, price)
            if closed:
                self._record_bar(closed)
                sig = self.strategy.on_bar(closed)
                if sig == "BUY" and not self.broker.in_position:
                    self._buy(price, int(now), "signal")
                elif sig == "SELL" and self.broker.in_position:
                    self._sell(price, int(now), "signal")
                eq = self.broker.equity(price)
                self.equity_curve.append([int(now), round(eq, 4)])
                self.price_curve.append([int(now), round(price, 4)])
                self.equity_curve = self.equity_curve[-20000:]
                self.price_curve = self.price_curve[-20000:]
            self.save()

    def relearn(self):
        self.log("Uczenie: pobieram 7 dni historii i szukam najlepszej strategii po opłatach…", "learn")
        try:
            bars = fetch_history(7, verbose=False)
            if self.bars_path.exists():
                bars = merge_bars(bars, load_bars(self.bars_path)[-10080:])
            save_bars(bars, self.dir / "sol_1m_7d.csv")
            rep = optimize(bars, self.fees, samples=self.learn_samples, verbose=False)
            atomic_write_json(self.dir / "learn_report.json", rep)
            if not rep["best"]:
                self.log("Uczenie: brak kandydatów", "warn")
                return
            cfg = save_config(rep, self.config_path)
            b = rep["best"]
            self.log(f"Uczenie: najlepsza {b['strategy']} – trening {b['train']['return_pct']:+.2f}%, "
                     f"test {b['test']['return_pct']:+.2f}% (po opłatach); zyskowna: {rep['profitable']}", "learn")
            if self.broker.in_position:
                self.pending_cfg = cfg
                self.log("Nowa konfiguracja zostanie użyta po zamknięciu bieżącej pozycji", "learn")
            else:
                with self.lock:
                    self._apply_cfg(cfg)
        except Exception as e:
            self.log(f"Uczenie nieudane: {e}", "warn")
        finally:
            self.last_learn = time.time()

    def snapshot(self) -> dict:
        b = self.broker
        price = self.last_price or 0.0
        sells = [t for t in b.trades if t.side == "SELL"]
        fees = dict(pct=sum(t.fee_pct_usd for t in b.trades), slippage=sum(t.slippage_usd for t in b.trades),
                    network=sum(t.network_usd for t in b.trades))
        tp, sl = self.strategy.levels() if self.strategy.in_pos else (None, None)
        return dict(
            updated=int(time.time()), started=self.started, price=self.last_price, price_source=self.last_source,
            cash=b.cash, sol=b.sol, start_cash=b.start_cash, equity=b.equity(price) if price else b.cash,
            fees_paid_usd=b.fees_paid_usd, fees_breakdown=fees, trades=[t.to_dict() for t in b.trades][-500:],
            round_trips=len(sells), wins=sum(1 for t in sells if t.pnl_usd > 0),
            realized_pnl=sum(t.pnl_usd for t in sells), in_position=b.in_position, entry_price=b.entry_price,
            take_profit_price=tp, stop_loss_price=sl,
            strategy=self.strategy.name, params=self.strategy.p, trade_enabled=self.trade_enabled,
            fee_model=self.fees.to_dict(), round_trip_cost_pct=self.fees.round_trip_pct() * 100,
            equity_curve=self.equity_curve[-3000:], price_curve=self.price_curve[-3000:],
            events=self.events[-100:], last_learn=self.last_learn, interval=self.interval,
        )

    def save(self):
        st = dict(broker=self.broker.to_dict(), events=self.events, equity_curve=self.equity_curve,
                  price_curve=self.price_curve, started=self.started, last_learn=self.last_learn)
        atomic_write_json(self.state_path, st)
        atomic_write_json(self.dir / "paper_snapshot.json", self.snapshot())

    def run(self, max_steps: int | None = None):
        if self.relearn_hours and (time.time() - self.last_learn) > self.relearn_hours * 3600 \
                and not self.config_path.exists():
            self.relearn()
        self.warmup()
        steps = 0
        while not self.stop_event.is_set():
            t0 = time.time()
            try:
                self.step()
            except Exception as e:
                self.log(f"Błąd kroku: {e}", "warn")
                if os.environ.get("SOLBOT_DEBUG"):
                    traceback.print_exc()
            steps += 1
            if max_steps and steps >= max_steps:
                break
            if self.relearn_hours and time.time() - self.last_learn > self.relearn_hours * 3600:
                threading.Thread(target=self.relearn, daemon=True).start()
                self.last_learn = time.time()
            self.stop_event.wait(max(0.0, self.interval - (time.time() - t0)))
        self.save()
