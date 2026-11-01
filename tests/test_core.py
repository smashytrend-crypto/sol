import math
import unittest

from solbot.backtest import run_backtest
from solbot.broker import PaperBroker
from solbot.data import Bar, BarAggregator, fill_gaps
from solbot.fees import FeeModel, get_fee_model
from solbot.indicators import RSI, RollingStats
from solbot.strategies import STRATEGIES, make_strategy


def bars_from(prices, start=1_700_000_000):
    return [Bar(start + 60 * i, p, p, p, p) for i, p in enumerate(prices)]


class FeeTests(unittest.TestCase):
    def test_round_trip_cost_matches_broker(self):
        f = get_fee_model("jupiter")
        b = PaperBroker(f, 1000.0)
        b._one_time_paid = True  # porównujemy bez jednorazowego rentu
        b.buy(100.0, 1000.0, 0)
        b.sell(100.0, None, 1)
        loss_pct = (1000.0 - b.cash) / 1000.0
        self.assertAlmostEqual(loss_pct, f.round_trip_pct(1000, 100), delta=1e-4)

    def test_zero_fees_no_loss(self):
        b = PaperBroker(get_fee_model("zero"), 1000.0)
        b.buy(50.0, 1000.0, 0)
        self.assertAlmostEqual(b.sol, 20.0)
        b.sell(55.0, None, 1)
        self.assertAlmostEqual(b.cash, 1100.0)
        self.assertEqual(b.fees_paid_usd, 0)

    def test_fee_components(self):
        f = FeeModel(trading_fee_pct=0.01, slippage_pct=0.0, base_fee_lamports=0, priority_fee_lamports=0, signatures=0)
        b = PaperBroker(f, 100.0)
        t = b.buy(10.0, 100.0, 0)
        self.assertAlmostEqual(t.fee_pct_usd, 1.0)
        self.assertAlmostEqual(t.sol, 9.9)

    def test_network_fee_in_usd(self):
        f = FeeModel(trading_fee_pct=0, slippage_pct=0, base_fee_lamports=5000, priority_fee_lamports=95000)
        self.assertAlmostEqual(f.network_fee_sol, 0.0001)
        self.assertAlmostEqual(f.network_fee_usd(200), 0.02)

    def test_pnl_includes_both_sides_fees(self):
        b = PaperBroker(get_fee_model("binance"), 1000.0)
        b.buy(100.0, 1000.0, 0)
        t = b.sell(100.0, None, 1)
        self.assertLess(t.pnl_usd, 0)
        self.assertAlmostEqual(t.pnl_usd, b.cash - 1000.0, places=6)

    def test_serialization_roundtrip(self):
        f = get_fee_model("jupiter")
        b = PaperBroker(f, 500.0)
        b.buy(100.0, 250.0, 0)
        b2 = PaperBroker.from_dict(b.to_dict(), f)
        self.assertAlmostEqual(b2.sol, b.sol)
        self.assertEqual(len(b2.trades), 1)


class IndicatorTests(unittest.TestCase):
    def test_rolling_stats(self):
        r = RollingStats(3)
        for x in [1, 2, 3, 4]:
            r.push(x)
        self.assertAlmostEqual(r.mean, 3.0)
        self.assertAlmostEqual(r.std, math.sqrt(2 / 3))

    def test_rsi_bounds(self):
        r = RSI(5)
        v = None
        for x in range(1, 20):
            v = r.push(x)
        self.assertEqual(v, 100.0)

    def test_aggregator_and_gaps(self):
        a = BarAggregator(60)
        self.assertIsNone(a.update(0, 10))
        self.assertIsNone(a.update(30, 12))
        closed = a.update(61, 11)
        self.assertEqual((closed.open, closed.high, closed.low, closed.close), (10, 12, 10, 12))
        filled = fill_gaps([Bar(0, 1, 1, 1, 1), Bar(180, 2, 2, 2, 2)], 60)
        self.assertEqual([b.ts for b in filled], [0, 60, 120, 180])


class BacktestTests(unittest.TestCase):
    def test_flat_market_no_trades_no_loss(self):
        r = run_backtest(bars_from([100.0] * 500), "mean_reversion", get_fee_model("jupiter"))
        self.assertEqual(r.trades, 0)
        self.assertAlmostEqual(r.end_equity, 1000.0)

    def test_dip_buy_profits_on_v_shape(self):
        prices = [100.0] * 30 + [97.0, 96.0, 96.5] + [96.5 + 0.2 * i for i in range(40)]
        p = dict(lookback=10, drop_pct=0.02, confirm=1, take_profit=0.02, stop_loss=0.05, cooldown=0, max_hold=0)
        r = run_backtest(bars_from(prices), "dip_buy", get_fee_model("jupiter"), p)
        self.assertEqual(r.trades, 1)
        self.assertGreater(r.return_pct, 1.0)
        z = run_backtest(bars_from(prices), "dip_buy", get_fee_model("zero"), p)
        self.assertGreater(z.return_pct, r.return_pct)  # opłaty zmniejszają wynik

    def test_no_lookahead(self):
        # sygnał na zamknięciu świecy i nie może być wykonany po cenie z tej samej świecy
        prices = [100.0] * 30 + [97.0, 97.5, 150.0]
        p = dict(lookback=10, drop_pct=0.02, confirm=1, take_profit=0.0, stop_loss=0.0)
        r = run_backtest(bars_from(prices), "dip_buy", get_fee_model("zero"), p, keep_log=True)
        buy = r.trade_log[0]
        self.assertEqual(buy["ref_price"], 150.0)  # kupno dopiero na następnej świecy

    def test_stop_loss(self):
        prices = [100.0] * 30 + [97.0, 97.5] + [97.5 - 0.5 * i for i in range(30)]
        p = dict(lookback=10, drop_pct=0.02, confirm=1, take_profit=0.1, stop_loss=0.02, cooldown=1000)
        r = run_backtest(bars_from(prices), "dip_buy", get_fee_model("zero"), p, keep_log=True)
        self.assertEqual(r.trades, 1)
        self.assertEqual(r.trade_log[-1]["reason"], "stop_loss")
        self.assertGreater(r.return_pct, -3.0)

    def test_all_strategies_run(self):
        import random
        rng = random.Random(1)
        prices, p = [], 100.0
        for _ in range(2000):
            p *= 1 + rng.gauss(0, 0.002)
            prices.append(p)
        for name in STRATEGIES:
            for fill in ("close", "intrabar"):
                r = run_backtest(bars_from(prices), name, get_fee_model("jupiter"), fill=fill)
                self.assertTrue(math.isfinite(r.end_equity))
                self.assertGreater(r.end_equity, 0)


if __name__ == "__main__":
    unittest.main()
