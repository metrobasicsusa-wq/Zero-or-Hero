import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from hero import backtest, dashboard, evolve, patrol, review
from hero.alpaca import Alpaca, AlpacaError
from hero.engine import Engine, round_option_price
from hero.indicators import max_drawdown, momentum, rsi, sma
from hero.journal import Journal
from hero.strategies import momentum as mom
from hero.strategies import options as opt

# A frozen copy of the generation-0 config: config/strategy.json changes every week as the
# strategy evolves, and the tests must not depend on whatever generation is live.
CFG = json.loads((Path(__file__).resolve().parent / "fixture_config.json").read_text())
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
    def submit_order(self, **o):
        self.orders.append(o)
        return {"id": f"order-{len(self.orders)}"}
    def close_position(self, s):
        self.closed.append(s)
        return {"id": f"close-{s}", "status": "accepted"}

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

    def test_rsi_filter_on_holdings(self):
        hot = [100.0 * 1.01 ** i for i in range(300)]  # steady climb: RSI 100
        closes = {"HOT": hot}
        p = {**CFG["stocks"], "rsi_max": 80}
        self.assertEqual(mom.rank(closes, {**p, "rsi_applies_to_holdings": True}, frozenset({"HOT"})), [])
        self.assertEqual(mom.rank(closes, {**p, "rsi_applies_to_holdings": False}, frozenset({"HOT"})), ["HOT"])
        # Not held: still blocked from entry either way.
        self.assertEqual(mom.rank(closes, {**p, "rsi_applies_to_holdings": False}), [])

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
        # Quote evidence is journaled with each order but never sent to the broker.
        self.assertFalse(any("evidence" in o or "order_key" in o for o in c.orders))
        from hero.journal import execution_key
        self.assertTrue(any(json.loads(l).get("order_key") == execution_key("order-1")
                            for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()))
        logged = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()]
        opt_ev = next(e["evidence"] for e in logged if e["kind"] == "order" and e["type"] == "limit")
        self.assertEqual((opt_ev["bid"], opt_ev["ask"], opt_ev["mid"]), (4.0, 4.2, 4.1))
        self.assertAlmostEqual(opt_ev["spread_pct_of_mid"], 0.0488, places=4)
        close_ev = next(e["evidence"] for e in logged if e["kind"] == "close" and e["symbol"].startswith("XYZ"))
        self.assertEqual(close_ev["unrealized_plpc"], "0.9")

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
            self.assertIsNone(data["review"])
            (Path(d) / "reviews").mkdir()
            (Path(d) / "reviews" / "2026-10-01.md").write_text(
                "# 盘后复盘 2026-10-01（Claude）\n\n## 要点（规则自动生成）\n- 今日 +1.00%\n\n# Facts\n- buy 1 X\n\n## AI 分析\nok\n")
            r = dashboard.collect(Path(d), cfg())["review"]
            self.assertEqual((r["date"], r["points"], r["has_ai"]), ("2026-10-01", ["今日 +1.00%"], True))


class Review(unittest.TestCase):
    def test_facts(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.equity("2026-10-01", 100_000, 50_000, 0, 500.0)
            j.equity("2026-10-02", 101_000, 50_000, 0, 505.0)
            (Path(d) / "trades.jsonl").write_text(json.dumps(
                {"ts": "2026-10-02T15:00:00+00:00", "kind": "order", "symbol": "NVDA261120C00235000", "side": "buy",
                 "qty": "2", "type": "limit", "limit_price": "11.40", "reason": "call on NVDA", "dry_run": False}) + "\n")
            from hero.journal import execution_key
            with open(Path(d) / "trades.jsonl") as f:
                lines = f.read().splitlines()
            ev = json.loads(lines[0]); ev["order_key"] = execution_key("o1")
            (Path(d) / "trades.jsonl").write_text(json.dumps(ev) + "\n")
            j.record_fills([{"id": "a1", "order_id": "o1", "transaction_time": "2026-10-02T15:01:00Z",
                             "symbol": "NVDA261120C00235000", "side": "buy", "qty": "2", "price": "11.35"}])
            text = review.facts(Path(d), cfg(), "2026-10-02")
            self.assertIn("→ filled 2 @ 11.35", text)
            self.assertIsNone(review.facts(Path(d), cfg(), "2026-10-03"))
            self.assertIn("| Day | +1.00% ($1,000) | +1.00% |", text)
            self.assertIn("buy 2 NVDA 11/20 call 235 @ 11.40 (call on NVDA)", text)


class Patrol(unittest.TestCase):
    def test_checks(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
        snap = {"ts": "2026-10-02T15:00:00+00:00",
                "account": {"equity": "97000", "last_equity": "100000"},
                "positions": [{"symbol": "NVDA261120C00235000", "unrealized_plpc": "-0.4"},
                              {"symbol": "AAPL", "unrealized_plpc": "0.02"}],
                "open_orders": [{"symbol": "MSFT", "side": "buy", "status": "new", "submitted_at": "2026-10-02T13:30:00Z"}]}
        problems = patrol.check(snap, market_open=True, now=now)
        self.assertEqual(len(problems), 4)
        self.assertTrue(problems[0].startswith("快照已 60 分钟"))
        self.assertEqual(len(patrol.check(snap, market_open=False, now=now)), 3)
        self.assertEqual(patrol.check({"ts": "2026-10-02T15:55:00+00:00"}, True, now), [])
        self.assertTrue(patrol.check(None, True, now))

    def test_review_report(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.equity("2026-10-01", 100_000, 50_000, 0, 500.0)
            j.equity("2026-10-02", 101_000, 50_000, 0, 505.0)
            j.snapshot({"account": {"equity": "101000", "last_equity": "100000", "cash": "1"},
                        "positions": [{"symbol": "NVDA", "asset_class": "us_equity", "qty": "1", "avg_entry_price": "1",
                                       "current_price": "1", "market_value": "50000", "unrealized_pl": "900",
                                       "unrealized_plpc": "0.02"}],
                        "open_orders": []})
            text = review.report(Path(d), cfg(), "2026-10-02")
            self.assertIn("今日 +1.00%，SPY +1.00%，持平", text)
            self.assertIn("浮盈最多：NVDA", text)
            self.assertIn("科技相关持仓占 100%", text)
            self.assertIsNone(review.report(Path(d), cfg(), "2026-10-03"))


class Publication(unittest.TestCase):
    def test_snapshot_drops_identifiers(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.snapshot({"account": {"equity": "1", "account_number": "PA123", "id": "uuid"},
                        "positions": [{"symbol": "AAPL", "asset_id": "x", "qty": "1"}],
                        "open_orders": [{"symbol": "AAPL", "id": "o1", "client_order_id": "c1", "status": "new"}]})
            text = (Path(d) / "snapshot.json").read_text()
            for secret in ("PA123", "uuid", "asset_id", "o1", "c1"):
                self.assertNotIn(secret, text)
            self.assertIn('"equity": "1"', text)

    def test_fills_dedupe_and_day(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            fill = {"id": "act-1", "order_id": "ord-9", "transaction_time": "2026-10-01T19:03:59Z", "symbol": "AAPL", "side": "buy",
                    "qty": "47", "price": "329.75", "type": "partial_fill", "cum_qty": "47", "leaves_qty": "1"}
            self.assertEqual(j.record_fills([fill]), 1)
            self.assertEqual(j.record_fills([{**fill, "price": "329.70"}]), 0)  # broker correction, same id
            self.assertEqual([f["price"] for f in j.fills("2026-10-01")], ["329.70"])
            self.assertEqual(j.fills("2026-10-02"), [])
            from hero.journal import execution_key
            self.assertEqual(j.fills("2026-10-01")[0]["order_key"], execution_key("ord-9"))
            self.assertNotIn("ord-9", (Path(d) / "fills.jsonl").read_text())
            self.assertNotIn("act-1", (Path(d) / "fills.jsonl").read_text())

    def test_final_only_after_close(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.equity("2026-10-01", 100_000, 50_000, 0, 500.0)
            (Path(d) / "snapshot.json").write_text(json.dumps({"ts": "2026-10-01T19:47:04+00:00"}))
            self.assertFalse(review.is_final(Path(d), "2026-10-01"))
            self.assertTrue(review.report(Path(d), cfg(), "2026-10-01").startswith("# 盘中预审"))
            self.assertEqual(review.export(Path(d), cfg(), "2026-10-01")["status"], "intraday_snapshot")
            (Path(d) / "snapshot.json").write_text(json.dumps({"ts": "2026-10-01T20:10:00+00:00"}))
            self.assertTrue(review.is_final(Path(d), "2026-10-01"))
            self.assertTrue(review.report(Path(d), cfg(), "2026-10-01").startswith("# 盘后复盘"))
            self.assertEqual(review.export(Path(d), cfg(), "2026-10-01")["status"], "post_market_review")


class OrderSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        return [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()]

    def test_decision_is_journaled_before_a_failing_submit(self):
        class Boom(FakeClient):
            def submit_order(self, **o):
                raise TimeoutError("network down")
        eng = Engine(Boom({}), cfg(), self.j)
        with self.assertRaises(TimeoutError):
            eng._order("test", {"bid": 1.0}, symbol="AAPL", qty="1", side="buy", type="market", time_in_force="day")
        kinds = [e["kind"] for e in self.events()]
        self.assertEqual(kinds, ["order", "order_error"])
        self.assertEqual(self.events()[0]["evidence"], {"bid": 1.0})
        self.assertNotIn("client_order_id", self.events()[0])

    def test_close_is_linked_to_its_order(self):
        c = FakeClient({})
        Engine(c, cfg(), self.j)._close("AAPL", "dropped", {"current_price": "1"})
        from hero.journal import execution_key
        ack = self.events()[1]
        self.assertEqual((ack["kind"], ack["order_key"]), ("order_ack", execution_key("close-AAPL")))
        self.assertEqual(ack["intent_key"], self.events()[0]["intent_key"])

    def test_submit_retry_cannot_duplicate(self):
        class Resp:
            def __init__(self, code, body):
                self.status_code, self._body = code, body
                self.text = json.dumps(body)
                self.content = self.text.encode()
            def json(self):
                return self._body

        class Session:
            headers = {}
            def __init__(self):
                self.calls = []
            def request(self, method, url, timeout, **kw):
                self.calls.append((method, url))
                if method == "POST" and len(self.calls) == 1:
                    return Resp(502, {"message": "bad gateway"})  # order was accepted, response lost
                if method == "POST":
                    return Resp(422, {"message": "client_order_id must be unique"})
                return Resp(200, {"id": "orig", "status": "new"})

        import hero.alpaca as alpaca
        sess = Session()
        orig_sleep, alpaca.time.sleep = alpaca.time.sleep, lambda s: None
        try:
            placed = Alpaca(session=sess).submit_order(symbol="AAPL", qty="1", side="buy", type="market",
                                                       time_in_force="day")
        finally:
            alpaca.time.sleep = orig_sleep
        self.assertEqual(placed["id"], "orig")
        self.assertEqual([m for m, _ in sess.calls], ["POST", "POST", "GET"])
        self.assertIn("by_client_order_id", sess.calls[-1][1])

    def test_close_position_is_not_retried(self):
        class Session:
            headers = {}
            calls = 0
            def request(self, method, url, timeout, **kw):
                Session.calls += 1
                return type("R", (), {"status_code": 503, "text": "down", "content": b"down"})()
        with self.assertRaises(AlpacaError):
            Alpaca(session=Session()).close_position("AAPL")
        self.assertEqual(Session.calls, 1)


class LiveConfig(unittest.TestCase):
    def test_live_config_has_every_fixture_key(self):
        live = json.loads((Path(__file__).resolve().parent.parent / "config" / "strategy.json").read_text())
        for section in ("stocks", "options", "risk"):
            self.assertEqual(set(CFG[section]), set(live[section]), section)
        self.assertIsInstance(live["generation"], int)


class Safety(unittest.TestCase):
    def test_refuses_live_endpoint(self):
        with self.assertRaises(AlpacaError):
            Alpaca(base_url="https://api.alpaca.markets")


if __name__ == "__main__":
    unittest.main()
