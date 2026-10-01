import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from hero import backtest, dashboard, evolve
from hero.alpaca import Alpaca, AlpacaError
from hero.engine import Engine, round_option_price
from hero.indicators import max_drawdown, momentum, rsi, sma
from hero.journal import Journal
from hero.strategies import momentum as mom
from hero.strategies import options as opt

CFG = json.loads((Path(__file__).resolve().parent.parent / "config" / "strategy.json").read_text())
TODAY = date(2026, 10, 1)


def series(drift: float, n: int = 400, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    xs = [100.0]
    for _ in range(n - 1):
        xs.append(xs[-1] * math.exp(drift + rng.gauss(0, 0.01)))
    return xs


def bars(xs: list[float]) -> list[dict]:
    d0 = TODAY - timedelta(days=len(xs))
    return [{"t": (d0 + timedelta(days=i)).isoformat() + "T04:00:00Z", "c": x} for i, x in enumerate(xs)]


class FakeClient:
    def __init__(self, closes, positions=(), open_orders=(), equity=100_000, last_equity=100_000):
        self.closes, self.pos, self.oo = closes, list(positions), list(open_orders)
        self.equity, self.last = equity, last_equity
        self.orders, self.closed = [], []

    def clock(self): return {"is_open": True}
    def account(self): return {"equity": str(self.equity), "last_equity": str(self.last), "cash": "0"}
    def positions(self): return self.pos
    def open_orders(self): return self.oo
    def daily_bars(self, symbols, start): return {s: bars(self.closes[s]) for s in symbols if s in self.closes}
    def submit_order(self, **o): self.orders.append(o)
    def close_position(self, s): self.closed.append(s)

    def option_contracts(self, und, **kw):
        spot = self.closes[und][-1]
        exp = (TODAY + timedelta(days=45)).isoformat()
        t = "C" if kw["type"] == "call" else "P"
        return [{"symbol": f"{und}{exp[2:4]}{exp[5:7]}{exp[8:10]}{t}{int(k * 1000):08d}",
                 "strike_price": str(k), "expiration_date": exp, "tradable": True}
                for k in (round(spot * 0.95), round(spot), round(spot * 1.05))]

    def option_snapshots(self, symbols):
        return {s: {"latestQuote": {"bp": 4.0, "ap": 4.2},
                    "greeks": {"delta": 0.3 + 0.2 * i}} for i, s in enumerate(symbols)}


def cfg(**over):
    c = json.loads(json.dumps(CFG))
    c["universe"] = ["SPY", "UP1", "UP2", "DOWN"]
    for k, v in over.items():
        c[k].update(v)
    return c


class Indicators(unittest.TestCase):
    def test_basic(self):
        xs = [1, 2, 3, 4, 5]
        self.assertEqual(sma(xs, 5), 3)
        self.assertIsNone(sma(xs, 6))
        self.assertAlmostEqual(momentum(xs, 4), 4)
        self.assertEqual(rsi([1, 2, 3, 4, 5, 6], 5), 100)
        self.assertAlmostEqual(max_drawdown([1, 2, 1, 3]), -0.5)


class Strategy(unittest.TestCase):
    def test_rank_prefers_uptrends(self):
        closes = {"UP1": series(0.002, seed=1), "UP2": series(0.001, seed=2), "DOWN": series(-0.002, seed=3)}
        ranked = mom.rank(closes, {**CFG["stocks"], "rsi_max": 101})
        self.assertNotIn("DOWN", ranked)
        self.assertEqual(ranked[0], "UP1")

    def test_bear_regime_scales_exposure(self):
        closes = {"SPY": series(-0.002, seed=4), "UP1": series(0.002, seed=1)}
        p = {**CFG["stocks"], "rsi_max": 101}
        w = mom.target_weights(closes, p, "SPY", 1.0)
        self.assertAlmostEqual(w["UP1"], p["gross_exposure"] * mom.BEAR_EXPOSURE_SCALE)

    def test_option_helpers(self):
        self.assertEqual(opt.occ_expiration("AAPL261120C00150000"), "2026-11-20")
        self.assertIsNone(opt.mid({"latestQuote": {"bp": 1.0, "ap": 2.0}}))  # too wide
        self.assertEqual(round_option_price(4.13), 4.15)
        self.assertEqual(round_option_price(1.234), 1.23)
        p = CFG["options"]
        self.assertTrue(opt.should_exit({"unrealized_plpc": "0.7"}, TODAY, "2026-12-01", p).startswith("take"))
        self.assertTrue(opt.should_exit({"unrealized_plpc": "-0.5"}, TODAY, "2026-12-01", p).startswith("stop"))
        self.assertTrue(opt.should_exit({"unrealized_plpc": "0"}, TODAY, "2026-10-05", p).startswith("dte"))
        self.assertIsNone(opt.should_exit({"unrealized_plpc": "0.1"}, TODAY, "2026-12-01", p))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.tmp.name))
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1),
                       "UP2": series(0.0015, seed=2), "DOWN": series(-0.002, seed=3)}

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_cycle(self):
        c = FakeClient(self.closes, positions=[
            {"symbol": "DOWN", "asset_class": "us_equity", "market_value": "5000"},
            {"symbol": "XYZ261001C00010000", "asset_class": "us_option", "unrealized_plpc": "0.9"}])
        res = Engine(c, cfg(stocks={"rsi_max": 101}), self.j).run(today=TODAY)
        self.assertEqual(res["status"], "ok")
        self.assertIn("DOWN", c.closed)
        self.assertIn("XYZ261001C00010000", c.closed)
        stock_buys = {o["symbol"] for o in c.orders if o["type"] == "market"}
        expected = set(mom.rank(self.closes, {**CFG["stocks"], "rsi_max": 101})[: CFG["stocks"]["top_n"]])
        self.assertTrue(expected and expected == stock_buys)
        option_buys = [o for o in c.orders if o["type"] == "limit"]
        # One slot is taken by the existing XYZ option (closed this run, but still counted).
        self.assertTrue(1 <= len(option_buys) <= CFG["options"]["max_positions"] - 1)
        # Delta closest to 0.5 is the middle (ATM) contract.
        self.assertIn("C", option_buys[0]["symbol"][-9])
        self.assertEqual(self.j.state()["last_rebalance"], TODAY.isoformat())

        # Second run the same day must not rebalance again.
        c2 = FakeClient(self.closes)
        Engine(c2, cfg(stocks={"rsi_max": 101}, options={"enabled": False}), self.j).run(today=TODAY)
        self.assertEqual(c2.orders, [])

    def test_daily_loss_halt_blocks_buys(self):
        c = FakeClient(self.closes, equity=95_000, last_equity=100_000)
        Engine(c, cfg(stocks={"rsi_max": 101}), self.j).run(today=TODAY)
        self.assertFalse([o for o in c.orders if o["side"] == "buy"])

    def test_dry_run_sends_nothing(self):
        c = FakeClient(self.closes)
        Engine(c, cfg(stocks={"rsi_max": 101}), self.j, dry_run=True).run(today=TODAY)
        self.assertEqual(c.orders, [])
        self.assertIn('"dry_run": true', (Path(self.tmp.name) / "trades.jsonl").read_text())
        self.assertEqual(self.j.state(), {})

    def test_bear_regime_buys_index_put(self):
        self.closes["SPY"] = series(-0.002, seed=6)
        c = FakeClient(self.closes)
        Engine(c, cfg(stocks={"rsi_max": 101}), self.j).run(today=TODAY)
        puts = [o for o in c.orders if o["type"] == "limit"]
        self.assertEqual(len(puts), 1)
        self.assertTrue(puts[0]["symbol"].startswith("SPY") and puts[0]["symbol"][-9] == "P")


class Evolution(unittest.TestCase):
    def test_backtest_and_evolve(self):
        raw = {"SPY": bars(series(0.0005, 700, 7)), "UP1": bars(series(0.001, 700, 8)),
               "UP2": bars(series(0.0008, 700, 9)), "DOWN": bars(series(-0.001, 700, 10))}
        dates, closes = backtest.align(raw, "SPY")
        self.assertEqual(len(dates), 700)
        c = cfg()
        new, report = evolve.evolve(c, closes, [100, 110], TODAY)
        self.assertIn("decision:", report)
        self.assertIn("+10.00%", report)
        self.assertIn(new["generation"], (0, 1))


class Dashboard(unittest.TestCase):
    def test_render_skips_dry_runs_and_escapes(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.event("order", symbol="AAPL", side="buy", qty="1", dry_run=True)
            j.event("order", symbol="NVDA", side="buy", qty="2", dry_run=False, reason="</script>")
            j.equity("2026-10-01", 100_000, 50_000, 0, 570.0)
            j.snapshot({"account": {"equity": "100000"}, "positions": [], "open_orders": []})
            data = dashboard.collect(Path(d), cfg())
            self.assertEqual([e["symbol"] for e in data["events"]], ["NVDA"])
            self.assertEqual(data["equity"][0]["benchmark"], "570.00")
            html = dashboard.render(data)
            self.assertNotIn("/*__DATA__*/null", html)
            self.assertNotIn("</script>\"", html)


class Safety(unittest.TestCase):
    def test_refuses_live_endpoint(self):
        with self.assertRaises(AlpacaError):
            Alpaca(base_url="https://api.alpaca.markets")


if __name__ == "__main__":
    unittest.main()
