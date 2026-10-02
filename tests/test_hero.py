import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from hero import backtest, dashboard, evolve, macro, patrol, review, stops
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
    def cancel_order(self, oid):
        self.cancelled = getattr(self, "cancelled", []) + [oid]
        return getattr(self, "cancel_result", "canceled")

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

    def test_macro_gauges_and_scaling(self):
        flat = [100.0] * 60
        calm = {"TLT": flat, "USO": flat, "VIXY": flat, "UUP": flat}
        stress = {"TLT": flat[:-20] + [100 - i * 0.4 for i in range(20)],   # bonds -7.6%: yields up
                  "USO": flat[:-20] + [100 + i for i in range(20)],         # oil +19%
                  "VIXY": flat, "UUP": flat}
        self.assertFalse(macro.risk_off(macro.gauges(calm)))
        g = macro.gauges(stress)
        self.assertTrue(g["rates"]["flag"] and g["oil"]["flag"])
        self.assertTrue(macro.risk_off(g))
        self.assertIn("2/4", macro.summary(g))
        closes = {"UP1": series(0.002, seed=1)}
        p = {**CFG["stocks"], "rsi_max": 101}
        base = mom.target_weights(closes, p, "SPY", 1.0, macro_closes=stress)["UP1"]
        self.assertAlmostEqual(base, p["gross_exposure"])  # scale 1.0: record only
        cut = mom.target_weights(closes, {**p, "macro_scale": 0.5}, "SPY", 1.0, macro_closes=stress)["UP1"]
        self.assertAlmostEqual(cut, p["gross_exposure"] * 0.5)
        calm_w = mom.target_weights(closes, {**p, "macro_scale": 0.5}, "SPY", 1.0, macro_closes=calm)["UP1"]
        self.assertAlmostEqual(calm_w, p["gross_exposure"])

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
            {"symbol": "UP2261001C00010000", "asset_class": "us_option", "unrealized_plpc": "0.9"}])
        res = Engine(c, cfg(stocks={"rsi_max": 101}), self.j).run(today=TODAY)
        self.assertEqual(res["status"], "ok")
        self.assertIn("DOWN", c.closed)
        self.assertIn("UP2261001C00010000", c.closed)
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
        whys = {e["symbol"]: e.get("why", "") for e in logged if e["kind"] in ("order", "close")}
        self.assertIn("达到止盈线", whys["UP2261001C00010000"])
        self.assertTrue(whys["DOWN"].startswith("移出目标：跌破"))
        self.assertTrue(all(w for w in whys.values()), whys)
        close_ev = next(e["evidence"] for e in logged if e["kind"] == "close" and e["symbol"].startswith("UP2261"))
        self.assertEqual(close_ev["unrealized_plpc"], "0.9")

        # Second run the same day must not rebalance again.
        c2 = FakeClient(self.closes)
        Engine(c2, cfg(stocks={"rsi_max": 101}, options={"enabled": False}), self.j).run(today=TODAY)
        self.assertEqual(c2.orders, [])

    def test_macro_cut_is_applied_and_explained(self):
        flat = [100.0] * 400
        stress = {"TLT": flat[:-20] + [100 - i * 0.4 for i in range(20)],
                  "USO": flat[:-20] + [100 + i for i in range(20)], "VIXY": flat, "UUP": flat}
        c = FakeClient({**self.closes, **stress})
        Engine(c, cfg(stocks={"rsi_max": 101, "macro_scale": 0.5}), self.j).run(today=TODAY)
        logged = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()]
        t = next(e for e in logged if e["kind"] == "targets")
        self.assertTrue(t["macro_risk_off"] and t["macro_applied"])
        self.assertNotIn("TLT", t["weights"])  # gauges are never traded
        buys = [e for e in logged if e["kind"] == "order" and e.get("reason") == "rebalance"]
        self.assertTrue(buys and all("宏观警报 2/4" in e["why"] for e in buys))

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
        mc = {"TLT": series(0, 700, 11), "USO": series(0, 700, 12), "VIXY": series(0, 700, 13), "UUP": series(0, 700, 14)}
        new, report = evolve.evolve(c, closes, [], TODAY, mc)
        self.assertIn(new["stocks"]["macro_scale"], (1.0, 0.5))


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

    def test_cancel_waits_for_final_state(self):
        class Resp:
            def __init__(self, code, body=None):
                self.status_code, self._b = code, body
                self.content = json.dumps(body).encode() if body is not None else b""
                self.text = self.content.decode()
            def json(self):
                return self._b

        class Session:
            headers = {}
            def __init__(self):
                self.gets = 0
            def request(self, method, url, timeout, **kw):
                if method == "DELETE":
                    return Resp(204)
                self.gets += 1
                return Resp(200, {"status": "pending_cancel" if self.gets < 3 else "canceled"})

        import hero.alpaca as alpaca
        orig, alpaca.time.sleep = alpaca.time.sleep, lambda s: None
        try:
            sess = Session()
            self.assertEqual(Alpaca(session=sess).cancel_order("o1"), "canceled")
            self.assertEqual(sess.gets, 3)
        finally:
            alpaca.time.sleep = orig

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

    def test_s500_config_is_complete_and_disjoint(self):
        root = Path(__file__).resolve().parent.parent
        s500 = json.loads((root / "config" / "s500.json").read_text())
        live = json.loads((root / "config" / "strategy.json").read_text())
        for section in ("stocks", "options", "risk"):
            self.assertLessEqual(set(CFG[section]), set(s500[section]), section)
        self.assertFalse(set(s500["universe"]) & set(live["universe"]))
        self.assertIn(s500["regime_symbol"], s500["universe"])
        self.assertTrue(s500["stocks"]["fractional"])
        self.assertEqual(s500["risk"]["stop_tif"], "day")


class Reasons(unittest.TestCase):
    def test_drop_reasons(self):
        p = {**CFG["stocks"], "rsi_max": 80, "top_n": 1}
        closes = {"UP1": series(0.002, seed=1), "UP2": series(0.0015, seed=2),
                  "HOT": [100.0 * 1.01 ** i for i in range(300)]}
        sc = mom.scores(closes, p)
        self.assertIn("RSI 100 过热", mom.why_dropped("HOT", sc["HOT"], p))
        # With RSI unconstrained both climbers are eligible; the steeper one ranks first.
        loose = {**p, "rsi_max": 101}
        sc2 = mom.scores({"HOT": closes["HOT"], "UP1": closes["UP1"]}, loose)
        self.assertEqual((sc2["HOT"]["rank"], sc2["UP1"]["rank"]), (1, 2))
        self.assertEqual(mom.why_dropped("UP1", sc2["UP1"], loose), "动量排名第 2，只持有前 1 名")
        self.assertTrue(mom.why_selected("HOT", sc2["HOT"], loose).startswith("动量排名第 1/1"))
        self.assertEqual(mom.why_dropped("GONE", None, p), "不在股票池或没有行情数据")


class ProtectiveStops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.tmp.name))
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}

    def tearDown(self):
        self.tmp.cleanup()

    def pos(self, sym, qty="10", entry="100", cur="105"):
        return {"symbol": sym, "asset_class": "us_equity", "qty": qty, "avg_entry_price": entry,
                "current_price": cur, "market_value": str(float(qty) * float(cur))}

    def stop_order(self, sym, qty, oid="s1"):
        return {"id": oid, "symbol": sym, "qty": qty, "side": "sell", "type": "stop", "status": "new",
                "client_order_id": stops.PREFIX + "abc", "submitted_at": "2026-10-01T14:00:00Z"}

    def run_engine(self, c, **over):
        conf = cfg(stocks={"rsi_max": 101}, options={"enabled": False}, **over)
        Engine(c, conf, self.j).run(today=TODAY)
        return c

    def test_places_gtc_stop_sized_to_volatility(self):
        c = self.run_engine(FakeClient(self.closes, positions=[self.pos("UP1")]))
        placed = [o for o in c.orders if o["type"] == "stop"]
        self.assertEqual(len(placed), 1)
        o = placed[0]
        self.assertEqual((o["symbol"], o["qty"], o["side"], o["time_in_force"]), ("UP1", "10", "sell", "gtc"))
        self.assertTrue(o["client_order_id"].startswith(stops.PREFIX))
        pct = 1 - float(o["stop_price"]) / 100  # below the entry (100), which is under the current price
        self.assertTrue(CFG["risk"]["stop_min_pct"] - 0.001 <= pct <= CFG["risk"]["stop_max_pct"] + 0.001, pct)

    def test_existing_matching_stop_is_kept_and_does_not_block_rebalance(self):
        c = FakeClient(self.closes, positions=[self.pos("UP1")], open_orders=[self.stop_order("UP1", "10")])
        self.run_engine(c)
        self.assertFalse([o for o in c.orders if o["type"] == "stop"])
        self.assertFalse(getattr(c, "cancelled", []))
        # UP1 is held with a resting stop, yet the rebalance still sized it (stop is not "busy").
        targets = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()
                   if '"targets"' in l]
        self.assertIn("UP1", targets[0]["weights"])

    def test_quantity_change_replaces_stop(self):
        c = FakeClient(self.closes, positions=[self.pos("UP1", qty="12")], open_orders=[self.stop_order("UP1", "10")])
        self.run_engine(c)
        self.assertEqual(c.cancelled, ["s1"])
        self.assertEqual([o["qty"] for o in c.orders if o["type"] == "stop"], ["12"])

    def test_stop_cancelled_before_closing_position(self):
        down = {**self.pos("DOWN"), "current_price": "50"}
        closes = {**self.closes, "DOWN": series(-0.002, seed=3)}
        c = FakeClient(closes, positions=[down], open_orders=[self.stop_order("DOWN", "10", "sd")])
        self.run_engine(c)
        self.assertEqual(c.cancelled, ["sd"])
        self.assertIn("DOWN", c.closed)
        kinds = [json.loads(l)["kind"] for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()
                 if '"DOWN"' in l]
        self.assertLess(kinds.index("cancel"), kinds.index("close"))
        self.assertFalse([o for o in c.orders if o["type"] == "stop" and o["symbol"] == "DOWN"])

    def test_stop_that_already_filled_blocks_the_sale(self):
        down = {**self.pos("DOWN"), "current_price": "50"}
        closes = {**self.closes, "DOWN": series(-0.002, seed=3)}
        c = FakeClient(closes, positions=[down], open_orders=[self.stop_order("DOWN", "10", "sd")])
        c.cancel_result = "filled"
        self.run_engine(c)
        self.assertNotIn("DOWN", c.closed)  # the stop already sold it; never sell twice

    def test_stop_price_never_above_market(self):
        self.assertEqual(stops.stop_price(100, 80, 0.1), 72.0)
        self.assertEqual(stops.stop_price(100, 120, 0.1), 90.0)
        self.assertEqual(stops.stop_pct(None, CFG["risk"]), (CFG["risk"]["stop_max_pct"], None))

    def test_patrol_ignores_resting_stops_but_flags_missing_ones(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)
        snap = {"ts": "2026-10-02T19:55:00+00:00", "account": {},
                "positions": [{"symbol": "AAPL", "asset_class": "us_equity", "unrealized_plpc": "0"},
                              {"symbol": "MSFT", "asset_class": "us_equity", "unrealized_plpc": "0"}],
                "open_orders": [{"symbol": "AAPL", "side": "sell", "status": "new", "protective_stop": True,
                                 "submitted_at": "2026-09-01T14:00:00Z"}]}
        self.assertEqual(patrol.check(snap, market_open=True, now=now), ["MSFT 没有券商端保护性止损单"])


class Ownership(unittest.TestCase):
    def test_positions_outside_universe_are_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            foreign = [{"symbol": "PLTR", "asset_class": "us_equity", "qty": "0.6", "avg_entry_price": "20",
                        "current_price": "21", "market_value": "12.6"},
                       {"symbol": "PLTR261120C00025000", "asset_class": "us_option", "qty": "1",
                        "unrealized_plpc": "-0.9"}]
            c = FakeClient(closes, positions=foreign)
            Engine(c, cfg(stocks={"rsi_max": 101}), Journal(Path(d))).run(today=TODAY)
            self.assertEqual(c.closed, [])  # not sold, not stopped out, even at -90%
            self.assertFalse([o for o in c.orders if o["symbol"].startswith("PLTR")])


class SmallAccount(unittest.TestCase):
    def test_timestamp_parsing(self):
        from datetime import datetime, timezone
        for t in ("2026-10-02T16:00:23Z", "2026-10-02T16:00:23.123456789Z", "2026-10-02T16:00:23.5+00:00"):
            self.assertEqual(opt.parse_ts(t).replace(microsecond=0), datetime(2026, 10, 2, 16, 0, 23, tzinfo=timezone.utc))

    def test_option_filters_budget_delta_age(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 2, 16, 1, 0, tzinfo=timezone.utc)
        contracts = [{"symbol": f"C{i}", "strike_price": "10"} for i in range(4)]
        snaps = {
            "C0": {"latestQuote": {"bp": 2.0, "ap": 2.1, "t": "2026-10-02T16:00:30Z"}, "greeks": {"delta": 0.5}},   # too expensive
            "C1": {"latestQuote": {"bp": 0.80, "ap": 0.84, "t": "2026-10-02T16:00:30Z"}, "greeks": {"delta": 0.1}},  # delta out of band
            "C2": {"latestQuote": {"bp": 0.80, "ap": 0.84, "t": "2026-10-02T15:50:00Z"}, "greeks": {"delta": 0.45}}, # stale quote
            "C3": {"latestQuote": {"bp": 0.80, "ap": 0.86, "t": "2026-10-02T16:00:30Z"}, "greeks": {"delta": 0.35}}, # ok
        }
        p = {"target_delta": 0.5, "max_spread": 0.12, "delta_min": 0.25, "delta_max": 0.6, "max_quote_age_s": 120}
        c, price = opt.pick_contract(contracts, snaps, 10.0, p, budget=100, now=now)
        self.assertEqual(c["symbol"], "C3")
        self.assertAlmostEqual(price, 0.83)

    def s500_cfg(self, **over):
        c = cfg(stocks={"rsi_max": 101, "fractional": True, "top_n": 2, "gross_exposure": 0.7},
                options={"enabled": False}, risk={"stop_tif": "day", "daily_loss_halt": -1.0})
        c["universe"] = ["UP1", "UP2", "SPY"]
        c["attempt"] = {"number": 1, "start_capital": 500, "end_loss": 0.4}
        c.update(over)
        return c

    def test_fractional_notional_orders_and_day_stops(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            held = {"symbol": "SPY", "asset_class": "us_equity", "qty": "0.137", "avg_entry_price": "700",
                    "current_price": "720", "market_value": "98.64"}
            c = FakeClient(closes, positions=[held], equity=500, last_equity=500)
            Engine(c, self.s500_cfg(), Journal(Path(d))).run(today=TODAY)
            buys = [o for o in c.orders if o["side"] == "buy"]
            self.assertTrue(buys and all("notional" in o and "qty" not in o for o in buys), buys)
            stop = next(o for o in c.orders if o["type"] == "stop")
            self.assertEqual((stop["qty"], stop["time_in_force"]), ("0.137", "day"))

    def test_rehearsal_before_live_date_sends_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            c = FakeClient(closes, equity=500, last_equity=500)
            Engine(c, self.s500_cfg(live_from="2026-10-08"), Journal(Path(d))).run(today=TODAY)
            self.assertEqual(c.orders, [])
            self.assertIn('"dry_run": true', (Path(d) / "trades.jsonl").read_text())

    def test_attempt_end_liquidates_once_then_stays_flat(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            pos = {"symbol": "UP1", "asset_class": "us_equity", "qty": "1", "avg_entry_price": "400",
                   "current_price": "290", "market_value": "290"}
            c = FakeClient(closes, positions=[pos], equity=295, last_equity=320)
            res = Engine(c, self.s500_cfg(), j).run(today=TODAY)
            self.assertEqual(res["status"], "attempt_ended")
            self.assertEqual(c.closed, ["UP1"])
            self.assertTrue(j.state()["attempt_1_ended"])
            c2 = FakeClient(closes, positions=[], equity=295, last_equity=295)
            self.assertEqual(Engine(c2, self.s500_cfg(), j).run(today=TODAY)["status"], "attempt_ended")
            self.assertEqual((c2.orders, c2.closed), ([], []))

    def test_options_limited_to_top_ranked(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1),
                      "HOT": [100.0 * 1.004 ** i for i in range(400)]}
            conf = self.s500_cfg(options={**CFG["options"], "enabled": True, "max_rank": 1, "max_positions": 1,
                                          "allocation": 1.0})
            conf["universe"] = ["UP1", "HOT", "SPY"]
            c = FakeClient(closes, equity=500, last_equity=500)
            Engine(c, conf, Journal(Path(d))).run(today=TODAY)
            opts = [o for o in c.orders if o["type"] == "limit"]
            top = mom.rank(closes, conf["stocks"])[0]
            self.assertTrue(opts and all(o["symbol"].startswith(top) for o in opts), (top, opts))

    def test_option_skip_is_explained(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            conf = self.s500_cfg(options={**CFG["options"], "enabled": True, "max_positions": 1,
                                          "allocation": 0.2, "budget_filter": True})
            c = FakeClient(closes, equity=500, last_equity=500)  # fake contracts cost $410 each
            Engine(c, conf, Journal(Path(d))).run(today=TODAY)
            self.assertFalse([o for o in c.orders if o["type"] == "limit"])
            text = (Path(d) / "trades.jsonl").read_text()
            self.assertIn("没有买期权", text)
            self.assertIn("预算 $100", text)
            self.assertIn("超预算", text)  # which filter rejected the contracts

    def test_rehearsal_review_shows_would_be_trades(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            conf = {**self.s500_cfg(), "live_from": "2099-01-01", "name": "Claude-500", "regime_symbol": "SPY"}
            j = Journal(Path(d))
            Engine(FakeClient(closes, equity=500, last_equity=500), conf, j).run(today=TODAY)
            day = TODAY.isoformat()
            log = Path(d) / "trades.jsonl"  # events carry the wall-clock time; pin them to the test day
            log.write_text("\n".join(json.dumps({**json.loads(l), "ts": day + "T14:00:00+00:00"})
                                     for l in log.read_text().splitlines()) + "\n")
            text = review.report(Path(d), conf, day)
            self.assertIn("演练", text.splitlines()[0])
            self.assertIn("| | Claude-500 | SPY |", text)
            self.assertIn("buy $", text)
            self.assertEqual(review.export(Path(d), conf, day)["rehearsal"]["cycles"], 1)
            self.assertIsNone(review.rehearsal(Path(d), {**conf, "live_from": "2000-01-01"}, day))

    def test_same_day_profit_held_overnight_but_stop_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            j.record_fills([{"id": "f1", "order_id": "o1", "transaction_time": "2026-10-01T15:00:00Z",
                             "symbol": "UP1261009C00300000", "side": "buy", "qty": "1", "price": "0.9"}])
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            conf = self.s500_cfg(options={**CFG["options"], "enabled": False, "hold_overnight": True,
                                          "take_profit": 0.8, "stop_loss": -0.5, "exit_dte": 1})
            win = {"symbol": "UP1261009C00300000", "asset_class": "us_option", "unrealized_plpc": "1.2"}
            c = FakeClient(closes, positions=[win], equity=500, last_equity=500)
            Engine(c, conf, j).run(today=TODAY)
            self.assertNotIn(win["symbol"], c.closed)  # +120% on the day it was bought: wait until tomorrow
            lose = {**win, "unrealized_plpc": "-0.6"}
            c2 = FakeClient(closes, positions=[lose], equity=500, last_equity=500)
            Engine(c2, conf, j).run(today=TODAY)
            self.assertIn(lose["symbol"], c2.closed)  # stop-loss still exits the same day

    def test_broker_rejection_does_not_abort_cycle(self):
        class Rejecting(FakeClient):
            def submit_order(self, **o):
                if o["side"] == "buy":
                    raise AlpacaError("POST /v2/orders -> 403: pattern day trading protection")
                return super().submit_order(**o)
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
            c = Rejecting(closes, equity=500, last_equity=500)
            res = Engine(c, self.s500_cfg(), Journal(Path(d))).run(today=TODAY)
            self.assertEqual(res["status"], "ok")
            self.assertIn("pattern day trading", (Path(d) / "trades.jsonl").read_text())


class Safety(unittest.TestCase):
    def test_refuses_live_endpoint(self):
        with self.assertRaises(AlpacaError):
            Alpaca(base_url="https://api.alpaca.markets")


if __name__ == "__main__":
    unittest.main()
