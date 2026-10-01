"""CLI: python -m hero {run,status,evolve,backtest}"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from hero import backtest, evolve
from hero.alpaca import Alpaca
from hero.engine import Engine
from hero.journal import Journal

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "strategy.json"
JOURNAL = ROOT / "journal"
EVOLVE_HISTORY_DAYS = 3 * 365


def main() -> None:
    ap = argparse.ArgumentParser(prog="hero")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one trading cycle")
    r.add_argument("--dry-run", action="store_true", help="log orders without sending them")
    r.add_argument("--force", action="store_true", help="run even if the market is closed")
    sub.add_parser("status", help="account and positions")
    sub.add_parser("evolve", help="search parameters and maybe adopt a new generation")
    sub.add_parser("backtest", help="backtest the current config")
    args = ap.parse_args()

    cfg = json.loads(CFG_PATH.read_text())
    client = Alpaca()
    journal = Journal(JOURNAL)

    if args.cmd == "run":
        print(json.dumps(Engine(client, cfg, journal, args.dry_run).run(force=args.force)))
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
