"""CLI: python -m hero {run,status,evolve,backtest}"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from hero import backtest, dashboard, earnings, evolve, macro, market, patrol, review, universe
from hero.alpaca import Alpaca
from hero.engine import Engine
from hero.journal import Journal

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "strategy.json"
JOURNAL = ROOT / "journal"
EARNINGS = ROOT / "data" / "earnings.json"
MARKET = ROOT / "data" / "market.json"
UNIVERSE = ROOT / "data" / "universe.json"


def apply_dynamic_universe(cfg: dict) -> dict:
    """Swap in this month's dynamic universe; keep the static list if the file is missing."""
    dyn = cfg.get("dynamic_universe")
    data = universe.load(UNIVERSE) if dyn else None
    if data and data["size"] == dyn["size"]:
        cfg = {**cfg, "universe": universe.symbols(data), "universe_month": data["month"]}
    return cfg
EVOLVE_HISTORY_DAYS = 3 * 365


def et_today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(prog="hero")
    ap.add_argument("--config", default=str(CFG_PATH), help="strategy config (one per experiment)")
    ap.add_argument("--journal", default=str(JOURNAL), help="journal directory (one per experiment)")
    ap.add_argument("--exchange", default=str(ROOT / "exchange" / "claude"), help="where daily exchange files go")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one trading cycle")
    r.add_argument("--dry-run", action="store_true", help="log orders without sending them")
    r.add_argument("--force", action="store_true", help="run even if the market is closed")
    sub.add_parser("status", help="account and positions")
    sub.add_parser("evolve", help="search parameters and maybe adopt a new generation")
    sub.add_parser("backtest", help="backtest the current config")
    d = sub.add_parser("dashboard", help="render the monitoring page from the journal")
    d.add_argument("--out", default=str(ROOT / "site" / "index.html"))
    rv = sub.add_parser("review", help="print the post-market fact sheet for a day")
    rv.add_argument("--date", default=et_today())
    rv.add_argument("--write", action="store_true", help="save to journal/reviews/DATE.md instead of printing facts")
    sub.add_parser("patrol", help="health checks; prints problems, one per line")
    sub.add_parser("snapshot", help="refresh account/positions/orders snapshot and today's fills from the broker")
    e = sub.add_parser("earnings", help="refresh the earnings calendar (at most one API call per ET day)")
    e.add_argument("--configs", nargs="+", default=[str(CFG_PATH), str(ROOT / "config" / "s500.json")])
    u = sub.add_parser("universe", help="rebuild the monthly dynamic universe (once per ET month)")
    u.add_argument("--size", type=int, default=100)
    m = sub.add_parser("market", help="refresh yields and oil (Alpha Vantage) and news sentiment (Alpaca), once per ET day")
    m.add_argument("--configs", nargs="+", default=[str(CFG_PATH), str(ROOT / "config" / "s500.json")])
    args = ap.parse_args()

    cfg_path, jdir = Path(args.config), Path(args.journal)
    cfg = apply_dynamic_universe(json.loads(cfg_path.read_text()))
    journal = Journal(jdir)
    if args.cmd == "dashboard":
        dashboard.write(jdir, cfg, Path(args.out))
        return
    if args.cmd == "review":
        if not args.write:
            print(review.facts(jdir, cfg, args.date) or f"NO_TRADING_DAY {args.date}")
            return
        text = review.report(jdir, cfg, args.date)
        if text is None:
            print(f"NO_TRADING_DAY {args.date}")
            return
        out = jdir / "reviews" / f"{args.date}.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(text)
        exchange = Path(args.exchange) / f"{args.date}.json"
        exchange.parent.mkdir(parents=True, exist_ok=True)
        exchange.write_text(json.dumps(review.export(jdir, cfg, args.date), indent=2, ensure_ascii=False) + "\n")
        print(out)
        print("FINAL" if review.is_final(jdir, args.date) else "PRELIMINARY")
        return
    if args.cmd in ("earnings", "market"):
        import os
        symbols = set().union(*(json.loads(Path(c).read_text())["universe"] for c in args.configs),
                              universe.symbols(universe.load(UNIVERSE)))
        now = datetime.now(ZoneInfo("America/New_York"))
        if args.cmd == "earnings":
            print(earnings.refresh(EARNINGS, symbols, os.environ["ALPHAVANTAGE_API_KEY"], now))
        else:
            news_client = Alpaca() if os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID") else None
            print(market.refresh(MARKET, symbols, os.environ["ALPHAVANTAGE_API_KEY"], now, news_client))
        return
    client = Alpaca()
    if args.cmd == "universe":
        print(universe.refresh(UNIVERSE, client, args.size, datetime.now(ZoneInfo("America/New_York"))))
        return

    if args.cmd == "patrol":
        for problem in patrol.check(patrol.load(jdir), bool(client.clock().get("is_open"))):
            print(problem)
        return

    if args.cmd in ("run", "snapshot"):
        if args.cmd == "run":
            cal = earnings.load(EARNINGS)
            print(json.dumps(Engine(client, cfg, journal, args.dry_run, earnings=cal).run(force=args.force)))
        journal.snapshot({"account": client.account(), "positions": client.positions(),
                          "open_orders": client.open_orders(),
                          "benchmark": review.benchmark_quote(client, cfg["regime_symbol"])})
        print(f"fills: {journal.record_fills(client.fill_activities(et_today()))} new")
    elif args.cmd == "status":
        a = client.account()
        print(f"equity {a['equity']}  cash {a['cash']}  buying power {a['buying_power']}")
        for p in client.positions():
            print(f"{p['symbol']:<24}{p['qty']:>8}  mv {p['market_value']:>12}  pl {float(p['unrealized_plpc']):+.1%}")
    else:
        start = (date.today() - timedelta(days=EVOLVE_HISTORY_DAYS)).isoformat()
        macro_syms = [s for s in macro.SYMBOLS.values() if s not in cfg["universe"]]
        _, aligned = backtest.align(client.daily_bars(cfg["universe"] + macro_syms, start), cfg["regime_symbol"])
        closes = {s: xs for s, xs in aligned.items() if s in cfg["universe"]}
        macro_closes = {s: aligned[s] for s in macro.SYMBOLS.values() if s in aligned} or None
        if args.cmd == "backtest":
            n = min(len(xs) for xs in closes.values())
            print(json.dumps(evolve.evaluate(closes, cfg["stocks"], cfg, n, macro_closes), indent=2))
        else:
            new, report = evolve.evolve(cfg, closes, journal.equity_curve(), date.today(), macro_closes)
            report += evolve.briefing_accuracy(ROOT / "macro" / "briefings" / "scores.csv")
            evolve.save(new, report, cfg_path, jdir / "evolution.md")
            print(report)


if __name__ == "__main__":
    main()
