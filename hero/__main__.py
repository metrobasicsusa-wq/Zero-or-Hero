"""CLI: python -m hero {run,status,evolve,backtest}"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from hero import backtest, dashboard, evolve, patrol, review
from hero.alpaca import Alpaca
from hero.engine import Engine
from hero.journal import Journal

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "strategy.json"
JOURNAL = ROOT / "journal"
EVOLVE_HISTORY_DAYS = 3 * 365


def et_today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(prog="hero")
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
    args = ap.parse_args()

    cfg = json.loads(CFG_PATH.read_text())
    journal = Journal(JOURNAL)
    if args.cmd == "dashboard":
        dashboard.write(JOURNAL, cfg, Path(args.out))
        return
    if args.cmd == "review":
        if not args.write:
            print(review.facts(JOURNAL, cfg, args.date) or f"NO_TRADING_DAY {args.date}")
            return
        text = review.report(JOURNAL, cfg, args.date)
        if text is None:
            print(f"NO_TRADING_DAY {args.date}")
            return
        out = JOURNAL / "reviews" / f"{args.date}.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(text)
        exchange = ROOT / "exchange" / "claude" / f"{args.date}.json"
        exchange.parent.mkdir(parents=True, exist_ok=True)
        exchange.write_text(json.dumps(review.export(JOURNAL, cfg, args.date), indent=2, ensure_ascii=False) + "\n")
        print(out)
        print("FINAL" if review.is_final(JOURNAL, args.date) else "PRELIMINARY")
        return
    client = Alpaca()

    if args.cmd == "patrol":
        for problem in patrol.check(patrol.load(JOURNAL), bool(client.clock().get("is_open"))):
            print(problem)
        return

    if args.cmd in ("run", "snapshot"):
        if args.cmd == "run":
            print(json.dumps(Engine(client, cfg, journal, args.dry_run).run(force=args.force)))
        journal.snapshot({"account": client.account(), "positions": client.positions(),
                          "open_orders": client.open_orders()})
        print(f"fills: {journal.record_fills(client.fill_activities(et_today()))} new")
    elif args.cmd == "status":
        a = client.account()
        print(f"equity {a['equity']}  cash {a['cash']}  buying power {a['buying_power']}")
        for p in client.positions():
            print(f"{p['symbol']:<24}{p['qty']:>8}  mv {p['market_value']:>12}  pl {float(p['unrealized_plpc']):+.1%}")
    else:
        start = (date.today() - timedelta(days=EVOLVE_HISTORY_DAYS)).isoformat()
        _, closes = backtest.align(client.daily_bars(cfg["universe"], start), cfg["regime_symbol"])
        if args.cmd == "backtest":
            n = min(len(xs) for xs in closes.values())
            print(json.dumps(evolve.evaluate(closes, cfg["stocks"], cfg, n), indent=2))
        else:
            new, report = evolve.evolve(cfg, closes, journal.equity_curve(), date.today())
            evolve.save(new, report, CFG_PATH, JOURNAL / "evolution.md")
            print(report)


if __name__ == "__main__":
    main()
