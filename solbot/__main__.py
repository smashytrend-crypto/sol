"""SolBot – CLI.

  python -m solbot fees                    # profile opłat i koszt pełnego cyklu
  python -m solbot fetch --days 7          # pobierz minutowe świece SOL z ostatnich 7 dni
  python -m solbot learn                   # „nauka”: optymalizacja strategii na 7 dniach + zapis konfiguracji
  python -m solbot backtest                # backtest bieżącej konfiguracji (lub --strategy ...)
  python -m solbot run                     # bot demo na żywo + panel WWW (http://127.0.0.1:8050)
  python -m solbot paper                   # sam bot demo (bez panelu)
  python -m solbot dashboard               # sam panel
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .backtest import format_result, run_backtest
from .data import fetch_history, load_bars, save_bars
from .fees import FEE_PROFILES, get_fee_model
from .optimize import optimize, save_config
from .paper import PaperBot, atomic_write_json, load_config
from .strategies import STRATEGIES


def _bars(args):
    path = Path(args.data) / "sol_1m_7d.csv"
    if args.refresh or not path.exists():
        print(f"Pobieram {args.days} dni świec 1m…")
        bars = fetch_history(args.days)
        save_bars(bars, path)
    else:
        bars = load_bars(path)
    print(f"Dane: {len(bars)} świec, {time.strftime('%Y-%m-%d %H:%M', time.gmtime(bars[0].ts))} → "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(bars[-1].ts))} UTC, "
          f"cena {bars[0].open:.2f} → {bars[-1].close:.2f}")
    return bars


def cmd_fees(args):
    print(f"{'profil':12s} {'prowizja':>9s} {'poślizg':>8s} {'sieć/tx':>12s} {'cykl kup+sprz.':>15s}  uwagi")
    for name, f in FEE_PROFILES.items():
        print(f"{name:12s} {f.pct_fee*100:8.3f}% {f.slippage_pct*100:7.3f}% {f.network_fee_sol:9.6f} SOL "
              f"{f.round_trip_pct(args.notional, args.price)*100:13.3f}%  {f.notes}")
    print(f"\n(cykl liczony dla pozycji ${args.notional:.0f} przy cenie SOL ${args.price:.0f})")


def cmd_fetch(args):
    args.refresh = True
    _bars(args)


def cmd_learn(args):
    bars = _bars(args)
    fees = get_fee_model(args.fees)
    print(f"Model opłat '{fees.name}': koszt pełnego cyklu ≈ {fees.round_trip_pct()*100:.3f}%")
    print(f"Szukam parametrów ({args.samples} prób/strategię, wykonanie: {args.fill})…")
    strategies = args.strategies.split(",") if args.strategies else None
    rep = optimize(bars, fees, strategies, samples=args.samples, fill=args.fill, start_cash=args.cash)
    atomic_write_json(Path(args.data) / "learn_report.json", rep)
    if not rep["best"]:
        print("Brak wyników.")
        return
    cfg = save_config(rep, Path(args.data) / "best_config.json")
    b = rep["best"]
    print("\nTOP 5 (trening → test, po opłatach):")
    for x in rep["leaderboard"][:5]:
        print(f"  {x['strategy']:15s} {x['train']['return_pct']:+6.2f}% → {x['test']['return_pct']:+6.2f}%  "
              f"transakcje {x['train']['trades']}+{x['test']['trades']}")
    print(f"\nNajlepsza: {b['strategy']} {json.dumps(b['params'])}")
    print(f"Cały okres: {format_result(run_backtest(bars, b['strategy'], fees, b['params'], args.cash, fill=args.fill))}")
    print(f"Kup i trzymaj w tym okresie: {rep['buy_hold_pct']:+.2f}%")
    if rep["profitable"]:
        print("✔ Konfiguracja zarabia po opłatach na danych treningowych i testowych → zapisano data/best_config.json")
    else:
        print("✘ Żadna konfiguracja nie zarobiła po opłatach na obu częściach danych. Bot demo będzie tylko "
              "obserwował (trade_enabled=false). To uczciwy wynik – lepiej nie handlować niż tracić na opłatach.")


def cmd_backtest(args):
    bars = _bars(args)
    if args.strategy:
        cfg = dict(strategy=args.strategy, params=json.loads(args.params or "{}"), fees=args.fees)
    else:
        cfg = load_config(Path(args.data) / "best_config.json")
        if args.fees_override:
            cfg["fees"] = args.fees
    fees = get_fee_model(cfg["fees"])
    r = run_backtest(bars, cfg["strategy"], fees, cfg["params"], args.cash, keep_log=True, fill=args.fill)
    print(format_result(r))
    print(f"Opłaty łącznie ${r.fees_usd:.2f} przy {r.trades} cyklach kup/sprzedaj (model '{fees.name}')")
    rep = dict(summary=r.summary(), trades=r.trade_log, equity_curve=r.equity_curve, fee_model=fees.to_dict())
    atomic_write_json(Path(args.data) / "backtest_report.json", rep)
    if args.compare_fees:
        print("\nTa sama strategia przy różnych modelach opłat:")
        for name in FEE_PROFILES:
            rr = run_backtest(bars, cfg["strategy"], get_fee_model(name), cfg["params"], args.cash, fill=args.fill)
            print(f"  {name:12s} {rr.return_pct:+7.2f}%  opłaty ${rr.fees_usd:7.2f}")


def _bot(args) -> PaperBot:
    return PaperBot(args.data, start_cash=args.cash, interval=args.interval, relearn_hours=args.relearn_hours,
                    reset=args.reset)


def cmd_paper(args):
    bot = _bot(args)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.save()
        print("\nZatrzymano, stan zapisany.")


def cmd_run(args):
    from .dashboard import serve
    bot = _bot(args)
    serve(args.data, args.host, args.port, bot=bot, block=False)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.save()
        print("\nZatrzymano, stan zapisany.")


def cmd_dashboard(args):
    from .dashboard import serve
    serve(args.data, args.host, args.port)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="solbot", description="Demo bot do handlu SOL (fikcyjne pieniądze)")
    ap.add_argument("--data", default="data", help="katalog danych (domyślnie ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def data_opts(p):
        p.add_argument("--days", type=float, default=7)
        p.add_argument("--refresh", action="store_true", help="pobierz świeże dane z sieci")
        p.add_argument("--cash", type=float, default=1000.0)
        p.add_argument("--fees", default="jupiter", help=f"profil opłat: {', '.join(FEE_PROFILES)}")
        p.add_argument("--fill", default="close", choices=["close", "intrabar"],
                       help="close = konserwatywnie (domyślnie), intrabar = optymistycznie")

    p = sub.add_parser("fees", help="pokaż modele opłat")
    p.add_argument("--notional", type=float, default=1000)
    p.add_argument("--price", type=float, default=100)
    p.set_defaults(fn=cmd_fees)

    p = sub.add_parser("fetch", help="pobierz historię cen")
    data_opts(p)
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("learn", help="optymalizuj strategię na historii")
    data_opts(p)
    p.add_argument("--samples", type=int, default=200)
    p.add_argument("--strategies", help=f"lista po przecinku: {','.join(STRATEGIES)}")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("backtest", help="backtest konfiguracji")
    data_opts(p)
    p.add_argument("--strategy", choices=list(STRATEGIES))
    p.add_argument("--params", help='JSON, np. \'{"lookback":15,"drop_pct":0.01}\'')
    p.add_argument("--fees-override", action="store_true", help="użyj --fees zamiast opłat z konfiguracji")
    p.add_argument("--compare-fees", action="store_true", help="porównaj wynik przy wszystkich profilach opłat")
    p.set_defaults(fn=cmd_backtest)

    for name, fn in (("paper", cmd_paper), ("run", cmd_run)):
        p = sub.add_parser(name, help="bot demo na żywo" + (" + panel" if name == "run" else ""))
        p.add_argument("--cash", type=float, default=1000.0, help="kapitał startowy (fikcyjne USD)")
        p.add_argument("--interval", type=float, default=10.0, help="co ile sekund sprawdzać cenę")
        p.add_argument("--relearn-hours", type=float, default=24.0, help="co ile godzin uczyć się od nowa (0 = nigdy)")
        p.add_argument("--reset", action="store_true", help="zacznij od nowa (kasuje stan konta demo)")
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8050)
        p.set_defaults(fn=fn)

    p = sub.add_parser("dashboard", help="sam panel WWW")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p.set_defaults(fn=cmd_dashboard)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
