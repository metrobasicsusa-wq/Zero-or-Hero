import json
import math
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from hero import alerts, backtest, dashboard, earnings, evolve, macro, market, patrol, review, stops
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

    def stock_snapshots(self, symbols):
        return {s: v for s, v in getattr(self, "snaps", {}).items() if s in symbols}

    def news(self, symbols, start, limit=50):
        return getattr(self, "headlines", [])

    def order_by_client_id(self, cid):
        found = getattr(self, "by_cid", {})
        if cid not in found:
            raise AlpacaError(f"GET order {cid} -> 404", 404)
        if isinstance(found[cid], Exception):
            raise found[cid]
        return found[cid]

    def close_position(self, s):
        self.closed.append(s)
        return {"id": f"close-{s}", "status": "accepted"}

    def option_contracts(self, und, **kw):
        self.contract_queries = getattr(self, "contract_queries", []) + [(und, kw)]
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

    def test_briefing_accuracy_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "scores.csv"
            self.assertEqual(evolve.briefing_accuracy(path), "")
            path.write_text("date,kind,prediction,result,source\n"
                            "2026-10-02,prediction,SPY 0%..+1%,hit,cnbc\n"
                            "2026-10-02,prediction,10y > 5.20%,miss,cnbc\n"
                            "2026-10-02,direction,bullish,hit,cnbc\n"
                            "2026-10-05,prediction,VIX < 20,na,\n")
            line = evolve.briefing_accuracy(path)
        self.assertIn("2 days", line)
        self.assertIn("predictions 1/2 (50%)", line)
        self.assertIn("predictions unverifiable 1", line)
        self.assertIn("bias vs SPY 1/1 (100%)", line)


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


    def test_rehearsal_shows_simulated_events_once_a_day(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            for _ in range(3):
                j.event("order", symbol="SPY261005C00777000", side="buy", qty="35", reason="zdte", dry_run=True)
            (Path(d) / "zdte.json").write_text('{"phase": "zero_dte", "sim_cash": 255.0, "history": []}')
            c = {**cfg(), "live_from": "2026-10-08", "launch_approved": False}
            data = dashboard.collect(Path(d), c)
            self.assertTrue(data["rehearsal"])
            self.assertEqual(len(data["events"]), 1)
            self.assertEqual(data["zdte"]["sim_cash"], 255.0)
            self.assertEqual(dashboard.collect(Path(d), {**c, "launch_approved": True})["events"], [])


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
        self.assertIsInstance(s500["launch_approved"], bool)  # explicit; launch approved by the user on 2026-10-06
        self.assertTrue(s500["options"]["require_held"])


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
        c["live_from"], c["launch_approved"] = "2000-01-01", True  # a gated experiment, already approved
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

    def _ending(self, d, pos, **kw):
        """Run one cycle of an account past its 40% loss line; returns the client."""
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        c = kw.pop("client", None) or FakeClient(closes, positions=pos, equity=kw.pop("equity", 295),
                                                 last_equity=320, **kw)
        Engine(c, self.s500_cfg(), Journal(Path(d))).run(today=TODAY)
        return c

    @staticmethod
    def _exits(c):
        return [o for o in c.orders if o["client_order_id"].startswith("hero-exit-")]

    POS = {"symbol": "UP1", "asset_class": "us_equity", "qty": "1.5", "avg_entry_price": "400",
           "current_price": "290", "market_value": "435"}

    def test_attempt_end_confirms_flat_before_ending(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d))
            # Cycle 1: loss line hit -> latch, exit sent for the broker-confirmed quantity, not "ended".
            c = self._ending(d, [self.POS])
            self.assertEqual([(o["symbol"], o["qty"], o["side"]) for o in self._exits(c)], [("UP1", "1.5", "sell")])
            self.assertIn("attempt_1_ending", j.state())
            self.assertNotIn("attempt_1_ended", j.state())
            # Cycle 2 (new process): the exit is still working -> wait, nothing re-sent, even though
            # equity bounced above the line.
            working = {"id": "x1", "symbol": "UP1", "side": "sell", "type": "market", "status": "new",
                       "client_order_id": c.orders[0]["client_order_id"]}
            c2 = self._ending(d, [self.POS], open_orders=[working], equity=330)
            self.assertEqual((c2.orders, c2.closed, getattr(c2, "cancelled", [])), ([], [], []))
            self.assertNotIn("attempt_1_ended", j.state())
            # Cycle 3: broker shows flat -> confirmed and ended; later cycles do nothing.
            self._ending(d, [], equity=290)
            self.assertIn("attempt_1_ended", j.state())
            c4 = self._ending(d, [], equity=290)
            self.assertEqual((c4.orders, c4.closed), ([], []))

    def test_attempt_end_partial_fill(self):
        with tempfile.TemporaryDirectory() as d:
            self._ending(d, [self.POS])
            # Partially filled and still working: wait.
            partial = {**self.POS, "qty": "0.4"}
            working = {"id": "x1", "symbol": "UP1", "side": "sell", "type": "market", "status": "partially_filled",
                       "client_order_id": "hero-exit-abc", "qty": "1.5", "filled_qty": "1.1"}
            c2 = self._ending(d, [partial], open_orders=[working])
            self.assertEqual(c2.orders, [])
            # The rest expired unfilled: exactly one new exit, for the remaining 0.4 only.
            c3 = self._ending(d, [partial])
            self.assertEqual([o["qty"] for o in self._exits(c3)], ["0.4"])
            self.assertNotIn("attempt_1_ended", Journal(Path(d)).state())

    def test_attempt_end_uncertain_submit_then_restart(self):
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}

        def crashed(d):
            class Timeout(FakeClient):
                def submit_order(self, **o):
                    self.orders.append(o)
                    raise TimeoutError("read timed out")  # did it reach the broker? unknown
            c = Timeout(closes, positions=[self.POS], equity=295, last_equity=320)
            with self.assertRaises(TimeoutError):
                self._ending(d, None, client=c)
            st = Journal(Path(d)).state()
            self.assertIn("attempt_1_ending", st)  # the latch survived the crash
            cid = c.orders[0]["client_order_id"]
            self.assertEqual(st["attempt_1_exits"], {"UP1": cid})  # saved before the broker call
            return cid

        def restart(d, **broker):
            c = FakeClient(closes, positions=broker.pop("positions", [self.POS]), equity=295, last_equity=295,
                           open_orders=broker.pop("open_orders", []))
            c.by_cid = broker.pop("by_cid", {})
            self._ending(d, None, client=c)
            return c

        with tempfile.TemporaryDirectory() as d:  # reached the broker, not yet in the open-orders list
            cid = crashed(d)
            self.assertEqual(restart(d, by_cid={cid: {"status": "accepted"}}).orders, [])
        with tempfile.TemporaryDirectory() as d:  # broker answers "not found": never arrived -> send once
            crashed(d)
            self.assertEqual(len(self._exits(restart(d))), 1)
        with tempfile.TemporaryDirectory() as d:  # lookup itself fails: unknown -> wait, no resend
            cid = crashed(d)
            c = restart(d, by_cid={cid: AlpacaError("GET -> 503", 503)})
            self.assertEqual(c.orders, [])
            self.assertIn("无法确认上一张平仓单的状态", (Path(d) / "trades.jsonl").read_text())
        with tempfile.TemporaryDirectory() as d:  # it filled: flat -> confirmed and ended
            cid = crashed(d)
            c = restart(d, positions=[], by_cid={cid: {"status": "filled"}})
            self.assertEqual(c.orders, [])
            self.assertIn("attempt_1_ended", Journal(Path(d)).state())
        with tempfile.TemporaryDirectory() as d:  # it was canceled with the position intact -> send again
            cid = crashed(d)
            self.assertEqual(len(self._exits(restart(d, by_cid={cid: {"status": "canceled"}}))), 1)

    def test_attempt_end_cancel_and_fill_interleave(self):
        with tempfile.TemporaryDirectory() as d:
            stop = {"id": "s1", "symbol": "UP1", "side": "sell", "type": "stop", "client_order_id": "hero-stop-1"}
            # The stop filled while we were cancelling it: do not also sell this cycle.
            c = FakeClient({"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)},
                           positions=[self.POS], open_orders=[stop], equity=295, last_equity=320)
            c.cancel_result = "filled"
            self._ending(d, None, client=c)
            self.assertEqual((c.cancelled, c.orders), (["s1"], []))
            # Next cycle the broker shows the stop's partial fill left 0.5: exit only that.
            c2 = self._ending(d, [{**self.POS, "qty": "0.5"}])
            self.assertEqual([o["qty"] for o in self._exits(c2)], ["0.5"])

    def test_attempt_end_retries_after_rejection_and_unconfirmed_cancel(self):
        with tempfile.TemporaryDirectory() as d:
            class Rejecting(FakeClient):
                def submit_order(self, **o):
                    self.orders.append(o)
                    raise AlpacaError("403 rejected")
            c = Rejecting({"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)},
                          positions=[self.POS], equity=295, last_equity=320)
            self._ending(d, None, client=c)  # rejection is journaled, not "ended"
            self.assertIn("order_error", (Path(d) / "trades.jsonl").read_text())
            self.assertNotIn("attempt_1_ended", Journal(Path(d)).state())
            stop = {"id": "s1", "symbol": "UP1", "side": "sell", "type": "stop", "client_order_id": "hero-stop-1"}
            c2 = FakeClient({"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)},
                            positions=[self.POS], open_orders=[stop], equity=295, last_equity=295)
            c2.cancel_result = "pending_cancel"
            self._ending(d, None, client=c2)
            self.assertEqual((c2.cancelled, c2.orders), (["s1"], []))
            c3 = self._ending(d, [self.POS], open_orders=[stop])
            self.assertEqual((c3.cancelled, len(self._exits(c3))), (["s1"], 1))

    def test_options_only_on_broker_confirmed_holdings(self):
        with tempfile.TemporaryDirectory() as d:
            closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1), "UP2": series(0.0015, seed=2)}
            conf = self.s500_cfg(options={**CFG["options"], "enabled": True, "max_positions": 1, "allocation": 0.2,
                                          "require_held": True, "bear_puts": False})
            c = FakeClient(closes, equity=100_000, last_equity=100_000)  # no stock held yet
            Engine(c, conf, Journal(Path(d))).run(today=TODAY)
            self.assertFalse([o for o in c.orders if o["type"] == "limit"])
            self.assertIn("还没有券商确认的股票持仓", (Path(d) / "trades.jsonl").read_text())
            held = {"symbol": "UP1", "asset_class": "us_equity", "qty": "3", "avg_entry_price": "100",
                    "current_price": "110", "market_value": "330"}
            c2 = FakeClient(closes, positions=[held], equity=100_000, last_equity=100_000)
            Engine(c2, conf, Journal(Path(d))).run(today=TODAY)
            opts = [o for o in c2.orders if o["type"] == "limit"]
            self.assertTrue(opts and all(o["symbol"].startswith("UP1") for o in opts))
            # Bear regime: no index puts for this experiment.
            bear = {**closes, "SPY": series(-0.002, seed=4)}
            c3 = FakeClient(bear, positions=[held], equity=100_000, last_equity=100_000)
            Engine(c3, conf, Journal(Path(d))).run(today=TODAY)
            self.assertFalse([o for o in c3.orders if o["type"] == "limit"])

    def test_launch_needs_approval_not_just_the_date(self):
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        base = {k: v for k, v in self.s500_cfg().items() if k not in ("live_from", "launch_approved")}

        def sends(conf):
            with tempfile.TemporaryDirectory() as d:
                c = FakeClient(closes, equity=500, last_equity=500)
                Engine(c, conf, Journal(Path(d))).run(today=TODAY)
                return bool(c.orders)
        ok = {**base, "live_from": "2000-01-01", "launch_approved": True}
        self.assertTrue(sends(ok))
        # Every missing, empty or malformed gate field means rehearsal.
        for bad in ({"launch_approved": False}, {"launch_approved": None}, {"launch_approved": "true"},
                    {"live_from": None}, {"live_from": ""}, {"live_from": "soon"}, {"live_from": "2099-01-01"}):
            self.assertFalse(sends({**ok, **bad}), bad)
        self.assertFalse(sends({k: v for k, v in ok.items() if k != "launch_approved"}))
        self.assertFalse(sends({k: v for k, v in ok.items() if k != "live_from"}))
        # The main account has no gate at all and trades normally.
        main = {k: v for k, v in base.items() if k != "attempt"}
        self.assertTrue(sends(main))

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


class Earnings(unittest.TestCase):
    ROWS = [{"symbol": "UP1", "reportDate": (TODAY + timedelta(days=5)).isoformat(), "timeOfTheDay": "pre-market",
             "estimate": "1.0", "fiscalDateEnding": "2026-09-30"},
            {"symbol": "ZZZ", "reportDate": TODAY.isoformat(), "timeOfTheDay": "", "estimate": "", "fiscalDateEnding": ""}]

    def cal(self, rows=None, fetched=TODAY):
        from datetime import datetime
        return earnings.build(self.ROWS if rows is None else rows, {"UP1", "UP2", "SPY"},
                              datetime(fetched.year, fetched.month, fetched.day, 9, 0))

    def test_calendar_helpers(self):
        c = self.cal()
        self.assertEqual(list(c["reports"]), ["UP1"])  # only our own symbols are kept
        r = earnings.next_report(c, "UP1", TODAY)
        self.assertEqual(earnings.last_safe_expiry(r), TODAY + timedelta(days=4))  # pre-market: day before
        post = {**r, "time": "post-market"}
        self.assertEqual(earnings.last_safe_expiry(post), TODAY + timedelta(days=5))  # after the close: same day ok
        self.assertIsNone(earnings.next_report(c, "UP2", TODAY))
        self.assertTrue(earnings.usable(c, TODAY + timedelta(days=3)))
        self.assertFalse(earnings.usable(c, TODAY + timedelta(days=4)))
        self.assertFalse(earnings.usable(None, TODAY))

    def test_refresh_calls_api_once_per_day(self):
        from datetime import datetime
        calls = []
        orig = earnings.fetch
        earnings.fetch = lambda key: calls.append(key) or self.ROWS
        try:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "earnings.json"
                now = datetime(2026, 10, 1, 9, 35)
                self.assertTrue(earnings.refresh(path, {"UP1"}, "k", now).startswith("fetched"))
                self.assertEqual(earnings.refresh(path, {"UP1"}, "k", now), "fresh")
                self.assertTrue((Path(d) / "earnings" / "2026-10-01.json").exists())  # point-in-time archive
                earnings.refresh(path, {"UP1"}, "k", datetime(2026, 10, 2, 9, 35))
                self.assertEqual(len(calls), 2)
        finally:
            earnings.fetch = orig

    def engine(self, d, cal, positions=()):
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        conf = cfg(stocks={"rsi_max": 101, "top_n": 1},
                   options={"enabled": True, "max_positions": 1, "allocation": 0.2, "min_dte": 3, "max_dte": 10,
                            "earnings_guard": True, "exit_dte": 1, "max_rank": 1})
        c = FakeClient(closes, positions=list(positions))
        Engine(c, conf, Journal(Path(d)), earnings=cal).run(today=TODAY)
        return c, (Path(d) / "trades.jsonl").read_text()

    def test_no_calendar_no_option(self):
        with tempfile.TemporaryDirectory() as d:
            c, log = self.engine(d, None)
            self.assertNotIn("UP1", [u for u, _ in getattr(c, "contract_queries", [])])
            self.assertIn("财报日历不可用", log)

    def test_expiry_capped_before_report(self):
        with tempfile.TemporaryDirectory() as d:
            c, _ = self.engine(d, self.cal())
            und, kw = c.contract_queries[0]
            self.assertEqual(kw["expiration_date_lte"], (TODAY + timedelta(days=4)).isoformat())

    def test_report_too_close_skips(self):
        rows = [{**self.ROWS[0], "reportDate": (TODAY + timedelta(days=2)).isoformat()}]
        with tempfile.TemporaryDirectory() as d:
            c, log = self.engine(d, self.cal(rows))
            self.assertNotIn("UP1", [u for u, _ in getattr(c, "contract_queries", [])])
            self.assertIn("会跨过财报", log)

    def test_held_option_sold_before_report(self):
        exp = TODAY + timedelta(days=6)
        sym = f"UP1{exp:%y%m%d}C00100000"
        pos = {"symbol": sym, "asset_class": "us_option", "unrealized_plpc": "0.1", "qty": "1"}
        rows = [{**self.ROWS[0], "reportDate": (TODAY + timedelta(days=1)).isoformat()}]
        with tempfile.TemporaryDirectory() as d:
            c, log = self.engine(d, self.cal(rows), positions=[pos])
            self.assertIn(sym, c.closed)
            self.assertIn("财报前平仓", log)


class Market(unittest.TestCase):
    def test_series_and_news_summaries(self):
        pts = [{"date": f"2026-09-{d:02d}", "value": str(4.0 + d / 100)} for d in range(1, 30)] + \
              [{"date": "2026-09-30", "value": "."}]
        x = market.summarize_series(pts, "%")
        self.assertEqual((x["date"], x["latest"], x["chg_1d"]), ("2026-09-29", 4.29, 0.01))
        self.assertEqual(x["chg_20d"], 0.2)
        feed = [{"title": "Up", "url": "u1", "source": "s", "time_published": "t",
                 "ticker_sentiment": [{"ticker": "NVDA", "relevance_score": "0.9", "ticker_sentiment_score": "0.5"},
                                      {"ticker": "XYZ", "relevance_score": "1", "ticker_sentiment_score": "-1"}]},
                {"title": "Down", "url": "u2", "source": "s", "time_published": "t",
                 "ticker_sentiment": [{"ticker": "NVDA", "relevance_score": "0.1", "ticker_sentiment_score": "-0.5"}]}]
        n = market.summarize_news(feed, {"NVDA"})
        self.assertEqual(list(n), ["NVDA"])  # only our symbols
        self.assertEqual((n["NVDA"]["articles"], n["NVDA"]["sentiment"]), (2, 0.4))  # relevance-weighted
        self.assertEqual(n["NVDA"]["label"], "偏多")
        self.assertEqual(n["NVDA"]["top"][0]["title"], "Up")

    def test_build_survives_one_failed_series(self):
        calls = []

        def fake(params, key):
            calls.append(params["function"])
            if params.get("maturity") == "2year":
                raise RuntimeError("premium endpoint")
            if params["function"] == "NEWS_SENTIMENT":
                return {"feed": []}
            return {"data": [{"date": "2026-10-01", "value": "4.1"}]}
        orig = market._get
        market._get = fake
        try:
            from datetime import datetime
            d = market.build("k", {"NVDA"}, datetime(2026, 10, 2, 9, 0), pause=0)
        finally:
            market._get = orig
        self.assertEqual(len(calls), 4)
        self.assertIn("us2y", d["errors"])
        self.assertEqual(sorted(d["series"]), ["us10y", "wti"])
        self.assertTrue(any("10 年期美债 4.10%" in l for l in market.lines(d)))

    def test_retry_only_requests_what_failed(self):
        from datetime import datetime
        calls = []

        def fake(params, key):
            calls.append(params.get("maturity") or params["function"])
            if params["function"] == "NEWS_SENTIMENT":
                return {"feed": []}
            return {"data": [{"date": "2026-10-01", "value": "4.1"}]}
        orig = market._get
        market._get = fake
        try:
            now = datetime(2026, 10, 5, 9, 10)
            prev = {"fetched_on": "2026-10-05", "series": {"us10y": {"latest": 5.2}, "wti": {"latest": 90.0}},
                    "news": {"NVDA": {}}, "news_window": "w", "errors": {"us2y": "rate limited"}}
            d = market.build("k", {"NVDA"}, now, pause=0, previous=prev)
            self.assertEqual(calls, ["2year"])
            self.assertEqual(d["errors"], {})
            self.assertEqual(d["series"]["us10y"], {"latest": 5.2})
            calls.clear()
            market.build("k", {"NVDA"}, now, pause=0, previous={**prev, "fetched_on": "2026-10-02"})
            self.assertEqual(len(calls), 4)  # yesterday's data is not reused
        finally:
            market._get = orig

    def test_refresh_caps_daily_calls(self):
        from datetime import datetime
        runs = []
        orig = market.build
        market.build = lambda key, symbols, now, **kw: runs.append(1) or {"fetched_on": now.date().isoformat(),
                                                                    "series": {}, "errors": {"us2y": "x"}}
        try:
            with tempfile.TemporaryDirectory() as d:
                path, now = Path(d) / "market.json", datetime(2026, 10, 2, 9, 0)
                for _ in range(5):  # every 10-minute cycle of the day
                    market.refresh(path, {"NVDA"}, "k", now)
                self.assertEqual(len(runs), market.MAX_ATTEMPTS)
                market.refresh(path, {"NVDA"}, "k", datetime(2026, 10, 5, 9, 0))  # next day: fetch again
                self.assertEqual(len(runs), market.MAX_ATTEMPTS + 1)
        finally:
            market.build = orig


def snap(prev, last):
    return {"prevDailyBar": {"c": prev}, "latestTrade": {"p": last}}


class Alerts(unittest.TestCase):
    def test_headline_patterns(self):
        hits = {
            "Acme slashes full-year guidance as demand weakens": ["下调指引"],
            "SEC opens probe into Acme accounting": ["监管调查"],
            "Acme files for Chapter 11 bankruptcy protection": ["破产"],
            "Morgan Stanley downgrades Acme to equal-weight": ["降级"],
            "Acme CEO steps down effective immediately": ["高管离职"],
            "Acme announces $500 million stock offering": ["增发"],
            "Trading halted in Acme shares pending news": ["停牌"],
        }
        for headline, want in hits.items():
            self.assertEqual(alerts.match(headline), want, headline)
        for quiet in ("Acme raises guidance after record quarter", "Analyst upgrades Acme to buy",
                      "Cramer recalls the 2008 crash", "Acme beats estimates; CEO says demand strong"):
            self.assertEqual(alerts.match(quiet), [], quiet)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1), "UP2": series(0.0015, seed=2)}
        self.conf = cfg(stocks={"rsi_max": 101, "top_n": 2})

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, snaps=None, headlines=(), positions=(), open_orders=()):
        c = FakeClient(self.closes, positions=list(positions), open_orders=list(open_orders))
        c.snaps, c.headlines = snaps or {}, list(headlines)
        Engine(c, self.conf, Journal(Path(self.tmp.name))).run(today=TODAY)
        log = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()]
        return c, log

    def test_market_breaker_blocks_buys_and_options_and_tightens(self):
        held = {"symbol": "UP1", "asset_class": "us_equity", "qty": "10", "avg_entry_price": "100",
                "current_price": "200", "market_value": "2000"}
        old_stop = {"id": "s1", "symbol": "UP1", "side": "sell", "type": "stop", "qty": "10",
                    "stop_price": "170.00", "client_order_id": "hero-stop-1"}
        c, log = self.run_with(snaps={"SPY": snap(500, 487)}, positions=[held], open_orders=[old_stop])
        kinds = [e["kind"] for e in log]
        self.assertIn("circuit", kinds)
        self.assertFalse([o for o in c.orders if o["side"] == "buy"])  # no stock buys, no options
        self.assertIn("buy_blocked", kinds)
        self.assertEqual(c.cancelled, ["s1"])
        new = [o for o in c.orders if o["type"] == "stop"]
        self.assertEqual(new[0]["stop_price"], "194.00")  # 3% under the 200 current price
        self.assertEqual(c.closed, [])  # the breaker never sells by itself

    def test_news_alert_blocks_that_name_only_and_is_logged_once(self):
        h = {"id": 7, "headline": "UP1 slashes outlook on weak orders", "symbols": ["UP1"], "source": "benzinga",
             "url": "https://example.com/7", "created_at": "2026-10-01T12:00:00Z"}
        c, log = self.run_with(headlines=[h])
        bought = {o["symbol"] for o in c.orders if o["side"] == "buy" and o["type"] == "market"}
        self.assertNotIn("UP1", bought)
        self.assertTrue(bought)  # the other targets are still bought
        self.assertEqual([e["matched"] for e in log if e["kind"] == "news_alert"], [["下调指引"]])
        _, log2 = self.run_with(headlines=[h])  # same headline next cycle: no second alert
        self.assertEqual(sum(e["kind"] == "news_alert" for e in log2), 1)

    def test_stock_drop_tightens_stop_without_selling(self):
        held = {"symbol": "UP1", "asset_class": "us_equity", "qty": "10", "avg_entry_price": "100",
                "current_price": "90", "market_value": "900"}
        c, log = self.run_with(snaps={"UP1": snap(100, 90)}, positions=[held])
        self.assertIn("stock_drop_alert", [e["kind"] for e in log])
        stop = [o for o in c.orders if o["type"] == "stop"][0]
        self.assertEqual(stop["stop_price"], "87.30")
        self.assertEqual(c.closed, [])


class ResearchShort(unittest.TestCase):
    def test_runs_on_both_universes_with_late_listings(self):
        from hero import research_short as rs

        class Bars:
            def daily_bars(self, symbols, start):
                out = {}
                for k, sym in enumerate(symbols):
                    xs = series(0.0004 * (k % 5 - 2), 700, k)
                    late = 200 if k % 7 == 3 else 0  # some names list part-way through
                    out[sym] = bars(xs)[late:]
                return out
        rep = rs.run(Bars(), Path(__file__).resolve().parent.parent)
        self.assertEqual(set(rep["results"]), {"Claude", "Claude-500"})
        for r in rep["results"].values():
            self.assertEqual(set(r["variants"]), {"long_only", "short_leg", "long_short", "bear_short", "bear_inverse"})
            self.assertTrue(all(v["days"] > 100 for v in r["variants"].values()))
        self.assertIn("做空研究回测", rs.markdown(rep))


class ResearchUniverse(unittest.TestCase):
    def test_dynamic_pool_uses_only_past_liquidity(self):
        from hero import research_universe as ru

        class Fake:
            def assets(self):
                return [{"symbol": s, "tradable": True, "marginable": True, "shortable": True, "fractionable": True,
                         "exchange": "NYSE"} for s in ("AAA", "BBB", "CCC", "DDD")] + \
                       [{"symbol": "BRK.B", "tradable": True, "marginable": True, "shortable": True,
                         "fractionable": True, "exchange": "NYSE"}]

            def daily_bars(self, symbols, start):
                out = {}
                for k, sym in enumerate(symbols):
                    bs = bars(series(0.0003 * (k % 4 - 1), 900, k))
                    for i, b in enumerate(bs):
                        # DDD becomes the most traded name only in the second half
                        b["v"] = 1e7 if (sym == "DDD" and i > 600) else 1e5 * (10 - k)
                    out[sym] = bs
                return out
        self.assertNotIn("BRK.B", ru.candidates(Fake()))
        c = Fake()
        syms = ["AAA", "BBB", "CCC", "DDD", "SPY"]
        dates, closes, dvol = ru.align(c.daily_bars(syms, "2020-01-01"), "SPY")
        firsts = {s: 0 for s in syms}
        pools = ru.monthly_pools(dates, closes, dvol, firsts, ["AAA", "BBB", "CCC", "DDD"], 1, 300)
        early, late = pools[min(pools)], pools[max(pools)]
        self.assertNotEqual(early, ["DDD"])
        self.assertEqual(late, ["DDD"])  # picked up once it became liquid, not before
        rep = ru.run(c, Path(__file__).resolve().parent.parent)
        self.assertEqual(len(rep["results"]["Claude"]["variants"]), 3)
        self.assertIn("股票池回测", ru.markdown(rep))


class ResearchS500(unittest.TestCase):
    def test_restart_rule_replay(self):
        from hero import research_s500 as r5
        self.assertEqual(r5.attempts([0.1, 0.1]), {"attempts": 1, "final_attempt_multiple": 1.21, "net_per_500": 105.0})
        # -50% ends attempt 1; attempt 2 starts fresh at $500 and doubles: $1000 - $1000 allocated = 0
        self.assertEqual(r5.attempts([-0.5, 1.0]), {"attempts": 2, "final_attempt_multiple": 2.0, "net_per_500": 0.0})
        p = r5.params(10, 2)
        self.assertEqual((p["trend_sma"], p["top_n"], p["gross_exposure"]), (20, 2, 0.9))


class DynamicUniverse(unittest.TestCase):
    class Broker:
        def assets(self):
            return [{"symbol": s, "tradable": True, "marginable": True, "shortable": True, "fractionable": True,
                     "exchange": "NASDAQ"} for s in ("BIG", "MID", "SMALL", "CHEAP", "NEW")]

        def daily_bars(self, symbols, start):
            spec = {"BIG": (300, 50, 1e7), "MID": (300, 50, 1e6), "SMALL": (300, 50, 1e4),
                    "CHEAP": (300, 5, 1e9), "NEW": (100, 50, 1e9)}  # (days, price, volume)
            return {s: [{"t": f"d{i}", "c": spec[s][1], "v": spec[s][2]} for i in range(spec[s][0])]
                    for s in symbols if s in spec}

    def test_build_ranks_by_dollar_volume_with_filters(self):
        from datetime import datetime
        from hero import universe
        d = universe.build(self.Broker(), 2, datetime(2026, 10, 2, 9, 0))
        self.assertEqual(d["stocks"], ["BIG", "MID"])  # CHEAP under $10, NEW under a year: excluded
        self.assertEqual(d["month"], "2026-10")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.json"
            self.assertTrue(universe.refresh(path, self.Broker(), 2, datetime(2026, 10, 2)).startswith("built"))
            self.assertEqual(universe.refresh(path, self.Broker(), 2, datetime(2026, 10, 20)), "fresh")
            self.assertTrue((Path(tmp) / "universe" / "2026-10.json").exists())
            self.assertIn("SPY", universe.symbols(universe.load(path)))

    def test_held_name_outside_the_pool_is_sold_when_the_account_is_ours(self):
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1), "OLD": series(0.003, seed=9)}
        held = {"symbol": "OLD", "asset_class": "us_equity", "qty": "2", "market_value": "500",
                "avg_entry_price": "100", "current_price": "250"}
        conf = cfg(stocks={"rsi_max": 101, "top_n": 1})
        conf["universe"] = ["SPY", "UP1"]
        with tempfile.TemporaryDirectory() as d:
            c = FakeClient(closes, positions=[held])
            Engine(c, conf, Journal(Path(d))).run(today=TODAY)
            self.assertEqual(c.closed, [])  # not ours without owns_account: left alone
        with tempfile.TemporaryDirectory() as d:
            c = FakeClient(closes, positions=[held])
            Engine(c, {**conf, "owns_account": True}, Journal(Path(d))).run(today=TODAY)
            self.assertEqual(c.closed, ["OLD"])  # strongest momentum, but no longer in the pool
            self.assertIn("不在股票池", (Path(d) / "trades.jsonl").read_text())

    def test_config_falls_back_to_static_list(self):
        from hero import __main__ as cli
        conf = {"universe": ["A"], "dynamic_universe": {"size": 100}}
        orig = cli.UNIVERSE
        try:
            cli.UNIVERSE = Path("/nonexistent/universe.json")
            self.assertEqual(cli.apply_dynamic_universe(conf)["universe"], ["A"])
        finally:
            cli.UNIVERSE = orig


class PoolFilters(unittest.TestCase):
    def test_funds_excluded_and_share_classes_merged(self):
        from hero import research_universe as ru

        class Assets:
            def assets(self):
                base = {"tradable": True, "marginable": True, "shortable": True, "fractionable": True, "exchange": "NASDAQ"}
                return [{**base, "symbol": s, "name": n} for s, n in (
                    ("TQQQ", "ProShares UltraPro QQQ"), ("SQQQ", "ProShares UltraPro Short QQQ"),
                    ("TLT", "iShares 20+ Year Treasury Bond ETF"), ("IBIT", "iShares Bitcoin Trust ETF"),
                    ("GOOGL", "Alphabet Inc. Class A Common Stock"), ("GOOG", "Alphabet Inc. Class C Capital Stock"),
                    ("NVDA", "NVIDIA Corporation Common Stock"))]
        c = ru.candidates(Assets())
        self.assertEqual(sorted(c), ["GOOG", "GOOGL", "NVDA"])
        pool = ru.top_by_company([(5.0, "GOOGL"), (4.0, "GOOG"), (3.0, "NVDA")], c, 2)
        self.assertEqual(pool, ["GOOGL", "NVDA"])  # GOOG is the same company


class ResearchEarnings(unittest.TestCase):
    def test_reaction_day_and_summary(self):
        from datetime import datetime
        from hero import research_earnings as re_
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        pre = datetime(2026, 1, 6, 7, 2, tzinfo=re_.ET)
        post = datetime(2026, 1, 6, 16, 5, tzinfo=re_.ET)
        self.assertEqual(re_.reaction_index(dates, pre), 1)   # before the open: reacts that day
        self.assertEqual(re_.reaction_index(dates, post), 2)  # after the close: reacts next day
        self.assertIsNone(re_.reaction_index(dates, datetime(2026, 1, 7, 16, 30, tzinfo=re_.ET)))
        block = {"form": ["8-K", "8-K", "10-Q"], "filingDate": ["2026-01-06", "2026-01-08", "2026-01-06"],
                 "acceptanceDateTime": ["2026-01-06T21:05:00.000Z", "2026-01-08T12:00:00.000Z", "2026-01-06T21:00:00.000Z"],
                 "items": ["2.02,9.01", "5.02", ""]}
        self.assertEqual([t.hour for t in re_._item_202(block, "2026-01-01")], [16])
        ev = [{"symbol": "X", "move": m, "gap": m, "reaction_day": "2026-01-06", "timing": "after close",
               "implied_move": 0.05, "ratio": abs(m) / 0.05, "mom126": mo, "cheapness": c}
              for m, mo, c in [(0.2, 0.5, 0.5), (-0.02, 0.1, 1.0), (0.03, -0.1, 1.2)] * 12]
        s = re_.summarize({"events": ev})
        self.assertEqual((s["big"], s["big_up"]), (12, 12))
        self.assertEqual(s["by_mom126"][2]["rate"], 1.0)  # the strong-momentum third holds every big up-move
        self.assertIn("超过预期", re_.markdown({"generated": "2026-10-02", "symbols": 1, "since": "2021", "missing": []}, s))


class ResearchLottery(unittest.TestCase):
    def test_costs_strikes_and_bankroll(self):
        from hero import research_lottery as rl
        self.assertAlmostEqual(rl.entry_cost(0.05), 0.06)    # at least a cent of spread
        self.assertAlmostEqual(rl.exit_value(2.0), 1.8)
        self.assertEqual(rl.pick_strike([100, 105, 110], 104), 105)
        self.assertIsNone(rl.pick_strike([100], 120))
        names = rl.strategies({"spot": 100.0, "implied_move": 0.05})
        self.assertAlmostEqual(names["预期波动 2 倍处"], 110.0)
        trades = [{"strategy": "价外 10%", "reaction_day": f"2025-0{i}-01", "ret_reaction": r, "ret_expiry": -1.0}
                  for i, r in enumerate([-1.0, -1.0, 19.0], 1)]
        s = rl.summarize(trades)["价外 10%"]["ret_reaction"]
        self.assertEqual((s["n"], s["hit_rate"], s["best"]), (3, 0.333, 19.0))
        self.assertAlmostEqual(s["bank_500"], 500 * 0.9 * 0.9 * (1 + 0.1 * 19), delta=1)  # 10% of balance per bet
        self.assertIn("持有到期", rl.markdown({"generated": "d", "source": "x", "events": 3, "slippage": 0.1, "bet": 0.1},
                                          rl.summarize(trades)))
        self.assertIn("ret_tp5", dict(rl.EXITS))


class ResearchFlow(unittest.TestCase):
    def test_signal_needs_both_surge_and_breakout(self):
        from hero import research_flow as rf
        self.assertEqual(rf.months("2024-01-15", "2024-03-02")[-1], ("2024-03-01", "2024-03-02"))
        dates = [f"d{i:03d}" for i in range(40)]
        closes = [100.0] * 25 + [101.0, 99.0, 102.0] + [100.0] * 12
        vol = {d: 10.0 for d in dates}
        vol["d025"] = 50.0   # surge on a breakout day -> signal
        vol["d026"] = 50.0   # surge without a breakout -> nothing
        sig, brk = rf.signals(dates, closes, vol)
        self.assertEqual([dates[i] for i in sig], ["d025"])
        self.assertIn(27, brk)  # breakout without a surge
        self.assertAlmostEqual(rf.forward([100.0, 110.0, 121.0], [0], 2)[0], 0.21)


class ResearchRatchet(unittest.TestCase):
    def test_switches_after_a_hit_and_caps_the_loss(self):
        from hero import research_ratchet as rr
        days = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        idx = rr.growth_index(days, [0.0, 0.0, 0.10, 0.0])
        self.assertAlmostEqual(rr.value_at(idx, days, "2025-01-06"), 1.1)
        self.assertEqual(rr.value_at(idx, days, "2024-12-31"), 1.0)
        evs = [{"reaction_day": "2025-01-03", "ret": -1.0}, {"reaction_day": "2025-01-03", "ret": 19.0},
               {"reaction_day": "2025-01-07", "ret": -1.0}]
        w = rr.window(evs, idx, days, "2025-01-02", "2025-01-07", stake=10, budget=100)
        # core 400 * 1.1; pot 100 - 10 + 190 = 280, switched on 01-03 then +10%; the last bet is never placed
        self.assertTrue(w["hit"])
        self.assertAlmostEqual(w["final"], 400 * 1.1 + 280 * 1.1)
        loss = rr.window([{"reaction_day": "2025-01-03", "ret": -1.0}] * 20, idx, days, "2025-01-02", "2025-01-07", 10, 100)
        self.assertAlmostEqual(loss["final"], 400 * 1.1)  # the most it can lose is the lottery budget
        weekly = rr.pick([{"reaction_day": "2025-01-06", "mom126": 0.1}, {"reaction_day": "2025-01-07", "mom126": 0.5}], "weekly")
        self.assertEqual([e["mom126"] for e in weekly], [0.5])


class LotterySleeve(unittest.TestCase):
    def test_schedule_pick_and_contract(self):
        from datetime import date
        from hero import lottery as lot
        fri = date(2026, 10, 9)
        self.assertEqual(lot.schedule({"date": "2026-10-09", "time": "pre-market"}), (date(2026, 10, 8), fri))
        self.assertEqual(lot.schedule({"date": "2026-10-09", "time": "post-market"}), (fri, date(2026, 10, 12)))
        self.assertEqual(lot.schedule({"date": "2026-10-12", "time": "unknown"}), (fri, date(2026, 10, 13)))
        cal = {"reports": {"A": [{"date": "2026-10-08", "time": "post-market"}],
                           "B": [{"date": "2026-10-09", "time": "pre-market"}],
                           "C": [{"date": "2026-10-20", "time": "post-market"}]}}
        pick = lot.pick_for_week(cal, {"A": 0.5, "B": 0.9, "C": 2.0}, date(2026, 10, 5))
        self.assertEqual((pick["symbol"], pick["entry"], pick["exit"]), ("B", "2026-10-08", "2026-10-09"))
        p = lot.settings({})
        cs = [{"symbol": f"K{k}", "strike_price": str(k)} for k in (105, 110, 115, 130)]
        snaps = {"K110": {"latestQuote": {"ap": 0.80}}, "K115": {"latestQuote": {"ap": 0.40}},
                 "K130": {"latestQuote": {"ap": 0.05}}}
        c, ask = lot.choose_contract(cs, snaps, 100.0, p)
        self.assertEqual((c["symbol"], ask), ("K115", 0.40))  # 110 costs $80 > $50, so walk out to 115

    def test_ledger_stops(self):
        from hero import lottery as lot
        with tempfile.TemporaryDirectory() as d:
            L = lot.Ledger(Path(d) / "lottery.json", lot.settings({}))
            L.opened({"symbol": "X", "cost": 40.0})
            self.assertEqual(L.reserve(), 160.0)
            L.closed(0.0, "d1")
            self.assertTrue(L.active)
            L.opened({"symbol": "Y", "cost": 20.0})
            L.closed(400.0, "d2")  # 20x
            self.assertFalse(L.active)
            self.assertIn("中奖", L.d["done"])
            self.assertEqual(L.reserve(), 0.0)  # everything goes back to the momentum book

    def test_engine_buys_late_on_entry_day_and_sells_after_the_report(self):
        from datetime import datetime, timedelta
        from hero import lottery as lot
        today = TODAY  # 2026-10-01, a Thursday
        cal = {"fetched_on": today.isoformat(),
               "reports": {"UP1": [{"date": (today + timedelta(days=1)).isoformat(), "time": "pre-market"}]}}
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        spot = closes["UP1"][-1]

        class Broker(FakeClient):
            def option_contracts(self, und, **kw):
                exp = kw["expiration_date_gte"]
                return [{"symbol": f"{und}C{k}", "strike_price": str(k), "expiration_date": exp, "type": "call"}
                        for k in (round(spot * 1.12, 2), round(spot * 1.2, 2))]

            def option_snapshots(self, symbols):
                return {s: {"latestQuote": {"bp": getattr(self, "bid", 0.3), "ap": 0.35}} for s in symbols}
        conf = {**cfg(stocks={"rsi_max": 101, "top_n": 1}, options={"enabled": False}),
                "lottery": {"enabled": True, "budget": 200, "stake_max": 50}}
        with tempfile.TemporaryDirectory() as d:
            early = Broker(closes)
            Engine(early, conf, Journal(Path(d)), earnings=cal).run(today=today, now=datetime(2026, 10, 1, 11, 0))
            self.assertFalse([o for o in early.orders if o.get("type") == "limit"])  # picked, but not bought before 15:30
            late = Broker(closes)
            Engine(late, conf, Journal(Path(d)), earnings=cal).run(today=today, now=datetime(2026, 10, 1, 15, 40))
            calls = [o for o in late.orders if o.get("type") == "limit"]
            self.assertEqual([(o["qty"], o["limit_price"]) for o in calls], [("1", "0.35")])
            ledger = json.loads((Path(d) / "lottery.json").read_text())
            self.assertEqual((ledger["pot"], ledger["open"]["exit"]), (165.0, "2026-10-02"))
            # Next day: the call is held as a position with a big loss; the normal stop rules must not touch it,
            # the sleeve sells it at the open after the report.
            pos = {"symbol": ledger["open"]["symbol"], "asset_class": "us_option", "unrealized_plpc": "-0.9", "qty": "1"}
            nxt = Broker(closes, positions=[pos])
            nxt.bid = 3.5  # it paid off
            Engine(nxt, conf, Journal(Path(d)), earnings=cal).run(today=today + timedelta(days=1),
                                                                  now=datetime(2026, 10, 2, 9, 35))
            self.assertEqual(nxt.closed, [pos["symbol"]])
            ledger = json.loads((Path(d) / "lottery.json").read_text())
            self.assertEqual(ledger["bets"][0]["ret"], 9.0)
            self.assertIn("中奖", ledger["done"])
            log = (Path(d) / "trades.jsonl").read_text()
            self.assertIn("lottery_pick", log)
            self.assertIn("lottery_result", log)


class ResearchHero(unittest.TestCase):
    def test_attempts_end_at_target_or_loss_line(self):
        from hero import research_hero as rh
        d = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
        stream = [(d[1], -0.5), (d[2], 9.0), (d[3], 1.0)]
        self.assertEqual(rh.play(stream, d[0], 10)[0], "zero")             # -50% crosses the 40% line
        self.assertEqual(rh.play(stream, d[1], 10)[:2], ("hero", d[2]))    # a fresh attempt from d1 hits 10x
        seq = rh.sequential(stream, d[0], 10)
        self.assertEqual((seq["heroes"], seq["zeros"], seq["median_days_to_hero"]), (1, 1, 1))
        mix = rh.mixed([(d[1], 2.0), (d[5], 5.0)], [(d[2], 1.0), (d[3], 1.0)], 3.0)
        self.assertEqual(rh.play(mix, d[0], 10)[:2], ("hero", d[3]))  # 3x, then the second leg doubles twice


class Research0DTE(unittest.TestCase):
    def test_symbols_strikes_and_exits(self):
        from hero import research_0dte as z
        self.assertEqual(z.occ("SPY", "2024-02-01", "C", 495.0), "SPY240201C00495000")
        self.assertEqual(z.strike_for(490.2, "C", 0.003), 492.0)   # rounded outward
        self.assertEqual(z.strike_for(490.2, "P", 0.003), 488.0)
        bars = {"10:00": {"c": 0.10, "h": 0.10}, "11:00": {"c": 0.20, "h": 0.60}, "15:30": {"c": 0.05, "h": 0.05}}
        self.assertAlmostEqual(z.trade(bars, "10:00", 5), 4.0)     # cost 0.11, the 0.60 high reaches 5x (0.55)
        self.assertAlmostEqual(z.trade(bars, "10:00", 10), (0.04 / 0.11) - 1)  # never 10x: out at 15:30 minus a cent
        self.assertIsNone(z.trade(bars, "12:00", 3))               # nothing traded within 5 minutes of 12:00
        self.assertEqual(z.at_or_after({"10:03": {}}, "10:00")[0], "10:03")


class ResearchEvents(unittest.TestCase):
    def test_events_from_headlines(self):
        from hero import research_events as ev
        days = ["2026-08-20", "2026-08-21", "2026-10-05", "2026-10-06", "2026-10-07"]
        items = [
            {"symbols": ["MRVL"], "created_at": "2026-08-20T12:00:00Z", "headline": "Marvell to host investor day on October 6"},
            {"symbols": ["MRVL"], "created_at": "2026-10-06T15:00:00Z", "headline": "Marvell stock rallies following investor day targets"},
            {"symbols": ["MRVL"], "created_at": "2026-10-06T21:00:00Z", "headline": "Marvell investor day recap"},
            {"symbols": ["MRVL", "A", "B", "C"], "created_at": "2026-10-06T15:00:00Z", "headline": "Investor day roundup"},
            {"symbols": ["MRVL"], "created_at": "2026-10-05T15:00:00Z", "headline": "Why Marvell stock is tanking today"},
        ]
        es = ev.find_events(items, "MRVL", days)
        self.assertEqual(len(es), 1)
        self.assertEqual((es[0]["reaction_day"], es[0]["kind"], es[0]["pre_announced"]), ("2026-10-06", "investor", True))
        self.assertEqual(ev.reaction_day("2026-10-05T20:30:00Z", days), "2026-10-06")   # 16:30 ET -> next day
        self.assertEqual(ev.kind_of("Apple unveils iPhone at keynote"), "product")
        self.assertEqual(ev.half_bank([{"reaction_day": "a", "ret_tp5": 4.0}, {"reaction_day": "b", "ret_tp5": -1.0}], "ret_tp5"),
                         {"final": 750, "best": 1500, "zeros": 0, "heroes": 0})


class ZeroDTE(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        self.conf = {**cfg(stocks={"rsi_max": 101, "top_n": 1}, options={"enabled": False}),
                     "attempt": {"number": 1, "start_capital": 500, "end_loss": 0.9},
                     "live_from": "2000-01-01", "launch_approved": False,  # rehearsal: simulated fills
                     "zero_dte": {"enabled": True, "underlying": "SPY", "entry": "12:00", "offset": 0.003,
                                  "direction": "trend", "take": 5, "fraction": 1.0, "exit_at": "15:30", "switch_at": 2.0}}

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, hh, mm, bid=0.45, ask=0.50, day=TODAY, equity=500):
        from datetime import datetime

        class Broker(FakeClient):
            def option_snapshots(self, symbols):
                return {s: {"latestQuote": {"bp": bid, "ap": ask}} for s in symbols}
        c = Broker(self.closes, equity=equity, last_equity=equity)
        c.snaps = {"SPY": {"dailyBar": {"o": 600.0}, "latestTrade": {"p": 603.0}}}
        Engine(c, self.conf, Journal(Path(self.tmp.name))).run(today=day, now=datetime(day.year, day.month, day.day, hh, mm))
        book = json.loads((Path(self.tmp.name) / "zdte.json").read_text())
        path = Path(self.tmp.name) / "trades.jsonl"
        log = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        return c, book, log

    def test_buy_take_profit_and_switch_to_momentum(self):
        c, book, log = self.cycle(11, 50)
        self.assertIsNone(book["today"])                      # before the entry time: nothing
        self.assertFalse([e for e in log if e["kind"] == "targets"])  # and no momentum while phase 1 runs
        c, book, log = self.cycle(12, 5)
        t = book["today"]
        self.assertEqual((t["symbol"], t["qty"], t["take_price"]), ("SPY261001C00605000", 10, 2.5))  # up day -> calls
        self.assertEqual(book["sim_cash"], 0.0)               # all in
        self.assertEqual(c.orders, [])                        # rehearsal sends nothing
        c, book, log = self.cycle(12, 15, bid=2.6)
        self.assertEqual(book["history"][0]["ret"], 4.0)
        self.assertEqual(book["phase"], "momentum")           # $2,500 >= 2 x $500: phase 1 over for good
        self.assertIn("zdte_switch", [e["kind"] for e in log])
        c, book, log = self.cycle(12, 25)
        self.assertTrue([e for e in log if e["kind"] == "targets"])  # momentum runs from now on

    def test_losing_day_below_50_ends_the_round(self):
        self.cycle(12, 5)
        c, book, log = self.cycle(15, 35, bid=0.02)
        self.assertEqual(book["history"][0]["proceeds"], 20.0)
        self.assertIn("zdte_attempt_end", [e["kind"] for e in log])
        self.assertEqual(book["sim_cash"], 500)                # rehearsal resets to a fresh $500
        self.assertEqual(book["phase"], "zero_dte")


class ZeroDTELive(unittest.TestCase):
    """Launched: real (paper) orders, the take resting at the broker, a cancel-then-sell exit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "zdte.json").write_text(json.dumps(
            {"phase": "zero_dte", "switched": None, "sim_cash": 990.0, "start": 500.0, "today": None,
             "history": [{"date": "2026-10-05", "ret": 2.0}]}))  # what the rehearsal left behind
        self.conf = {**cfg(stocks={"rsi_max": 101, "top_n": 1}, options={"enabled": False}),
                     "attempt": {"number": 1, "start_capital": 500, "end_loss": 0.9},
                     "live_from": "2000-01-01", "launch_approved": True,
                     "zero_dte": {"enabled": True, "underlying": "SPY", "entry": "10:00", "offset": 0.006,
                                  "direction": "trend", "take": 3, "fraction": 0.5, "exit_at": "15:30", "switch_at": 2.0}}
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        self.pos, self.oo = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, hh, mm, bid=0.06, ask=0.07):
        from datetime import datetime
        test = self

        class Broker(FakeClient):
            def option_snapshots(self, symbols):
                return {s: {"latestQuote": {"bp": bid, "ap": ask}} for s in symbols}

            def submit_order(self, **o):
                super().submit_order(**o)
                test.oo.append({"id": f"order-{len(self.orders)}", "symbol": o["symbol"], "side": o["side"]})
                return {"id": f"order-{len(self.orders)}"}

            def cancel_order(self, oid):
                test.oo[:] = [o for o in test.oo if o["id"] != oid]
                return super().cancel_order(oid)

        c = Broker(self.closes, positions=self.pos, open_orders=self.oo, equity=500, last_equity=500)
        c.pos, c.oo = self.pos, self.oo
        c.snaps = {"SPY": {"dailyBar": {"o": 770.0}, "latestTrade": {"p": 772.0}}}
        Engine(c, self.conf, Journal(self.root)).run(today=TODAY, now=datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm))
        book = json.loads((self.root / "zdte.json").read_text())
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()]
        return c, book, log

    def test_live_round_trip_with_cancel_then_sell(self):
        c, book, _ = self.cycle(10, 5)
        self.assertEqual(book["mode"], "live")
        self.assertEqual(book["rehearsal"]["sim_cash"], 990.0)        # the rehearsal is kept, not traded on
        self.assertNotIn("sim_cash", book)
        buy = c.orders[0]
        self.assertEqual((buy["side"], buy["type"], buy["limit_price"], buy["qty"]), ("buy", "limit", "0.07", "35"))
        sym = buy["symbol"]
        self.oo[:] = []                                                # the buy fills
        self.pos.append({"symbol": sym, "qty": "35", "market_value": "245"})
        c, book, _ = self.cycle(10, 15)
        take = c.orders[0]
        self.assertEqual((take["side"], take["limit_price"], take["qty"]), ("sell", "0.21", "35"))
        c, book, _ = self.cycle(15, 30, bid=0.03)                     # time exit: cancel the take, then sell
        self.assertEqual(c.cancelled, ["order-1"])
        self.assertEqual(c.closed, [sym])
        self.assertEqual(book["today"]["status"], "open")             # booked once the broker shows us flat
        self.pos[:] = []
        c, book, log = self.cycle(15, 40)
        self.assertEqual((book["today"]["status"], book["today"]["proceeds"]), ("closed", 105.0))  # 35 x $0.03
        self.assertEqual(c.closed, [])
        self.assertEqual([e for e in log if e["kind"] == "zdte_result"][-1]["dry_run"], False)

    def test_unfilled_buy_is_cancelled_not_booked_as_a_loss(self):
        self.cycle(10, 5)
        c, book, _ = self.cycle(15, 30)
        self.assertEqual(c.cancelled, ["order-1"])
        self.assertEqual(c.closed, [])
        self.assertEqual((book["today"]["exit"], book["today"]["ret"]), ("买单没有成交，已撤单", 0.0))

    def test_take_filled_at_the_broker(self):
        self.cycle(10, 5)
        self.oo[:] = []
        sym = json.loads((self.root / "zdte.json").read_text())["today"]["symbol"]
        self.pos.append({"symbol": sym, "qty": "35", "market_value": "245"})
        self.cycle(10, 15)
        self.oo[:], self.pos[:] = [], []                               # the take fills
        c, book, _ = self.cycle(11, 0)
        self.assertEqual((book["today"]["exit"], book["today"]["proceeds"]), ("止盈 3 倍（券商挂单成交）", 735.0))
        self.assertEqual(c.orders, [])                                 # no second trade the same day


class Safety(unittest.TestCase):
    def test_refuses_live_endpoint(self):
        with self.assertRaises(AlpacaError):
            Alpaca(base_url="https://api.alpaca.markets")


if __name__ == "__main__":
    unittest.main()
