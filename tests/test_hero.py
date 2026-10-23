import json
import os
import math
import random
import tempfile
import unittest
from datetime import date, datetime, timedelta
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

    def test_a_sale_stuck_behind_a_pending_stop_cancel_holds_the_buys_and_retries(self):
        # 2026-10-08: SPY's and MSFT's stops were still "pending_cancel" at 9:30, so the sale and the trim
        # were skipped while the buys went out -- the account ended about $10k on margin
        from hero import stops
        held = {"symbol": "DOWN", "asset_class": "us_equity", "qty": "50", "avg_entry_price": "100",
                "current_price": "100", "market_value": "5000"}
        stop = {"id": "s1", "symbol": "DOWN", "qty": "50", "side": "sell", "type": "stop", "status": "new",
                "client_order_id": stops.PREFIX + "abc", "submitted_at": "2026-10-01T14:00:00Z"}
        conf = cfg(stocks={"rsi_max": 101}, options={"enabled": False})
        c = FakeClient(self.closes, positions=[held], open_orders=[stop])
        c.cancel_result = "pending_cancel"
        Engine(c, conf, self.j).run(today=TODAY)
        self.assertEqual((c.closed, [o for o in c.orders if o["type"] == "market"]), ([], []))  # no sale, and no buys
        self.assertNotIn("last_rebalance", self.j.state())                                     # the next cycle retries
        logged = (Path(self.tmp.name) / "trades.jsonl").read_text()
        self.assertIn("rebalance_deferred", logged)
        c2 = FakeClient(self.closes, positions=[held], open_orders=[stop])                     # the cancel went through
        Engine(c2, conf, self.j).run(today=TODAY)
        self.assertIn("DOWN", c2.closed)
        self.assertTrue([o for o in c2.orders if o["type"] == "market" and o["side"] == "buy"])
        self.assertEqual(self.j.state()["last_rebalance"], TODAY.isoformat())

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


class AlpacaErrorRedaction(unittest.TestCase):
    def test_ids_scrubbed(self):
        e = AlpacaError('DELETE https://x/v2/orders/6a1b2c3d-1623-47fc-8139-97d6feb1982a -> 403: '
                        '{"existing_order_id":"0f0e0d0c-aaaa-4bbb-8ccc-123456789abc","account":"PA3XYZ12345"}', 403)
        self.assertNotIn("6a1b2c3d", str(e))
        self.assertNotIn("0f0e0d0c", str(e))
        self.assertNotIn("PA3XYZ12345", str(e))
        self.assertEqual(e.status, 403)


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

    def test_unkeyed_order_error(self):
        # Sleeve order errors carry no intent_key; the review must skip them, not crash.
        with tempfile.TemporaryDirectory() as d:
            Journal(Path(d)).equity("2026-10-12", 50_000, 50_000, 0, 600.0)
            (Path(d) / "trades.jsonl").write_text(json.dumps(
                {"ts": "2026-10-12T19:30:56+00:00", "kind": "order_error", "reason": "sleeve",
                 "symbol": "INTC261012C00103000", "error": "403 no available quote"}) + "\n")
            self.assertEqual(review.decisions(Path(d), "2026-10-12"), [])


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
    def test_experiment_book_and_broker_benchmark(self):
        from hero import review as rv
        row, prev = {"equity": "278.35", "cash": "278.35", "benchmark": "624.69"}, {"equity": "500", "cash": "500",
                                                                                   "benchmark": "632.05"}
        acct = {"equity": "50000", "last_equity": "50000", "cash": "50000"}
        # A $500 experiment inside a $50,000 paper account reads its own book, never the broker totals.
        eq, last, cash, basis = rv._equity({"attempt": {"start_capital": 500}}, acct, True, row, prev)
        self.assertEqual((eq, last, cash), (278.35, 500.0, 278.35))
        self.assertIn("experiment book", basis)
        self.assertEqual(rv._equity({}, acct, True, row, prev)[:2], (50000.0, 50000.0))  # main account: broker
        cfg = {"regime_symbol": "SPY"}
        snap = {"benchmark": {"symbol": "SPY", "date": "2026-10-07", "close": 777.1, "prev_close": 779.09}}
        self.assertEqual(rv._bench(cfg, "2026-10-07", snap, row, prev)[:2], (777.1, 779.09))
        # Another day's quote, or another symbol, falls back to the journal column.
        self.assertEqual(rv._bench(cfg, "2026-10-08", snap, row, prev)[:2], (624.69, 632.05))
        self.assertEqual(rv._bench({"regime_symbol": "SMH"}, "2026-10-07", snap, row, prev)[:2], (624.69, 632.05))

        class Client:
            def stock_snapshots(self, symbols):
                return {"SPY": {"dailyBar": {"t": "2026-10-07T04:00:00Z", "c": 777.1}, "prevDailyBar": {"c": 779.09}}}

        class Broken:
            def stock_snapshots(self, symbols):
                raise RuntimeError("down")
        self.assertEqual(rv.benchmark_quote(Client(), "SPY"), snap["benchmark"])
        self.assertIsNone(rv.benchmark_quote(Broken(), "SPY"))

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
        # Held at its 20% target (68 x $294 of $100k), so there is no add-on buy to clear the stop for.
        c = FakeClient(self.closes, positions=[self.pos("UP1", qty="68", cur="294")],
                       open_orders=[self.stop_order("UP1", "68")])
        self.run_engine(c)
        self.assertFalse([o for o in c.orders if o["type"] == "stop"])
        self.assertFalse(getattr(c, "cancelled", []))
        # UP1 is held with a resting stop, yet the rebalance still sized it (stop is not "busy").
        targets = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()
                   if '"targets"' in l]
        self.assertIn("UP1", targets[0]["weights"])

    def test_quantity_change_replaces_stop(self):
        c = FakeClient(self.closes, positions=[self.pos("UP1", qty="68", cur="294")],
                       open_orders=[self.stop_order("UP1", "60")])
        self.run_engine(c)
        self.assertEqual(c.cancelled, ["s1"])
        self.assertEqual([o["qty"] for o in c.orders if o["type"] == "stop"], ["68"])

    def test_stop_replacement_waits_while_the_old_cancel_is_pending(self):
        # 2026-10-20 AAPL: tightening after a news alert cancelled the old stop and placed the new one at once; the
        # cancel was still pending, so the broker rejected the new stop ("insufficient qty ... held_for_orders").
        c = FakeClient(self.closes, positions=[self.pos("UP1", qty="68", cur="294")],
                       open_orders=[self.stop_order("UP1", "60")])
        c.cancel_result = "pending_cancel"
        self.run_engine(c)
        self.assertEqual(c.cancelled, ["s1"])
        self.assertFalse([o for o in c.orders if o["type"] == "stop"])                 # not this cycle
        self.assertIn("stop_deferred", (Path(self.tmp.name) / "trades.jsonl").read_text())

    def test_an_option_without_a_quote_is_offered_at_a_cent(self):
        # 2026-10-08 SPY 781C: three market closes in a row rejected for "no available quote"
        from hero.alpaca import AlpacaError
        sym = "SPY261008C00781000"
        c = FakeClient(self.closes, positions=[{**self.pos(sym, qty="13", cur="0"), "asset_class": "us_option"}])

        def no_quote(s):
            raise AlpacaError(f'DELETE https://x/v2/positions/{s} -> 403: {{"code":40310000,"message":"order has been '
                              'rejected due to no available quote for symbol. please reenter with a limit"}', 403)
        c.close_position = no_quote
        Engine(c, cfg(), self.j)._close(sym, "zdte_exit", why="末日期权 15:30 前平仓")
        self.assertEqual([(o["symbol"], o["qty"], o["type"], o["limit_price"]) for o in c.orders], [(sym, "13", "limit", "0.01")])
        log = (Path(self.tmp.name) / "trades.jsonl").read_text()
        self.assertIn("close_retry", log)
        self.assertNotIn("order_error", log)
        c.close_position = lambda s: (_ for _ in ()).throw(AlpacaError("DELETE x -> 403: insufficient buying power", 403))
        Engine(c, cfg(), self.j)._close("UP1", "dropped", why="x")                      # other rejections stay errors
        self.assertIn("order_error", (Path(self.tmp.name) / "trades.jsonl").read_text())

    def test_journal_never_writes_broker_ids_or_account_numbers(self):
        self.j.event("order_error", error='403: {"existing_order_id":"6a1b2c3d-1623-47fc-8139-97d6feb1982a",'
                                          '"related_orders":["0f0e0d0c-aaaa-4bbb-8ccc-123456789abc"],"account":"PA3XYZ12345"}',
                     symbol="PANW261023C00200000", nested={"ids": ["6a1b2c3d-1623-47fc-8139-97d6feb1982a"]})
        line = (Path(self.tmp.name) / "trades.jsonl").read_text()
        self.assertNotIn("6a1b2c3d", line)
        self.assertNotIn("0f0e0d0c", line)
        self.assertNotIn("PA3XYZ12345", line)
        self.assertIn("<id>", line)
        self.assertIn("<account>", line)
        self.assertIn("PANW261023C00200000", line)                                      # option symbols are kept

    def test_add_on_buy_cancels_the_stop_first(self):
        # 2026-10-19: an AAPL add-on buy was rejected as a potential wash trade because our GTC sell
        # stop was still resting. Cancel the stop, buy, and re-place the stop next cycle.
        c = self.run_engine(FakeClient(self.closes, positions=[self.pos("UP1")],
                                       open_orders=[self.stop_order("UP1", "10")]))
        self.assertEqual(c.cancelled, ["s1"])
        self.assertTrue([o for o in c.orders if o["symbol"] == "UP1" and o["side"] == "buy"])
        self.assertFalse([o for o in c.orders if o["type"] == "stop" and o["symbol"] == "UP1"])
        kinds = [json.loads(l)["kind"] for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()
                 if '"UP1"' in l and '"targets"' not in l]
        self.assertLess(kinds.index("cancel"), kinds.index("order"))

    def test_add_on_buy_waits_while_the_stop_cancel_is_pending(self):
        c = FakeClient(self.closes, positions=[self.pos("UP1")], open_orders=[self.stop_order("UP1", "10")])
        c.cancel_result = "pending_cancel"
        self.run_engine(c)
        self.assertFalse([o for o in c.orders if o["symbol"] == "UP1" and o["side"] == "buy"])
        self.assertIn("buy_deferred", (Path(self.tmp.name) / "trades.jsonl").read_text())
        self.assertNotIn("last_rebalance", self.j.state())  # the next cycle retries the add-on

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
        from unittest import mock
        calls = []
        orig = earnings.fetch
        earnings.fetch = lambda key: calls.append(key) or self.ROWS
        nas = {"2026-10-06": [{"symbol": "UP1", "reportDate": "2026-10-06", "timeOfTheDay": "post-market", "estimate": None}]}
        try:
          with mock.patch.object(earnings, "fetch_nasdaq", lambda day, get=None: nas.get(day, [])), \
                mock.patch.object(earnings._time, "sleep", lambda s: None), \
                mock.patch.object(earnings, "yahoo_dates", lambda syms, **k: {"UP1": {"date": "2026-10-06", "confirmed": True}}):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "earnings.json"
                now = datetime(2026, 10, 1, 9, 35)
                self.assertTrue(earnings.refresh(path, {"UP1"}, "k", now).startswith("fetched"))
                self.assertEqual(earnings.refresh(path, {"UP1"}, "k", now), "fresh")
                self.assertTrue((Path(d) / "earnings" / "2026-10-01.json").exists())  # point-in-time archive
                earnings.refresh(path, {"UP1"}, "k", datetime(2026, 10, 2, 9, 35))
                self.assertEqual(len(calls), 2)
                data = json.loads(path.read_text())
                # Nasdaq moved UP1 to 10-06 after the close; Yahoo says the company confirmed that date
                self.assertEqual(data["reports"]["UP1"], [{"date": "2026-10-06", "time": "post-market", "estimate": "1.0",
                                                          "fiscal_end": "2026-09-30", "checked": "nasdaq", "confirmed": True}])
                self.assertEqual(data["cross_check"]["yahoo"]["confirmed"], 1)
        finally:
            earnings.fetch = orig

    def test_nasdaq_overlay_moves_fills_disputes_and_adds(self):
        av = [{"symbol": "AMZN", "reportDate": "2026-10-29", "timeOfTheDay": "", "estimate": "2.0"},
              {"symbol": "MAT", "reportDate": "2026-10-20", "timeOfTheDay": "", "estimate": "0.9"},       # stale
              {"symbol": "NOC", "reportDate": "2026-10-26", "timeOfTheDay": "pre-market", "estimate": "7"},  # moved up
              {"symbol": "NOC", "reportDate": "2027-01-27", "timeOfTheDay": "pre-market", "estimate": "8"},  # next quarter
              {"symbol": "FAR", "reportDate": "2026-12-01", "timeOfTheDay": "post-market", "estimate": None}]
        row = lambda s, d, t: {"symbol": s, "reportDate": d, "timeOfTheDay": t, "estimate": None, "fiscalDateEnding": None}
        nas = {"2026-10-20": [row("NOC", "2026-10-20", "pre-market"), row("GE", "2026-10-20", "pre-market")],
               "2026-10-29": [row("AMZN", "2026-10-29", "post-market")],
               "2026-10-30": None}                                     # Nasdaq did not answer that day
        out, ch = earnings.overlay(av, nas)
        by = {(r["symbol"], r["reportDate"]): r for r in out}
        self.assertEqual(by[("AMZN", "2026-10-29")]["timeOfTheDay"], "post-market")
        self.assertTrue(by[("MAT", "2026-10-20")]["disputed"])
        self.assertEqual(by[("NOC", "2026-10-20")]["timeOfTheDay"], "pre-market")
        self.assertIn(("NOC", "2027-01-27"), by)                       # the later quarter stays
        self.assertNotIn(("NOC", "2026-10-26"), by)
        self.assertIn(("GE", "2026-10-20"), by)                        # only Nasdaq had it
        self.assertNotIn("checked", by[("FAR", "2026-12-01")])
        self.assertEqual((ch["moved"], ch["time_filled"], ch["disputed"], ch["added"]),
                         ([["NOC", "2026-10-26", "2026-10-20"]], [["AMZN", "unknown", "post-market"]], ["MAT"], 1))

    def test_nasdaq_rows_and_yahoo_confirmation(self):
        class R:
            def __init__(self, j):
                self.j = j
            def raise_for_status(self):
                pass
            def json(self):
                return self.j
        got = earnings.fetch_nasdaq("2026-10-20", get=lambda *a, **k: R({"data": {"rows": [
            {"symbol": "GE", "time": "time-pre-market", "epsForecast": "$2.02"},
            {"symbol": "OMC", "time": "time-not-supplied", "epsForecast": ""}]}}))
        self.assertEqual([(r["symbol"], r["timeOfTheDay"], r["estimate"]) for r in got],
                         [("GE", "pre-market", "2.02"), ("OMC", "", None)])
        self.assertIsNone(earnings.fetch_nasdaq("2026-10-20", get=lambda *a, **k: 1 / 0))
        data = {"reports": {"GE": [{"date": "2026-10-20", "time": "pre-market"}],
                            "NOC": [{"date": "2026-10-26", "time": "pre-market"}],
                            "AAPL": [{"date": "2026-12-30", "time": "post-market"}]}}
        asked = []
        ys = {"GE": {"date": "2026-10-20", "confirmed": True}, "NOC": {"date": "2026-10-20", "confirmed": True}}
        sm = earnings.confirm(data, date(2026, 10, 19), lookup=lambda syms: asked.extend(syms) or ys)
        self.assertEqual(asked, ["GE", "NOC"])                         # AAPL is beyond 30 days
        self.assertTrue(data["reports"]["GE"][0]["confirmed"])
        self.assertEqual((data["reports"]["NOC"][0]["confirmed"], data["reports"]["NOC"][0]["yahoo_date"]), (False, "2026-10-20"))
        self.assertEqual(sm["conflicts"], [["NOC", "2026-10-26", "2026-10-20"]])

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
    def setUp(self):  # never touch CBOE or AAII from tests
        self._orig = market.fetch_putcall, market.fetch_aaii
        market.fetch_putcall = lambda today: {"date": "2026-10-06", "total": 0.82}
        market.fetch_aaii = lambda today: {"date": "2026-09-30", "bullish": 34.6, "neutral": 18.9,
                                           "bearish": 46.5, "spread": -11.9}

    def tearDown(self):
        market.fetch_putcall, market.fetch_aaii = self._orig

    def test_putcall_and_aaii_parsing(self):
        from datetime import date

        class Resp:
            def __init__(self, code, body=None):
                self.status_code, self._body = code, body

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(self.status_code)

            def json(self):
                return self._body
        seen = []

        def get(url, **kw):
            seen.append(url[-24:-14])
            if "2026-10-06" in url:
                return Resp(200, {"ratios": [{"name": "TOTAL PUT/CALL RATIO", "value": "0.82"},
                                             {"name": "EQUITY PUT/CALL RATIO", "value": "0.57"},
                                             {"name": "SPX + SPXW PUT/CALL RATIO", "value": "1.03"}]})
            return Resp(403)
        p = self._orig[0](date(2026, 10, 7), get=get)
        self.assertEqual(seen, ["2026-10-07", "2026-10-06"])  # today not published yet: one day back
        self.assertEqual(p, {"date": "2026-10-06", "total": 0.82, "equity": 0.57, "spx": 1.03})
        html = ("<table><tr><td>Reported Date</td><td>Bullish</td><td>Neutral</td><td>Bearish</td></tr>"
                "<tr><td>Sep 30</td><td>34.6%</td><td>18.9%</td><td>46.5%</td></tr>"
                "<tr><td>Sep 23</td><td>32.7%</td><td>19.2%</td><td>48.1%</td></tr></table>")
        a = market.parse_aaii(html, date(2026, 10, 7))
        self.assertEqual(a, {"date": "2026-09-30", "bullish": 34.6, "neutral": 18.9, "bearish": 46.5,
                             "spread": -11.9})
        self.assertEqual(market.parse_aaii(html.replace("Sep 30", "Dec 31"), date(2027, 1, 5))["date"],
                         "2026-12-31")
        lines = market.lines({"series": {}, "gauges": {"putcall": p, "aaii": a}})
        self.assertIn("CBOE 认沽/认购比（2026-10-06）：全部 0.82，个股 0.57，SPX 1.03", lines)
        self.assertTrue(any("多空差 -11.9 点" in l for l in lines))

    def test_series_and_news_summaries(self):
        pts = [{"date": f"2026-09-{d:02d}", "value": str(4.0 + d / 100)} for d in range(1, 30)] + \
              [{"date": "2026-09-30", "value": "."}]
        x = market.summarize_series(pts, "%")
        self.assertEqual((x["date"], x["latest"], x["chg_1d"]), ("2026-09-29", 4.29, 0.01))
        self.assertEqual(x["chg_20d"], 0.2)
        items = [{"id": 1, "headline": "Nvidia shares surge to record on upgrade", "summary": "", "symbols": ["NVDA", "XYZ"],
                  "url": "u1", "source": "benzinga", "created_at": "t"},
                 {"id": 2, "headline": "Nvidia supplier misses estimates, warns", "summary": "", "symbols": ["NVDA"],
                  "url": "u2", "source": "benzinga", "created_at": "t"},
                 {"id": 3, "headline": "Nvidia to present at conference", "summary": "", "symbols": ["NVDA"],
                  "url": "u3", "source": "benzinga", "created_at": "t"}]
        self.assertEqual(market.score_text("Shares surge on upgrade but guidance cut"), 1 / 3)
        self.assertEqual(market.score_text("Company to present at conference"), 0.0)
        self.assertEqual(market.score_text("Lowered expectations"), 0.0)  # whole words only
        n = market.summarize_news(items, {"NVDA"})
        self.assertEqual(list(n), ["NVDA"])  # only our symbols
        self.assertEqual((n["NVDA"]["articles"], n["NVDA"]["sentiment"]), (3, 0.0))  # (1 - 1 + 0) / 3
        self.assertEqual(n["NVDA"]["label"], "中性")
        self.assertEqual({t["title"] for t in n["NVDA"]["top"][:2]},
                         {"Nvidia shares surge to record on upgrade", "Nvidia supplier misses estimates, warns"})

    def test_fetch_news_chunks_and_dedupes(self):
        from datetime import datetime

        class Client:
            def __init__(self):
                self.calls = []

            def news_range(self, symbols, start, end):
                self.calls.append(len(symbols))
                return iter([{"id": 7, "headline": "x", "symbols": symbols[:1]},
                             {"id": len(self.calls) * 100, "headline": "y", "symbols": symbols[:1]}])
        c = Client()
        syms = {f"S{i:03d}" for i in range(120)}
        items = market.fetch_news(c, syms, datetime(2026, 10, 6, 9), datetime(2026, 10, 7, 9))
        self.assertEqual(c.calls, [50, 50, 20])
        self.assertEqual(len(items), 4)  # id 7 appears in every chunk but is kept once

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
        self.assertEqual(len(calls), 3)  # yields and oil only; news comes from Alpaca
        self.assertIn("us2y", d["errors"])
        self.assertIn("no Alpaca client", d["errors"]["news"])
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
            self.assertEqual(len(calls), 3)  # yesterday's data is not reused
        finally:
            market._get = orig

    def test_news_retries_do_not_spend_alpha_vantage_calls(self):
        from datetime import datetime
        calls = []
        orig = market._get
        market._get = lambda params, key: calls.append(params["function"]) or {"data": []}

        class Client:
            def news_range(self, symbols, start, end):
                return iter([{"id": 1, "headline": "NVDA shares surge", "symbols": ["NVDA"]}])
        try:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "market.json"
                # This morning's file: series fine, news failed, both old retries used up, no news counter.
                path.write_text(json.dumps({"fetched_on": "2026-10-07", "attempts": 2,
                                            "series": {k: {"latest": 1.0} for k in market.SERIES},
                                            "gauges": {"putcall": {"total": 1.0}, "aaii": {"spread": 0}},
                                            "errors": {"news": "premium endpoint"}}))
                now = datetime(2026, 10, 7, 10, 0)
                self.assertEqual(market.refresh(path, {"NVDA"}, "k", now), "fresh")  # no news client: wait
                market.refresh(path, {"NVDA"}, "k", now, Client())
                d1 = json.loads(path.read_text())
                self.assertEqual(calls, [])  # no Alpha Vantage call
                self.assertEqual(d1["errors"], {})
                self.assertEqual(d1["news"]["NVDA"]["sentiment"], 1.0)
                self.assertEqual((d1["attempts"], d1["news_attempts"]), (2, 1))
                self.assertEqual(market.refresh(path, {"NVDA"}, "k", now, Client()), "fresh")
                # Series exhausted with a failure left: a news retry must not re-ask Alpha Vantage.
                path.write_text(json.dumps({"fetched_on": "2026-10-07", "attempts": 2, "series": {},
                                            "gauges": {"putcall": {"total": 1.0}, "aaii": {"spread": 0}},
                                            "errors": {"us2y": "x", "news": "y"}}))
                market.refresh(path, {"NVDA"}, "k", now, Client())
                d2 = json.loads(path.read_text())
                self.assertEqual(calls, [])
                self.assertEqual(sorted(d2["errors"]), ["us2y"])
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


class ResearchFly(unittest.TestCase):
    def test_butterfly_costs_and_exits(self):
        from hero import research_fly as f
        leg = lambda prices: f.filled({t: {"c": p} for t, p in prices.items()})
        legs = [leg({"14:58": 1.5, "15:40": 1.2}), leg({"14:59": 1.0, "15:40": 0.4}), leg({"15:00": 0.6, "15:40": 0.05})]
        r = f.trade(legs, "15:00", 100.0, 1.0, 100.2)
        self.assertAlmostEqual(r["cost"], 0.225)                  # wings bought high, body sold low
        self.assertEqual(r["15:30"], -1.0)                         # worth nothing after costs at 15:30
        self.assertAlmostEqual(r["15:50"], 0.354 / 0.225 - 1, places=3)
        self.assertEqual(r["tp3"], r["15:50"])                     # never reached 3x: out at 15:50
        self.assertAlmostEqual(r["close"], 0.8 / 0.225 - 1, places=3)  # 1 - |100.2 - 100| at the close
        self.assertIsNone(f.trade(legs, "14:00", 100.0, 1.0, 100.2))   # no leg traded near 14:00


class ResearchTails(unittest.TestCase):
    def test_weeks_expiries_and_multiples(self):
        from hero import research_tails as t
        self.assertEqual(t.week_entries(["2026-09-28", "2026-09-29", "2026-10-05", "2026-10-06"]), ["2026-09-28", "2026-10-05"])
        exps = ["2026-10-02", "2026-10-09", "2026-10-23", "2026-10-30", "2026-11-20"]
        self.assertEqual(t.pick_expiry(exps, "2026-09-28", "weekly"), "2026-10-02")
        self.assertEqual(t.pick_expiry(exps, "2026-09-28", "monthly"), "2026-10-23")   # 25 days: nearest to 28
        bars = {"2026-09-28": {"c": 0.10, "h": 0.10}, "2026-09-30": {"c": 1.0, "h": 2.6}, "2026-10-02": {"c": 0.0, "h": 0.0}}
        o = t.outcome(bars, "2026-09-28", "2026-10-02", 110.0, 105.0)
        self.assertEqual(o["best"], round(2.6 * 0.9 / 0.11, 2))     # 21x at the best day's high
        self.assertEqual((o["ret_expiry"], o["ret_take20"]), (-1.0, 19.0))
        self.assertIsNone(t.outcome(bars, "2026-09-29", "2026-10-02", 110.0, 105.0))   # no trade on entry


class ResearchSeller(unittest.TestCase):
    def test_spread_take_stop_and_expiry(self):
        from hero import research_seller as r
        days = ["d0", "d1", "d2", "d3"]
        short, long = {"d0": 2.0, "d1": 0.8, "d2": 5.0}, {"d0": 1.0, "d1": 0.4, "d2": 3.0}
        # credit (2.0 - 0.06) - (1.0 + 0.03) = 0.91
        held = r.spread_result(short, long, days, "d0", "d3", 100.0, 95.0, 101.0, managed=False)
        self.assertEqual((held["credit"], held["pnl"], held["max_loss"], held["exit"]), (91.0, 91.0, 409.0, "d3"))
        managed = r.spread_result(short, long, days, "d0", "d3", 100.0, 95.0, 101.0, managed=True)
        self.assertEqual(managed["exit"], "d1")                  # cost to close 0.42 <= half the credit
        lost = r.spread_result(short, long, days, "d0", "d3", 100.0, 95.0, 90.0, managed=False)
        self.assertEqual(lost["pnl"], -409.0)                    # settled below both strikes: the full width

    def test_butterfly_reaction_and_expiry(self):
        from hero import research_seller as r
        px = {"L": {"e": 6.0, "r": 5.0}, "M": {"e": 3.0, "r": 1.2}, "H": {"e": 1.0, "r": 0.1}}
        out = r.fly_result(px, ("L", "M", "H"), (95.0, 100.0, 105.0), "e", "r", 100.5, 100.0)
        cost = (6.0 + 0.3) + (1.0 + 0.05) - 2 * (3.0 - 0.15)
        self.assertAlmostEqual(out["cost"], round(cost, 3))
        self.assertAlmostEqual(out["ret_expiry"], round(5.0 / cost - 1, 3))   # pinned at the centre: worth the width

    def test_account_curve(self):
        from hero import research_seller as r
        ts = [{"entry": "2024-03-01", "exit": "2024-03-08", "pnl": 100.0, "max_loss": 400.0},
              {"entry": "2024-03-05", "exit": "2024-03-12", "pnl": -400.0, "max_loss": 400.0},  # overlaps: skipped
              {"entry": "2024-04-01", "exit": "2024-04-08", "pnl": -400.0, "max_loss": 400.0}]
        a = r.account(ts, 0.25, "2024-02-01", "2025-02-01")
        self.assertEqual((a["final"], a["trades"]), (round(10_000 * 1.0625 * 0.75), 2))


class ResearchNet(unittest.TestCase):
    def test_ticket_needs_a_real_move_and_game_rounds(self):
        from hero import research_net as n
        stock = {"d0": 100.0, "d1": 101.0, "d2": 112.0, "d3": 104.0}
        bars = {"d0": {"c": 0.10}, "d1": {"c": 5.0}, "d2": {"c": 3.0}, "d3": {"c": 0.0}}
        t = n.ticket(bars, stock, "d0", "d3", 120.0, 100.0, 0.2)
        # d1's 5.00 print comes with the stock at 101: not counted; d2 (stock 112) is 3 * 0.9 / 0.11 = 24.5x
        self.assertEqual((t["ret_tp10"], t["ret_tp20"], t["ret_expiry"], t["best"]), (9, 19, -1.0, 24.5))
        self.assertEqual(n.game([[19.0]] * 2), {"final": 500, "best": 10_000, "heroes": 1, "zeros": 0})  # 5,250 then 55,125
        self.assertEqual(n.game([[-1.0]] * 3)["zeros"], 0)          # halves each week: $62.50 after 3
        self.assertEqual(n.game([[-1.0]] * 4)["zeros"], 1)          # $31.25 after 4: below $50

    def test_rebound_signal(self):
        from hero import research_net as n
        from datetime import date, timedelta
        days = [(date(2023, 1, 2) + timedelta(days=i)).isoformat() for i in range(400)]
        px = [100.0] * 380 + [90.0, 88.0, 87.0, 88.0, 90.0, 91.0] + [91.0] * 14
        spy = dict(zip(days, px))
        sig = n.signals(spy, [days[385], days[300]])
        self.assertEqual(sig["rebound"], [days[385]])            # down 13% from the high, 4.6% off the low


class ResearchGap(unittest.TestCase):
    def test_trail_take_and_close(self):
        from hero import research_gap as g
        m = {"09:45": {"c": 0.10}, "10:00": {"c": 0.30}, "10:30": {"c": 0.50}, "11:00": {"c": 0.25}, "15:50": {"c": 0.05}}
        r = g.simulate(m)                       # cost 0.11; values are closes less 10%
        self.assertAlmostEqual(r["trail"], 0.225 / 0.11 - 1, places=3)   # doubled, best 0.45, out at 0.225 (<= 60%)
        self.assertEqual(r["tp3"], 2.0)         # 0.45 >= 3 x 0.11
        self.assertAlmostEqual(r["close"], 0.04 / 0.11 - 1, places=3)
        self.assertIsNone(g.simulate({"10:30": {"c": 1.0}}))             # no trade near 9:45
        self.assertEqual(g.game([{"day": "a", "trail": 39.0}], "trail"), {"heroes": 1, "zeros": 0, "best": 10_000})


class ResearchFlush(unittest.TestCase):
    def test_flush_and_confirm_entry(self):
        from hero import research_flush as f
        bar = lambda o, l, c: {"o": o, "l": l, "c": c}
        sm = {"09:30": bar(100, 99.5, 99.6), "09:33": bar(99.6, 98.2, 98.4), "09:36": bar(98.4, 96.8, 97.0),
              "09:40": bar(97.0, 96.9, 97.5), "09:45": bar(97.5, 97.4, 97.9), "10:00": bar(98, 97.9, 98.5)}
        r = f.flush(sm, 0.02)
        self.assertEqual(r["hit"], "09:36")                     # 96.8 is 3.2% under the 100 open
        self.assertAlmostEqual(r["drop"], 0.032)
        self.assertEqual(r["entries"], {"10:00": "10:00", "confirm": "09:46"})  # 97.9 >= 96.8 x 1.01, buy the next minute
        self.assertEqual(f.flush(sm, 0.02)["low_at"], "09:36")
        self.assertIsNone(f.flush(sm, 0.04))                    # never 4% down
        self.assertAlmostEqual(f.first_drop(sm), 0.032)
        self.assertEqual(f.next_minute("09:59"), "10:00")


class ResearchFlushStock(unittest.TestCase):
    def test_close_stop_target_spy_and_next_day(self):
        from hero import research_flush_stock as f
        bar = lambda o, h, l, c: {"o": o, "h": h, "l": l, "c": c}
        sm = {"09:30": bar(100, 100, 97, 97.5), "10:00": bar(98, 98.2, 97.8, 98), "10:30": bar(98, 100.2, 98, 100),
              "15:59": bar(100, 100, 99, 99)}
        spy = {"10:00": 500.0, "15:59": 505.0}
        r = f.after(sm, "10:00", spy, 102.0)
        self.assertEqual(r["entry_at"], "10:00")
        self.assertAlmostEqual(r["close"], 99 / 98 - 1 - f.COST, places=4)
        self.assertAlmostEqual(r["excess"], round(99 / 98 - 1 - f.COST, 4) - 0.01, places=4)
        self.assertAlmostEqual(r["stop"], r["close"], places=4)              # never back to 97
        self.assertAlmostEqual(r["stop_tp"], 0.02 - f.COST, places=4)       # 100.2 >= 98 x 1.02
        self.assertAlmostEqual(r["next_day"], 102 / 98 - 1 - f.COST, places=4)
        sm["10:30"] = bar(98, 98.5, 96.5, 97)                                # back through the 97 low first
        r = f.after(sm, "10:00", None, None)
        self.assertAlmostEqual(r["stop"], 97 / 98 - 1 - f.COST, places=4)
        self.assertAlmostEqual(r["stop_tp"], r["stop"], places=4)
        self.assertNotIn("excess", r)


class ResearchWeekly(unittest.TestCase):
    def test_take_exit_and_spread_settlement(self):
        from hero import research_weekly as w
        m = {"2026-01-05 10:00": {"c": 1.0}, "2026-01-06 11:00": {"c": 1.5, "h": 2.3}, "2026-01-08 15:50": {"c": 0.5}}
        self.assertEqual(w.buy_trade(m, 2, "2026-01-08 15:50"), 1)              # 2.3 >= 2 x 1.10
        self.assertAlmostEqual(w.buy_trade(m, 3, "2026-01-08 15:50"), (0.5 - 0.05) / 1.1 - 1)
        r = w.spread_result(1.00, 0.40, 100, 99, 101)                            # expires above the short put
        self.assertAlmostEqual(r["credit"], (1.00 - 0.03) - (0.40 + 0.012), places=3)
        self.assertAlmostEqual(r["pnl"], r["credit"], places=3)
        self.assertAlmostEqual(w.spread_result(1.00, 0.40, 100, 99, 90)["pnl"], r["credit"] - 1, places=3)  # full loss
        self.assertIsNone(w.spread_result(0.03, 0.02, 100, 99, 101))           # no credit after costs


class ResearchLotto0DTE(unittest.TestCase):
    def test_ticket_costs_a_cent_more_and_the_game_buys_whole_contracts(self):
        from hero import research_lotto0dte as l
        t = l.ticket({"10:00": {"c": 0.02}, "12:00": {"c": 0.5, "h": 0.6}, "15:55": {"c": 2.02}})
        self.assertEqual((t["cost"], t["take20"], t["take50"]), (0.03, 19, 49))   # $0.02 + $0.01; 0.6 >= 20 x 0.03
        self.assertAlmostEqual(t["hold"], (2.02 - 0.202) / 0.03 - 1, places=3)
        self.assertIsNone(l.ticket({"10:00": {"c": 0.20}}))                          # not a lottery ticket
        g = l.game([{"day": "2026-01-02", "cost": 0.03, "hold": 66.0}], 500, "hold")
        self.assertEqual(g["end"], 500 + 8 * 3 * 66)                                 # $25 buys 8 contracts at $3


class ResearchLETF(unittest.TestCase):
    def test_holds_only_after_a_close_above_the_average(self):
        from hero import research_letf as r
        closes = [10, 10, 12, 6, 6, 9]
        eq = r.curve(closes, 2)
        # day 2 closes above its 2-day average (12 > 11): held for day 3 (12 -> 6, -50%); day 3 closes below: out
        self.assertAlmostEqual(eq[3], (1 - r.COST) ** 2 * 0.5)      # in at day 2's close, out at day 3's
        self.assertAlmostEqual(eq[4], eq[3])                       # 6 is not above the average of 6 and 6: still out
        self.assertAlmostEqual(eq[5], eq[4] * (1 - r.COST))        # 9 > 7.5: back in at the close
        self.assertAlmostEqual(r.curve(closes, None)[-1], (1 - r.COST) * 0.9)  # buy and hold
        self.assertAlmostEqual(r.max_dd([1, 2, 1, 3]), -0.5)


class ResearchReclaim(unittest.TestCase):
    def test_signal_after_taking_back_the_prior_close_and_the_exits(self):
        from hero import research_reclaim as r
        bar = lambda o, c: {"o": o, "c": c}
        m = {"09:30": bar(97, 97), "09:45": bar(98, 98.5), "10:05": bar(99, 99.8), "10:18": bar(99.9, 100.2), "11:00": bar(101, 101)}
        self.assertEqual(r.signal(m, 100.0), "10:19")                    # the minute after the first close above 100
        self.assertIsNone(r.signal({**m, "09:30": bar(101, 101)}, 100.0))  # opened above: not this setup
        self.assertIsNone(r.signal({k: v for k, v in m.items() if k < "10:18"}, 100.0))
        opt = {"10:19": {"c": 0.20}, "11:00": {"c": 0.50, "h": 0.70}, "12:00": {"c": 1.40, "h": 1.60}, "15:50": {"c": 0.30}}
        t = r.trade(opt, "10:19")
        self.assertEqual((t["cost"], t["take3"], t["take5"]), (0.22, 2, 4))      # 0.70 >= 3 x 0.22; 1.60 >= 5 x 0.22
        self.assertAlmostEqual(t["hold"], (0.30 - 0.03) / 0.22 - 1, places=3)
        self.assertAlmostEqual(t["trail"], t["hold"], places=3)                    # armed at 1.26 >= 0.44; 0.27 <= 0.6 x 1.26 only at 15:50


class ResearchZDTEFilter(unittest.TestCase):
    def test_2026_10_08_reads_as_too_small_to_call(self):
        from hero import research_zdte_filter as r
        bar = lambda o, c: {"o": o, "c": c}
        m = {"09:30": bar(774.90, 774.95), "10:00": bar(775.3, 775.355), "10:30": bar(773.0, 772.9), "11:00": bar(773.5, 773.6)}
        mv = r.moves(m, 777.15)
        d = r.decisions(mv)
        self.assertEqual(d["live"], ("10:00", "C"))                     # +0.06%: the live rule buys the call
        self.assertEqual(d["bucket|0.0|0.001"], ("10:00", "C"))
        self.assertIsNone(d["bucket|0.001|0.002"])
        self.assertIsNone(d["skip|0.001"])                               # too small: sit out
        self.assertEqual(d["wait|0.002"], ("10:30", "P"))                # -0.26% by 10:30: a put then
        self.assertIsNone(d["wait|0.003"])                               # never 0.3% away at a check
        self.assertEqual(d["prior"], ("10:00", "P"))                     # below yesterday's close
        self.assertIsNone(d["agree"])                                    # open says up, prior close says down
        self.assertIsNone(r.moves({"10:00": bar(1, 1)}, 1.0))            # no opening minute

    def test_trades_for_picks_the_rules_contract(self):
        from hero import research_zdte_filter as r
        row = {"day": "2026-10-08", "move": {"10:00": 0.0006, "10:30": -0.0025, "11:00": -0.0017}, "prior": -0.0023,
               "res": {"10:00|C|0.006": -0.7, "10:30|P|0.006": 2.0, "10:00|P|0.006": 0.5}}
        self.assertEqual(r.trades_for([row], "live", 0.006), [("2026-10-08", -0.7)])
        self.assertEqual(r.trades_for([row], "wait|0.002", 0.006), [("2026-10-08", 2.0)])
        self.assertEqual(r.trades_for([row], "prior", 0.006), [("2026-10-08", 0.5)])
        self.assertEqual(r.trades_for([row], "skip|0.001", 0.006), [])


class ResearchPlanReview(unittest.TestCase):
    def test_picks_and_each_sleeves_exit(self):
        from hero import research_plan_review as r
        ev = [{"kind": "sleeve_plan", "ts": "2026-10-08T14:00:36+00:00", "sleeve": "qqq0dte-500", "symbol": "QQQ261008P00748000",
               "why": "qqq0dte-500（演算，不下单）：...；8 张 × $0.29"},
              {"kind": "sleeve_plan", "ts": "2026-10-08T13:50:00+00:00", "sleeve": "gap-500", "symbol": "COHR261009C00330000",
               "why": "gap-500：一张 $540.00 超过 $125.00，买不起"},
              {"kind": "sleeve_plan", "ts": "2026-10-08T14:00:36+00:00", "sleeve": "tsla0dte-500", "why": "没有要买的"}]
        ps = r.picks(ev, "2026-10-08")
        self.assertEqual(ps, [{"sleeve": "qqq0dte-500", "symbol": "QQQ261008P00748000", "qty": 8, "limit": 0.29, "at": "10:00"}])
        p = ps[0]
        b = lambda h, c: {"h": h, "c": c}
        hit = r.play({"10:00": b(0.3, 0.29), "13:05": b(0.90, 0.85), "15:30": b(1.5, 1.4)}, p, "zdte", 3)
        self.assertEqual((hit["how"], hit["when"]), ("3 倍止盈", "13:05"))
        self.assertAlmostEqual(hit["exit"], 0.87)
        miss = r.play({"10:00": b(0.3, 0.29), "15:30": b(0.1, 0.05), "15:45": b(0.9, 0.9)}, p, "zdte", 3)
        self.assertEqual((miss["exit"], miss["when"]), (0.05, "15:30"))     # nothing after 15:30 counts
        gap = r.play({"10:01": b(0.6, 0.6), "11:00": b(0.9, 0.9), "12:00": b(0.5, 0.5)}, {**p, "at": "10:00"}, "gap", 3)
        self.assertEqual((gap["how"], gap["exit"]), ("翻倍后回撤 40%", 0.5))
        self.assertEqual(r.last_weekday(date(2026, 10, 12)), date(2026, 10, 9))   # a Monday reviews Friday
        held = r.play({"15:59": b(0.4, 0.4)}, p, "weekly", 2)
        self.assertEqual((held["exit"], held["how"]), (0.4, "还拿着（按收盘估值）"))


class ResearchAhReversal(unittest.TestCase):
    def test_points_and_returns_around_an_after_close_report(self):
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        from hero import research_ah_reversal as ar
        et = ZoneInfo("America/New_York")
        utc = lambda d, h, m: f"2026-10-{d:02d}T{h + 4:02d}:{m:02d}:00Z"     # EDT -> UTC
        bars = [{"t": utc(21, 15, 58), "o": 99.5, "c": 99.8}, {"t": utc(21, 15, 59), "o": 99.8, "c": 100.0},
                {"t": utc(21, 16, 10), "o": 103, "c": 104.0}, {"t": utc(21, 17, 10), "o": 105, "c": 105.0},
                {"t": utc(21, 19, 55), "o": 102, "c": 102.0},
                {"t": utc(22, 9, 30), "o": 97.0, "c": 97.2}, {"t": utc(22, 9, 35), "o": 96.8, "c": 96.5},
                {"t": utc(22, 10, 0), "o": 96.2, "c": 96.0}, {"t": utc(22, 15, 59), "o": 95.1, "c": 95.0}]
        p = ar.points(bars, date(2026, 10, 21), date(2026, 10, 22), dt(2026, 10, 21, 16, 5, tzinfo=et))
        self.assertEqual((p["c0"], p["a1h"], p["ah20"], p["o"], p["m935"], p["m10"], p["c1"]), (100.0, 0.05, 0.02, -0.03, -0.035, -0.04, -0.05))
        self.assertEqual(p["accepted"], "16:05")
        self.assertAlmostEqual(ar.ret(p["ah20"], p["m935"], ar.COST_AH, ar.COST_RTH), 0.965 * 0.9995 / (1.02 * 1.002) - 1, places=6)
        self.assertIsNone(ar.ret(None, p["m935"], 0, 0))
        # an 8-K accepted too late for an hour of after-hours trading: no first-reaction point
        self.assertIsNone(ar.points(bars, date(2026, 10, 21), date(2026, 10, 22), dt(2026, 10, 21, 19, 30, tzinfo=et))["a1h"])
        row = {**p, "symbol": "X", "day": "2026-10-22", "spy_gap": -0.009}
        md = "\n".join(ar.flips([row]))
        self.assertIn("| ≥ 6% |", md.replace("| 3–6% | 1 |", "| ≥ 6% |")) if False else self.assertIn("| 3–6% | 1 | 100% | 100% |", md)
        G = ar.strategies([row])
        self.assertEqual(len(G["20:00 买盘后涨 2% 以上的，09:35 卖"]), 1)
        self.assertEqual(G["20:00 买盘后跌 2% 以上的，09:35 卖"], [])
        L = ar.ledger_exits([row])
        self.assertAlmostEqual(L["财报前收盘买，20:00 卖"][0][1], 1.02 * 0.998 / 1.0005 - 1, places=6)


class ResearchFadeLotto(unittest.TestCase):
    def test_an_spcx_day_is_found_priced_and_grouped(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from hero import research_fade_lotto as fl
        et = ZoneInfo("America/New_York")
        utc = lambda hhmm: datetime(2026, 10, 21, int(hhmm[:2]), int(hhmm[3:]), tzinfo=et).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        day = lambda d, o, c: {"t": f"{d}T04:00:00Z", "o": o, "c": c}
        # gaps(): a split between two days is not a gap
        adj = [day("2026-10-19", 100, 100), day("2026-10-20", 100, 100), day("2026-10-21", 108.6, 105)]
        raw = [day("2026-10-19", 100, 100), day("2026-10-20", 100, 100), day("2026-10-21", 108.6, 105)]
        self.assertEqual([g["day"] for g in fl.gaps(adj, raw, "2026-10-01")], ["2026-10-21"])
        split = [day("2026-10-19", 100, 100), day("2026-10-20", 200, 200), day("2026-10-21", 108.6, 105)]   # 2-for-1 on 10-21
        self.assertEqual(fl.gaps(adj, split, "2026-10-01"), [])
        # the day: opens 160.56 (+8.6%), above the open until 13:00, then falls through it
        stock = {}
        for h in range(9, 16):
            for m in range(0, 60):
                t = f"{h:02d}:{m:02d}"
                if "09:30" <= t <= "15:59":
                    stock[t] = {"t": utc(t), "c": 162.5 if t < "13:00" else 158.0}
        opt = {t: {"t": utc(t), "c": (0.25 if t < "13:00" else 3.0)} for t in stock if t >= "10:00"}

        class C:
            def _d(self, path, params):
                syms = params["symbols"].split(",")
                bars = {"AAA": [day("2026-10-19", 147, 147), day("2026-10-20", 147.85, 147.85), day("2026-10-21", 160.56, 158.0)]}
                return {"bars": {s: bars[s] for s in syms if s in bars}}

            def stock_bars(self, syms, start, end, feed="sip"):
                return {"AAA": [dict(v) for v in stock.values()]}

            def option_contracts(self, und, **kw):
                return [{"symbol": f"AAA261021P00{k}000", "strike_price": str(k), "expiration_date": "2026-10-21"} for k in (155, 160, 165)]

            def option_bars(self, syms, start, end, timeframe="1Min"):
                return {syms[0]: [dict(v) for v in opt.values()]}
        from unittest import mock
        with mock.patch.object(fl, "START", "2026-10-21"), \
                mock.patch.object(fl.json, "loads", return_value={"stocks": ["AAA"]}):
            rep = fl.run(C(), "2026-10-21")
        rows = rep["rows"]
        self.assertEqual(sorted(r["cap"] for r in rows), [0.5, 1.0])
        r = rows[0]
        self.assertEqual((r["sym"], r["kind"], r["strike"], r["at"], r["same_day"], r["crossed"]), ("AAA", "P", 160.0, "10:00", True, True))
        self.assertEqual(r["cost"], 0.275)                                      # 0.25 + 10% (at least a cent)
        self.assertEqual(r["res"]["take3"], 2.0)                                # 3x reached at 13:00
        self.assertGreater(r["peak"], 10)
        self.assertEqual(rep["counts"]["gaps"], 1)
        g = fl.groups(rows)
        self.assertEqual(len(g["提醒条件：跳空 5%+、≤$0.50"]), 1)
        self.assertEqual(len(g["$500 账本玩法：11:00–14:00 每天第一张"]), 0)   # bought at 10:00: the ledger starts at 11:00
        self.assertIn("10 倍以上 1 张", fl.extras(rep))


class ResearchPlanResults(unittest.TestCase):
    """Owner, 10-21: what the stopped / plan-only rules would have made, run through the live exit code."""

    def test_backfill_skips_what_the_engine_follows_and_the_deploy_time_picks(self):
        from hero import research_plan_results as rp
        pk = lambda sid, sym, day: ({}, {"sym": sym}, 1, day, "15:40", {"id": sid})
        picks = [pk("earn-opt-plan-500", "NOK261023C00011000", "2026-10-21"),      # the engine has it: counted once
                 pk("gap-plan-500", "T261023C00023000", "2026-10-21"),             # only rebuilt: kept
                 pk("spy0dte-old-plan-500", "SPY261021C00778000", "2026-10-21"),   # deploy-time entry: not the rule's
                 pk("gap-plan-500", "X261024C00010000", "2026-10-22")]             # no bars yet
        eng = [{"sleeve": "earn-opt-plan-500", "sym": "NOK261023C00011000", "day": "2026-10-21"}]
        self.assertEqual([p[5]["id"] for p in rp.usable(picks, eng, "2026-10-21")], ["gap-plan-500"])

    def test_an_all_in_rule_is_played_in_order_not_summed(self):
        # one shot 10-14..10-20: the tickets sum to about +$4,500, but all-in one after another it went to zero
        from hero import research_plan_results as rp
        row = lambda d, qty, paid, got: {"day": d, "at": "10:00", "sleeve": "oneshot-500", "family": "oneshot", "qty": qty,
                                         "paid": paid, "got": got, "pnl": got - paid, "ret": got / paid - 1, "valued": True,
                                         "sym": "QQQ261014P00728000", "cost": paid / qty / 100, "how": "到时卖", "src": "backfill"}
        rows = [row("2026-10-14", 22, 462.0, 1320.0), row("2026-10-15", 7, 448.0, 7.0), row("2026-10-16", 14, 462.0, 5474.0),
                row("2026-10-19", 10, 460.0, 10.0), row("2026-10-20", 20, 480.0, 20.0)]
        self.assertGreater(sum(r["got"] - r["paid"] for r in rows), 4000)
        led = rp.ledgers(rows, {"oneshot-500": {"start": 500, "fraction": 1.0}}, {"zero_line": 0.1, "target": 10000})
        x = led["oneshot-500"]
        # 1358 -> 19 contracts lose $63 each -> 161 -> 4 contracts win $358 each -> 1593 -> 31 lose $45 -> 198
        # -> 7 lose $23 -> 37, under $50: zero, $463 topped up
        self.assertEqual((x["zeros"], x["topups"], x["pnl"], x["cash"]), (1, 463.0, -463.0, 500.0))
        poor = rp.ledgers([row("2026-10-14", 1, 46.0, 0.0), row("2026-10-14", 1, 46.0, 0.0)][:1] +
                          [{**row("2026-10-15", 1, 400.0, 0.0)}], {"oneshot-500": {"start": 500}}, {"zero_line": 0.1, "target": 10000})
        self.assertEqual(poor["oneshot-500"]["skipped"], 1)                     # 454 left: 0.9 of a contract
        md = rp.markdown(rows, [], [], "2026-10-20", 0, led)
        self.assertIn("按账本顺序", md)
        self.assertIn("-$463.00 / 1 次", md)

    def ev(self, ts, sleeve, sym, why):
        return {"kind": "sleeve_plan", "ts": ts, "sleeve": sleeve, "symbol": sym, "why": why}

    def test_rebuild_reads_each_rules_exit_from_its_own_words(self):
        from hero import research_plan_results as r
        gap = r.rebuild(self.ev("2026-10-20T13:50:25+00:00", "gap-plan-500", "IREN261023C00026500",
                                "gap-plan-500（演算，不下单）：IREN 低开 -3.4%，买看涨 26.5（2026-10-23 到期），翻倍后回撤 40% 卖，最晚 15:50；1 张 × $1.03"), {})
        pk, qty, day, at = gap
        self.assertEqual((pk["ask"], pk["exit_day"], pk["exit_at"], pk["trail"], "take" in pk, qty, at),
                         (1.02, "2026-10-20", "15:50", True, False, 1, "09:50"))
        pk, *_ = r.rebuild(self.ev("2026-10-19T19:50:30+00:00", "overnight-plan-500", "AMZN261026C00272500",
                                   "…买看涨 272.5（2026-10-26 到期）；翻倍后回撤 40% 卖，否则 10-22 15:50 卖；1 张 × $1.33"), {})
        self.assertEqual((pk["exit_day"], pk["exit_at"], pk["trail"]), ("2026-10-22", "15:50", True))
        pk, *_ = r.rebuild(self.ev("2026-10-16T13:40:00+00:00", "opengap-500", "CRWV261016P00064000",
                                   "…买看跌 64（2026-10-16 到期），一半 3 倍止盈、一半翻倍后回撤 40% 卖，最晚 15:30；3 张 × $0.75"), {})
        self.assertEqual((pk["take"], pk["half"], pk["trail"], pk["exit_at"]), (3.0, True, False, "15:30"))
        pk, *_ = r.rebuild(self.ev("2026-10-21T14:00:00+00:00", "oneshot-500", "QQQ261021C00731000",
                                   "…顺势买当天到期看涨 731（离现价 1.1%），全仓、不止盈，拿到 15:55 赌一把到目标；25 张 × $0.20"), {})
        self.assertEqual(("take" in pk, pk["exit_at"], pk["expiry_out"]), (False, "15:55", "15:55"))
        pk, *_ = r.rebuild(self.ev("2026-10-19T14:00:00+00:00", "spydip-500", "SPY261019C00772000",
                                   "…买当天到期看涨 772（离现价 0.7%），3 倍止盈，15:30 平仓；25 张 × $0.10"), {"half_take": False})
        self.assertEqual((pk["take"], pk["half"], pk["exit_day"], pk["exit_at"]), (3.0, False, "2026-10-19", "15:30"))
        pk, *_ = r.rebuild(self.ev("2026-10-21T13:35:00+00:00", "breakgap-500", "SMCI261023C00040000",
                                   "突破大跳空：…买看涨 40（2026-10-23 到期），拿到 10-22 15:50 卖；1 张 × $2.00"), {})
        self.assertEqual((pk["exit_day"], pk["trail"]), ("2026-10-22", False))
        self.assertIsNone(r.rebuild(self.ev("2026-10-08T13:50:25+00:00", "gap-500", "COHR261009C00330000",
                                            "…一张 $540.00 超过 $125.00，买不起"), {}))

    def test_replay_runs_the_live_exits_on_one_minute_trades(self):
        from hero import research_plan_results as r
        bar = lambda hhmm, c, day="2026-10-20": {"t": f"{day}T{int(hhmm[:2]) + 4:02d}:{hhmm[3:]}:00Z", "c": c}
        gap_spec = {"id": "gap-plan-500", "kind": "gap", "start": 500, "plan_only": True}
        z_spec = {"id": "opengap-500", "kind": "opengap", "start": 500, "plan_only": True}
        e1 = self.ev("2026-10-20T13:50:25+00:00", "gap-plan-500", "AAA261023C00010000", "…翻倍后回撤 40% 卖，最晚 15:50；1 张 × $1.03")
        e2 = self.ev("2026-10-20T13:50:25+00:00", "opengap-500", "BBB261020P00010000",
                     "…一半 3 倍止盈、一半翻倍后回撤 40% 卖，最晚 15:30；4 张 × $0.30")
        e3 = self.ev("2026-10-20T14:00:00+00:00", "gap-plan-500", "CCC261023C00010000", "…翻倍后回撤 40% 卖，最晚 15:50；1 张 × $1.00")
        e4 = self.ev("2026-10-20T19:50:00+00:00", "gap-plan-500", "DDD261030C00010000", "…翻倍后回撤 40% 卖，否则 10-27 15:50 卖；2 张 × $1.00")
        picks = []
        for e, spec in ((e1, gap_spec), (e2, z_spec), (e3, gap_spec), (e4, gap_spec)):
            pk, qty, day, at = r.rebuild(e, spec)
            picks.append((e, pk, qty, day, at, spec))
        tape = r.Tape({"AAA261023C00010000": [bar("10:00", 1.0), bar("11:00", 2.5), bar("11:10", 1.4)],     # armed, then 44% off
                       "BBB261020P00010000": [bar("10:30", 0.9), bar("10:40", 0.5)],                      # take 0.87, runner trails
                       "CCC261023C00010000": [bar("09:59", 1.0)],                                         # nothing after the buy
                       "DDD261030C00010000": [bar("15:55", 1.2), bar("15:00", 1.5, "2026-10-21")]})       # still held
        rows = {x["sym"][:3]: x for x in r.replay(picks, tape, "2026-10-21")}
        self.assertEqual((rows["AAA"]["paid"], rows["AAA"]["got"], rows["AAA"]["pnl"]), (102.0, 140.0, 38.0))
        self.assertIn("翻倍后从最高 $2.50 回撤 40%", rows["AAA"]["how"])
        self.assertEqual((rows["BBB"]["paid"], rows["BBB"]["got"]), (116.0, 274.0))                   # 2 x $0.87 + 2 x $0.50
        self.assertFalse(rows["CCC"]["valued"])
        self.assertEqual((rows["DDD"]["held"], rows["DDD"]["got"]), (True, 300.0))                    # 2 x $1.50, its last trade
        md = r.markdown(list(rows.values()), [], [], "2026-10-21", 0)
        self.assertIn("| gap-plan | 2 |", md)                                                          # CCC not counted
        self.assertIn("估不了值", md)

    def test_live_rows_count_entries_before_the_stop_and_skip_open_ones(self):
        from hero import research_plan_results as r
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "journal-s500").mkdir()
            t = lambda tid, day, paid, got: {"tid": tid, "opened": f"{day} 10:00", "sym": "X", "paid": paid, "proceeds": got}
            (root / "journal-s500" / "sleeves.json").write_text(json.dumps({"sleeves": {
                "gap-500": {"open": [], "closed": [t("a1", "2026-10-15", 100, 0), t("a2", "2026-10-17", 100, 300)]},
                "sniper-500": {"open": [{"tid": "b1r"}], "closed": [t("b1", "2026-10-19", 100, 20), t("c1", "2026-10-15", 100, 0)]}}}))
            rows = {(x["sleeve"], x["day"]): x for x in r.live_rows(root)}
        self.assertEqual(set(rows), {("gap-500", "2026-10-15"), ("sniper-500", "2026-10-15")})          # after the stop / still open: out
        self.assertEqual(rows[("gap-500", "2026-10-15")]["pnl"], -100.0)
        md = r.markdown([], [], list(rows.values()), "2026-10-20", 0)
        self.assertIn("| sniper-plan | 0 |", md)                                         # a stopped rule with only its live record
        self.assertIn("1 / -$100.00 / -100%", md)


class ResearchStreak(unittest.TestCase):
    def test_streaks_and_what_followed(self):
        from hero import research_streak as r
        self.assertEqual(r.streaks([1, 2, 3, 3, 2, 1, 2]), [0, 1, 2, 0, -1, -2, 1])
        closes = [100, 101, 102, 103, 101, 100, 99, 100, 101]
        bars = [{"t": f"2026-01-{i + 1:02d}T05:00:00Z", "c": c, "l": c - 0.5} for i, c in enumerate(closes)]
        rows = r.outcomes(bars)
        self.assertEqual(len(rows), len(closes) - 5)
        third = rows[3]                                     # the 3rd straight up close, at 103
        self.assertEqual(third["streak"], 3)
        self.assertAlmostEqual(third["next"], 101 / 103 - 1)
        self.assertAlmostEqual(third["five"], 101 / 103 - 1)  # 5 days later: 101
        self.assertTrue(third["dip0.01"] and third["dip0.02"])  # low 98.5 <= 103 x 0.98
        g = r.groups(rows)
        self.assertEqual(g["连涨 3 天"]["n"], 1)
        self.assertEqual(g["全部日子"]["n"], 4)


class ResearchZDTEStreak(unittest.TestCase):
    def test_groups_and_rule_results(self):
        from hero import research_zdte_streak as r
        self.assertEqual(r.groups(-2, -0.012), ["全部日子", "连跌 2 天", "连跌 2 天以上", "昨天跌 1% 以上", "连跌 2 天以上且昨天跌 1% 以上"])
        self.assertEqual(r.groups(4, 0.003), ["全部日子", "连涨 3 天以上"])
        self.assertEqual(r.groups(-3, -0.002), ["全部日子", "连跌 3 天以上", "连跌 2 天以上"])
        res = {"C|0.006|3": 2.0, "P|0.006|3": -1.0}
        self.assertEqual(r.results(res, "P", 0.006, 3), {"calls": 2.0, "puts": -1.0, "trend": -1.0, "both": 0.5})
        self.assertEqual(r.results({"C|0.006|3": -0.5}, "C", 0.006, 3), {"calls": -0.5, "trend": -0.5})

    def test_table_counts_a_day_in_each_of_its_groups(self):
        from hero import research_zdte_streak as r
        rows = [{"day": "2026-10-08", "symbol": "QQQ", "streak": -2, "prev_ret": -0.004, "side": "P",
                 "res": {"C|0.006|3": -1.0, "P|0.006|3": 2.0}},
                {"day": "2026-10-09", "symbol": "QQQ", "streak": 1, "prev_ret": 0.004, "side": "C",
                 "res": {"C|0.006|3": -1.0, "P|0.006|3": -1.0}}]
        t = r.table({"rows": rows})
        self.assertEqual(t["QQQ|0.006|3|全部日子|puts"]["n"], 2)
        self.assertEqual(t["QQQ|0.006|3|连跌 2 天以上|puts"]["mean"], 2.0)
        self.assertNotIn("QQQ|0.006|3|昨天跌 1% 以上|puts", t)
        self.assertIn("| 连跌 2 天 | 1 |", r.markdown({"generated": "x", "start": "a", "end": "b"}, t))


class ResearchNvdaHigh(unittest.TestCase):
    def test_highs_events_and_report(self):
        from hero import research_nvda_high as r
        old = r.WARMUP
        r.WARMUP = 2
        try:
            self.assertEqual(r.new_highs([1, 2, 3, 2, 4]), [False, False, True, False, True])
            nv = [False, False, True, True, False, False, False, False, False, False, False, False, False, False, True]
            sp = [False, True, False, False, False, False, False, False, False, False, False, False, False, False, True]
            self.assertEqual(r.events(nv, sp), [2, 14])      # day 3 is the same cluster; SPY's day-1 high counts for day 2

            from datetime import date as _d, timedelta as _td
            days = [(_d(2026, 1, 1) + _td(days=i)).isoformat() for i in range(40)]
            closes = {"SPY": [100 + i for i in range(10)] + [109 - i for i in range(30)],
                      "QQQ": [50.0] * 40, "NVDA": [10 + i for i in range(10)] + [19 - 0.1 * i for i in range(30)]}

            class C:
                def _d(self, path, params):
                    s = params["symbols"]
                    return {"bars": {s: [{"t": d + "T04:00:00Z", "c": c} for d, c in zip(days, closes[s])]}}
            rep = r.run(C())
            self.assertEqual([e["day"] for e in rep["events"]], ["2026-01-03"])   # first joint high after warm-up
            first = rep["events"][0]
            self.assertAlmostEqual(first["SPY_5"], 107 / 102 - 1)
            self.assertEqual(rep["now"]["SPY"]["high_day"], "2026-01-10")
            md = r.markdown(rep)
            self.assertIn("2026-01-03", md)
        finally:
            r.WARMUP = old


class ResearchChase(unittest.TestCase):
    def test_events_and_groups(self):
        from hero import research_chase as r
        closes = [100, 100, 110, 112] + [112 + i for i in range(1, 22)]
        bars = [{"t": f"2026-01-{i + 1:02d}T05:00:00Z", "o": c - 1, "c": c} for i, c in enumerate(closes)]
        bars[3]["o"] = 111.0                                       # the morning after the jump
        rows = r.events(bars)
        jump = rows[0]                                             # day 3: 100 -> 110
        self.assertAlmostEqual(jump["ret"], 0.10)
        self.assertAlmostEqual(jump["gap"], 111 / 110 - 1)
        self.assertAlmostEqual(jump["close_next"], 112 / 110 - 1)
        self.assertAlmostEqual(jump["open_close"], 112 / 111 - 1)
        self.assertAlmostEqual(jump["open_5"], closes[7] / 111 - 1)
        g = r.groups(rows)
        self.assertEqual(g["当天涨 10% 以上"]["n"], 1)
        self.assertEqual(g["当天涨 10% 以上、第二天高开 3% 以上"]["n"], 0)
        self.assertIn("大涨后第二天", r.markdown({"generated": "x", "start": "a", "end": "b", "names": 1,
                                               "all": g, "since2024": g, "recent_big": []}))


class ResearchJumpDip(unittest.TestCase):
    def test_entries_candidates_and_outcome(self):
        from hero import research_jump_dip as r
        b = lambda o, h, c: {"o": o, "h": h, "c": c}
        m = {"09:30": b(95, 96, 95.5), "09:45": b(95.5, 97, 96.5), "10:00": b(96.5, 96.8, 96.0),
             "10:20": b(96, 97.5, 97.2), "11:00": b(97.2, 100.5, 100.2), "15:59": b(100, 101, 101)}
        got = r.entries(m, 100.0)
        self.assertEqual(got["open"], ("09:30", 95.0))
        self.assertEqual(got["10:00"], ("10:00", 96.0))
        self.assertEqual(got["green"], ("10:00", 96.0))                 # above the 95 open from 10:00 on
        self.assertEqual(got["orb"], ("10:20", 97.2))                   # above the 97 high of 9:30-10:00
        self.assertEqual(got["fill"], ("11:00", 100.2))                 # back at yesterday's 100
        self.assertEqual(r.entries({"10:00": b(1, 1, 1)}, 1.0), {})

        day = lambda i, o, c: {"t": f"2026-01-{i:02d}T05:00:00Z", "o": o, "c": c}
        adj = [day(1, 90, 90), day(2, 91, 100), day(3, 95, 101)] + [day(4 + k, 101, 101 + k) for k in range(6)]
        evs = r.candidates(adj, adj)
        self.assertEqual(evs[0]["day"], "2026-01-03")
        self.assertAlmostEqual(evs[0]["ret"], 100 / 90 - 1)
        self.assertAlmostEqual(evs[0]["gap"], 95 / 100 - 1)
        out = r.outcome(evs[0], {"open": ("09:30", 95.0)})
        self.assertAlmostEqual(out["open"]["day"], 101 / 95 - 1)
        self.assertAlmostEqual(out["open"]["five"], evs[0]["close5_adj"] / 95 - 1)
        split = [dict(x) for x in adj]
        raw = [dict(x) for x in adj]
        raw[2] = {**raw[2], "c": 50.5}                                   # a 2:1 split between day 2 and day 3
        self.assertNotIn("2026-01-03", [e["day"] for e in r.candidates(split, raw)])


class ResearchJumpPut(unittest.TestCase):
    def test_pick_and_play(self):
        from hero import research_jump_put as r
        cs = [{"symbol": f"X{e}P{k}", "strike_price": str(k), "expiration_date": e, "type": "put"}
              for e in ("2026-10-09", "2026-10-16", "2026-10-23") for k in (95, 97, 100, 102)]
        self.assertEqual(r.pick_put(cs, 100.5, 0.0, "2026-10-08"), "X2026-10-16P100")    # a week out, at or under the price
        self.assertEqual(r.pick_put(cs, 100.5, 0.03, "2026-10-08"), "X2026-10-16P97")
        later = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
        daily = {"2026-10-09": {"h": 1.5, "c": 1.4}, "2026-10-12": {"h": 2.4, "c": 2.0}, "2026-10-15": {"h": 1.0, "c": 0.5}}
        got = r.play(1.0, daily, later)                                     # cost 1.10
        self.assertAlmostEqual(got["next"], 1.26 / 1.10 - 1)
        self.assertAlmostEqual(got["five"], 0.45 / 1.10 - 1)
        self.assertEqual(got["take2"], 1.0)                                  # 2.4 >= 2.2
        self.assertIsNone(r.play(1.0, daily, []))
        self.assertIsNone(r.play(1.0, {}, later))                            # never traded again: left out


class ResearchBarbell(unittest.TestCase):
    def test_a_ticket_risks_its_share_and_the_rest_follows_the_core(self):
        from hero import research_barbell as r
        days = ["d0", "d1", "d2"]
        growth = [1.0, 1.10, 1.0]                       # the core +10% on d1
        p = r.path(days, growth, {"d1": 2.0}, 0.10, 1000.0, 0, 3)
        self.assertAlmostEqual(p[1], 900 * 1.10 + 100 * 3.0)   # 10% in a ticket that tripled
        self.assertAlmostEqual(p[2], p[1])
        self.assertEqual(r.path(days, growth, {"d1": -1.0}, 0.0, 1000.0, 0, 3), [1000.0, 1100.0, 1100.0])
        self.assertAlmostEqual(r.max_dd([1, 2, 1, 3]), -0.5)


class ResearchMovers(unittest.TestCase):
    def test_last_session_change_and_volume(self):
        from hero import research_movers as r
        b = lambda d, c, v=100: {"t": f"2026-10-0{d}T04:00:00Z", "o": c, "h": c * 1.02, "l": c * 0.98, "c": c, "v": v}
        day, rows = r.rows_from({"AAA": [b(6, 100), b(7, 100), b(8, 110, 300)], "OLD": [b(6, 50), b(7, 51)]})
        self.assertEqual((day, [x["symbol"] for x in rows]), ("2026-10-08", ["AAA"]))   # OLD has no bar that day
        self.assertAlmostEqual(rows[0]["chg"], 0.10)
        self.assertAlmostEqual(rows[0]["vol_x"], 3.0)
        self.assertIn("涨得最多", r.markdown({"day": day, "names": 1, "rows": rows}))


class ResearchChipNext(unittest.TestCase):
    def test_groups_and_summary(self):
        from hero import research_chipnext as r
        c = [100, 99, 96, 95, 97, 98, 99, 100, 101]
        rows = r.events(c, [f"d{i}" for i in range(len(c))])
        two = r.pick(rows, "连跌两天、第二天跌 2.5% 以上")
        self.assertEqual([x["day"] for x in two], ["d2"])               # 99 -> 96 after 100 -> 99
        s = r.summary(two)
        self.assertEqual((s["n"], s["down"]), (1, 1.0))                  # 96 -> 95 the next day
        self.assertAlmostEqual(s["five"], 100 / 96 - 1, places=4)       # 5 sessions later: 100
        self.assertEqual(r.summary([]), {"n": 0})


class ResearchChipDipCall(unittest.TestCase):
    def test_pick_play_and_entries(self):
        from hero import research_chip_dipcall as r
        cs = [{"symbol": f"X{e}C{k}", "strike_price": str(k), "expiration_date": e, "type": "call"}
              for e in ("2026-10-09", "2026-10-16") for k in (98, 100, 102, 104)]
        self.assertEqual(r.pick_call(cs, 99.5, 0.0, "2026-10-09")["symbol"], "X2026-10-09C100")
        self.assertEqual(r.pick_call(cs, 99.5, 0.02, "2026-10-09")["symbol"], "X2026-10-09C102")
        b = lambda h, c: {"h": h, "c": c}
        mins = {"10:00": b(1.0, 1.0), "12:00": b(1.5, 1.4), "15:50": b(1.3, 1.2)}
        got = r.play(mins, {"h": 2.4, "c": 0.5}, False)               # cost 1.10
        self.assertAlmostEqual(got["day"], 1.08 / 1.10 - 1)
        self.assertAlmostEqual(got["next"], 0.45 / 1.10 - 1)
        self.assertEqual(got["take2"], 1.0)                            # 2.4 >= 2.2 the next day
        same = r.play(mins, {"h": 9, "c": 9}, True)                     # expires that day: the next day never comes
        self.assertEqual((same["next"], same["take2"]), (same["day"], same["day"]))
        adj = [{"t": f"2026-10-0{i}T04:00:00Z", "c": c} for i, c in enumerate([100, 99, 96, 97, 98], start=1)]
        e = r.entries(adj)
        self.assertEqual((e[0]["day"], e[0]["next_day"]), ("2026-10-04", "2026-10-05"))
        self.assertAlmostEqual(e[0]["drop"], 96 / 99 - 1)


class Notify(unittest.TestCase):
    def capture(self):
        from hero import notify
        sent, ok = [], [True]
        orig = notify.post
        notify.post = lambda url, payload: (sent.append(payload), ok[0])[1]
        self.addCleanup(setattr, notify, "post", orig)
        return notify, sent, ok

    def journal(self, d, name="journal-s500", positions=()):
        root = Path(d) / name
        root.mkdir()
        (root / "snapshot.json").write_text(json.dumps({"account": {"equity": "49908.67", "last_equity": "50000", "cash": "49908.67"},
                                                        "positions": list(positions)}))
        return root

    @staticmethod
    def ev(ts, **k):
        return json.dumps({"ts": ts, **k}, ensure_ascii=False)

    def test_cards_for_new_events_only_and_the_cursor_survives_a_failure(self):
        notify, sent, ok = self.capture()
        with tempfile.TemporaryDirectory() as d:
            root = self.journal(d)
            log = root / "trades.jsonl"
            log.write_text(self.ev("2026-10-08T14:00:42+00:00", kind="zdte_fill", symbol="SPY261008C00781000",
                                   why="末日买单成交：13 张 × $0.07") + "\n")
            noon = datetime(2026, 10, 8, 12, 0, tzinfo=notify.ET)
            self.assertEqual(notify.push_journal(root, None, noon), 0)          # no webhook: nothing at all
            self.assertFalse((root / "notify_cursor.json").exists())
            self.assertEqual(notify.push_journal(root, "u", noon), 0)           # first run: only sets the cursor
            self.assertEqual(sent, [])
            with log.open("a") as f:
                f.write(self.ev("2026-10-08T14:10:26+00:00", kind="order", reason="protective_stop", symbol="AAPL", why="止损") + "\n")
                f.write(self.ev("2026-10-08T14:20:00+00:00", kind="sleeve_plan", dry_run=True, why="演算") + "\n")
                f.write(self.ev("2026-10-08T19:30:35+00:00", kind="close", symbol="SPY261008C00781000",
                                why="末日期权 15:30 前平仓，不留到收盘") + "\n")
                f.write(self.ev("2026-10-08T19:30:36+00:00", kind="alert_error", source="close",
                                error='DELETE https://x/v2/positions/SPY261008C00781000 -> 403: {"code":1,"message":"order has been rejected due to no available quote for symbol"}') + "\n")
            ok[0] = False
            self.assertEqual(notify.push_journal(root, "u", noon), 0)           # failed: cursor stays, retried later
            ok[0] = True
            sent.clear()
            self.assertEqual(notify.push_journal(root, "u", noon), 2)
            embeds = sent[-1]["embeds"]
            self.assertEqual(embeds[0]["title"], "多账本 $50k")
            self.assertIn("净值 **$49,909**", embeds[0]["description"])
            card = embeds[1]
            self.assertEqual((card["title"], card["color"]), ("SPY 10/08 781C", notify.RED))
            self.assertIn("💰 `15:30` 末日期权 15:30 前平仓", card["description"])
            self.assertIn("券商拒绝：合约没有报价", card["description"])
            self.assertNotIn("https://", card["description"])
            self.assertEqual(card["footer"]["text"], "已不在持仓里")
            self.assertEqual(len(embeds), 2)                                     # resting stops and plans stay quiet
            self.assertEqual(notify.push_journal(root, "u", noon), 0)           # nothing new

    def test_a_card_per_holding_once_after_the_close(self):
        notify, sent, _ = self.capture()
        pos = [{"symbol": "AMD", "qty": "15", "avg_entry_price": "636.06", "current_price": "621.5", "market_value": "9322.5",
                "unrealized_pl": "-218.4", "unrealized_plpc": "-0.0229", "unrealized_intraday_pl": "-218.4"}]
        with tempfile.TemporaryDirectory() as d:
            root = self.journal(d, "journal", pos)
            (root / "trades.jsonl").write_text(self.ev("2026-10-08T13:31:00+00:00", kind="order", symbol="AMD", side="buy",
                                                       qty="15", why="动量排名第 2/8") + "\n")
            (root / "notify_cursor.json").write_text(json.dumps({"ts": "2026-10-08T13:31:00+00:00"}))
            late = datetime(2026, 10, 8, 16, 0, tzinfo=notify.ET)
            notify.push_journal(root, "u", late)
            embeds = sent[-1]["embeds"]
            self.assertEqual(embeds[0]["title"], "主账户 $100k（收盘持仓）")
            card = embeds[1]                                                    # all holdings in one card
            self.assertEqual((card["title"], card["color"]), ("收盘持仓 1 个 · 浮动 -218", notify.RED))
            self.assertEqual(card["description"], "🔴 **AMD** 15 股 · $636.06 → $621.50 · **-218**（-2%）")
            self.assertEqual(len(embeds), 2)                                    # no ledgers in this journal
            sent.clear()
            notify.push_journal(root, "u", late)
            self.assertEqual(sent, [])                                          # once a day

    def test_sleeve_lines_lead_with_the_ledger_and_the_result(self):
        from hero import notify
        res = notify.lines([{"ts": "2026-10-14T15:50:00+00:00", "kind": "sleeve_result", "sleeve": "spy0dte-1000",
                             "symbol": "SPY261014P00768000", "ret": 2.0,
                             "why": "spy0dte-1000：SPY261014P00768000 3 倍止盈（券商挂单成交），花 $98.00、收回 $294.00（+200%），账本 $610.00"}])
        self.assertEqual(res, ["🟢 `11:50` [**SPY 末日 $1k**](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/spy0dte.html) **+200%** · 3 倍止盈（券商挂单成交），花 $98.00、收回 $294.00，账本 $610.00"])
        fill = notify.lines([{"ts": "2026-10-14T14:00:00+00:00", "kind": "sleeve_fill", "sleeve": "tsla0dte-5x-500",
                              "symbol": "TSLA261014P00362500", "why": "tsla0dte-5x-500：TSLA261014P00362500 成交 1 张 × $0.85，花 $85.00；"
                                                                      "1 张 5 倍止盈 $4.25 挂在券商"}])
        self.assertEqual(fill, ["✅ `10:00` [**TSLA 末日·5 倍 $500**](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/tsla0dte-5x.html) 成交 1 张 × $0.85，花 $85.00；1 张 5 倍止盈 $4.25 已挂单"])
        lost = notify.lines([{"ts": "2026-10-14T13:30:00+00:00", "kind": "sleeve_result", "sleeve": "earn-500", "symbol": "X", "ret": -0.97,
                              "why": "earn-500：X 到时卖出，花 $60.00、收回 $2.00（-97%），账本 $343.00"}])
        self.assertTrue(lost[0].startswith("🔴 `09:30` [**财报彩票 $500**](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/earn.html) **-97%** · 到时卖出"))

    def test_a_held_position_opens_its_card_with_two_short_lines(self):
        from hero import notify
        pos = [{"symbol": "NVDA261014P00222500", "qty": "15", "avg_entry_price": "0.44", "current_price": "1.27",
                "unrealized_pl": "1245", "unrealized_plpc": "1.886", "unrealized_intraday_pl": "1245"}]
        with tempfile.TemporaryDirectory() as d:
            root = self.journal(d, positions=pos)
            card = notify.cards(root, [{"ts": "2026-10-14T14:00:00+00:00", "kind": "sleeve_fill", "sleeve": "nvda0dte-500",
                                        "symbol": "NVDA261014P00222500", "qty": 10, "price": 0.44,
                                        "why": "nvda0dte-500：NVDA261014P00222500 成交 10 张 × $0.44，花 $440.00"}], "多账本 $50k")[1]
        self.assertNotIn("fields", card)
        self.assertTrue(card["description"].startswith("持仓 15 张 · 成本 $0.44\n现价 $1.27 · 浮动 **+1,245**（+189%）\n\n✅"))

    def test_the_same_contract_across_ledgers_folds_into_one_line_each(self):
        from hero import notify
        sym = "TSLA261014P00362500"
        ev = lambda ts, **k: {"ts": f"2026-10-14T{ts}:00+00:00", "symbol": sym, **k}
        evs = [ev("14:00", kind="sleeve_fill", sleeve="tsla0dte-500", qty=1, price=0.85,
                  why=f"tsla0dte-500：{sym} 成交 1 张 × $0.85，花 $85.00；1 张 3 倍止盈 $2.55 挂在券商"),
               ev("14:00", kind="sleeve_fill", sleeve="tsla0dte-5x-1000", qty=3, price=0.85,
                  why=f"tsla0dte-5x-1000：{sym} 成交 3 张 × $0.85，花 $255.00；1 张 5 倍止盈 $4.25 挂在券商"),
               ev("19:30", kind="sleeve_exit", sleeve="tsla0dte-500", why=f"tsla0dte-500：{sym} 2026-10-14 15:30 到时卖出，卖 1"),
               ev("19:30", kind="sleeve_result", sleeve="tsla0dte-500", ret=-0.965,
                  why=f"tsla0dte-500：{sym} 2026-10-14 15:30 到时卖出，花 $85.00、收回 $3.00（-96%），账本 $250.00"),
               ev("19:31", kind="sleeve_result", sleeve="tsla0dte-5x-1000", ret=-0.965,
                  why=f"tsla0dte-5x-1000：{sym} 2026-10-14 15:30 到时卖出，花 $255.00、收回 $9.00（-96%），账本 $436.00")]
        self.assertEqual(notify.lines(evs), [
            "✅ `10:00` 成交 × $0.85：[TSLA 末日](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/tsla0dte.html) $500 1 张 · [TSLA 末日·5 倍](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/tsla0dte-5x.html) $1k 3 张（共 4 张，花 $340）；挂 3/5 倍止盈",
            "🔴 `15:30` **-96%** · 15:30 到时卖出：[TSLA 末日](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/tsla0dte.html) $500 · [TSLA 末日·5 倍](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/tsla0dte-5x.html) $1k（花 $340、收回 $12）"])

    def test_ledger_scoreboard_after_the_close(self):
        from hero import notify
        with tempfile.TemporaryDirectory() as d:
            root = self.journal(d)
            led = lambda cash, start, **k: {"cash": cash, "start": start, "round": 1, "open": [], "closed": [], **k}
            (root / "sleeves.json").write_text(json.dumps({"sleeves": {
                "nvda0dte-500": led(1300.0, 500, open=[{"paid": 134.0}],
                                    closed=[{"closed": "2026-10-14 11:00", "paid": 220.0, "proceeds": 660.0},
                                            {"closed": "2026-10-13 15:30", "paid": 100.0, "proceeds": 0.0}]),
                "relay-1000": led(1000.0, 1000, round=2),
                "net-500": led(500.0, 500), "net-1000": led(1000.0, 1000)}}))
            cards = notify.ledger_cards(root, "2026-10-14")
            self.assertEqual(cards[0]["title"], "账本榜 · 合计 $3,934 / 起点 $3,000 · 今天已实现 +440")
            rows = cards[0]["description"].split("\n")
            self.assertEqual(rows[0], "🏆 [**NVDA 末日 $500**](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/nvda0dte.html) $1,434（2.87 倍） · 今天 +440 · 持 1 笔")
            self.assertEqual(rows[1], "🟢 [**接力 $1k**](https://metrobasicsusa-wq.github.io/Zero-or-Hero/f/relay.html) $1,000（1.00 倍） · 第 2 轮")
            self.assertEqual(rows[2], "⚪ 还没出手：撒网（500/1000）")

    def test_order_lines_say_buy_or_sell_and_how_much(self):
        from hero import notify
        line = notify.lines([{"ts": "2026-10-08T13:30:40+00:00", "kind": "order", "symbol": "XLE", "side": "sell", "qty": "56",
                              "why": "目标仓位 10%"}])[0]
        self.assertEqual(line, "🛒 `09:30` **卖 56 股**：目标仓位 10%")
        self.assertEqual(notify.readable("SPY261008C00781000 / QQQ261008P00748500"), "SPY 10/08 781C / QQQ 10/08 748.5P")

    def test_replay_sends_one_day_and_the_holdings_without_moving_the_cursor(self):
        notify, sent, _ = self.capture()
        with tempfile.TemporaryDirectory() as d:
            root = self.journal(d, "journal")
            (root / "trades.jsonl").write_text("\n".join([
                self.ev("2026-10-08T13:30:40+00:00", kind="order", symbol="AMD", side="buy", qty="15", why="买入"),
                self.ev("2026-10-09T13:30:40+00:00", kind="order", symbol="NVDA", side="buy", qty="1", why="买入")]) + "\n")
            self.assertEqual(notify.replay(root, "u", "2026-10-08"), 1)
            titles = [e["title"] for e in sent[0]["embeds"]]
            self.assertEqual(titles, ["【回放 2026-10-08】主账户 $100k", "AMD"])
            self.assertFalse((root / "notify_cursor.json").exists())

    def test_note_goes_out_as_one_card(self):
        notify, sent, _ = self.capture()
        orig = os.environ.get("DC_WEBHOOK")
        os.environ["DC_WEBHOOK"] = "u"
        try:
            self.assertEqual(notify.main(["--note"]), 0)
        finally:
            if orig is None:
                os.environ.pop("DC_WEBHOOK", None)
            else:
                os.environ["DC_WEBHOOK"] = orig
        self.assertEqual(len(sent[0]["embeds"]), 1)
        self.assertTrue(sent[0]["embeds"][0]["title"] and sent[0]["embeds"][0]["description"])

    def test_messages_stay_under_discord_limits(self):
        from hero import notify
        many = [{"title": f"X{i}", "description": "y" * 900} for i in range(25)]
        msgs = notify.messages(many)
        self.assertTrue(all(len(m["embeds"]) <= 10 and sum(notify.size(c) for c in m["embeds"]) <= notify.CHARS_PER_MESSAGE
                            and notify.nbytes(m) <= notify.MESSAGE_BYTES for m in msgs))
        self.assertEqual(sum(len(m["embeds"]) for m in msgs), 25)
        wide = notify.messages([{"title": "中", "description": "\n".join(["中文" * 100] * 20)}])   # 4,000 chars, ~12 KB
        self.assertTrue(all(notify.nbytes(m) <= notify.MESSAGE_BYTES for m in wide))
        self.assertEqual(sum(len(m["embeds"]) for m in wide), 3)
        self.assertEqual(notify.direction("spy0dte-old-plan-1000"), "spy0dte")


class SniperScore(unittest.TestCase):
    def test_candidates_are_scored_by_the_day_in_their_direction_and_grouped_by_confidence(self):
        from hero import research_sniper_score as rs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "journal-s500" / "sniper").mkdir(parents=True)
            (root / "journal-s500" / "sniper" / "2026-10-13.json").write_text(json.dumps({"date": "2026-10-13", "sniper": None}))
            (root / "journal-s500" / "sniper" / "2026-10-15.json").write_text(json.dumps({"date": "2026-10-15",
                "sniper": {"symbol": "CRWV", "direction": "put", "confidence": 0.3},
                "candidates": [{"rank": 1, "symbol": "CRWV", "direction": "put", "confidence": 0.3},
                               {"rank": 2, "symbol": "MU", "direction": "put", "confidence": 0.2}]}))
            (root / "journal-s500" / "sleeves.json").write_text(json.dumps({"sleeves": {"sniper-500": {"closed": [
                {"opened": "2026-10-15 09:40", "und": "CRWV", "paid": 100.0, "proceeds": 250.0}]}}}))
            ps = rs.picks(root)
            self.assertEqual([(p["symbol"], p["traded"]) for p in ps], [("CRWV", True), ("MU", False)])
            rows = [rs.score(ps[0], {"o": 60, "h": 61, "l": 55.8, "c": 57}), rs.score(ps[1], {"o": 100, "h": 102, "l": 99, "c": 101})]
            self.assertEqual((rows[0]["close_move"], rows[0]["best_move"], rows[0]["hit"]), (0.05, 0.07, True))
            self.assertEqual((rows[1]["close_move"], rows[1]["hit"]), (-0.01, False))
            md = rs.report(rows, rs.booked(root))
            self.assertIn("| 0.3–0.4 | 1 | 100% | 100% | +5.0% | +7.0% |", md)
            self.assertIn("| 2026-10-15 | 1 | CRWV | 看跌 | 0.30 | +5.0% | +7.0% | +150% |", md)

    def test_skipped_names_are_scored_apart_from_the_candidates(self):
        from hero import research_sniper_score as rs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "journal-s500" / "sniper").mkdir(parents=True)
            (root / "journal-s500" / "sniper" / "2026-10-20.json").write_text(json.dumps({"date": "2026-10-20",
                "sniper": {"symbol": "CLF", "direction": "call", "confidence": 0.3},
                "candidates": [{"rank": 1, "symbol": "CLF", "direction": "call", "confidence": 0.3}],
                "skipped": [{"symbol": "AMZN", "direction": "call", "reason": "分析师评级", "why_not": "首选名单"},
                            {"symbol": "XYZ", "why_not": "no direction: not scored"}]}))
            ps = rs.picks(root)
            self.assertEqual([(p["symbol"], p["skipped"]) for p in ps], [("CLF", False), ("AMZN", True)])
            rows = [rs.score(ps[0], {"o": 14.2, "h": 14.58, "l": 13.32, "c": 13.55}),
                    rs.score(ps[1], {"o": 255.34, "h": 260.0, "l": 255.2, "c": 259.76})]
            md = rs.report(rows, {})
            self.assertIn("| 0.3–0.4 | 1 |", md)                                  # the skipped one stays out of the buckets
            self.assertIn("| 看过没选（全部） | 1 | 100% |", md)
            self.assertIn("| 　没选：分析师评级 | 1 |", md)
            self.assertIn("| 2026-10-20 | 没选（分析师评级） | AMZN | 看涨 | 0.00 |", md)


class ResearchIronFly(unittest.TestCase):
    def test_credit_reaction_buyback_and_expiry(self):
        from hero import research_ironfly as r
        legs = {"sc": "SC", "sp": "SP", "lc": "LC", "lp": "LP"}
        px = {"SC": {"e": 5.0, "r": 1.0}, "SP": {"e": 5.0, "r": 1.2}, "LC": {"e": 1.0, "r": 0.1}, "LP": {"e": 1.0, "r": 0.1}}
        out = r.ironfly(px, legs, 100.0, 10.0, "e", "r", 100.5, 103.0)
        credit = 2 * (5.0 - 0.25) - 2 * (1.0 + 0.05)              # 7.40
        self.assertAlmostEqual(out["credit"], round(credit, 3))
        self.assertAlmostEqual(out["max_loss"], round(10 - credit, 3))
        buyback = (1.0 + 0.05) + (1.2 + 0.06) - 2 * max(0.1 - 0.02, 0)
        self.assertAlmostEqual(out["ret_react"], round((credit - buyback) / (10 - credit), 3))
        self.assertAlmostEqual(out["ret_expiry"], round((credit - 3.0) / (10 - credit), 3))   # settled 3 from the body


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
                                  "direction": "trend", "take": 3, "fraction": 0.5, "exit_at": "15:30", "switch_at": 2.0,
                                  "buy_pad": 0.01, "fill_wait_s": 0, "chase_cap": 1.3}}
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}
        self.pos, self.oo = [], []
        self.fills = []   # per buy order, in order: "fill", "none", or a partial quantity

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, hh, mm, bid=0.06, ask=0.07, asks=None):
        from datetime import datetime
        test = self
        quotes = list(asks or [])

        class Broker(FakeClient):
            def option_snapshots(self, symbols):
                a = quotes.pop(0) if quotes else ask
                return {s: {"latestQuote": {"bp": bid, "ap": a}} for s in symbols}

            def submit_order(self, **o):
                super().submit_order(**o)
                oid = f"order-{len(self.orders)}"
                test.oo.append({"id": oid, "symbol": o["symbol"], "side": o["side"]})
                if o["side"] == "buy":
                    how = test.fills.pop(0) if test.fills else "none"
                    n = int(o["qty"]) if how == "fill" else (how if isinstance(how, int) else 0)
                    self.by_cid = {**getattr(self, "by_cid", {}), o["client_order_id"]: {
                        "id": oid, "status": "filled" if how == "fill" else ("partially_filled" if n else "new"),
                        "filled_qty": str(n), "filled_avg_price": o["limit_price"] if n else None}}
                    if n:
                        test.oo[:] = [x for x in test.oo if x["id"] != oid] if how == "fill" else test.oo
                        test.pos.append({"symbol": o["symbol"], "qty": str(n), "market_value": "0"})
                return {"id": oid}

            def cancel_order(self, oid):
                test.oo[:] = [o for o in test.oo if o["id"] != oid]
                for v in getattr(self, "by_cid", {}).values():
                    if v["id"] == oid and v["status"] != "filled":
                        v["status"] = "canceled"
                return super().cancel_order(oid)

        c = Broker(self.closes, positions=self.pos, open_orders=self.oo, equity=500, last_equity=500)
        c.pos, c.oo = self.pos, self.oo
        c.snaps = {"SPY": {"dailyBar": {"o": 770.0}, "latestTrade": {"p": 772.0}}}
        Engine(c, self.conf, Journal(self.root)).run(today=TODAY, now=datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm))
        book = json.loads((self.root / "zdte.json").read_text())
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()]
        return c, book, log

    def test_live_round_trip_with_cancel_then_sell(self):
        self.fills = ["fill"]
        c, book, _ = self.cycle(10, 5)
        self.assertEqual(book["mode"], "live")
        self.assertEqual(book["rehearsal"]["sim_cash"], 990.0)        # the rehearsal is kept, not traded on
        self.assertNotIn("sim_cash", book)
        buy = c.orders[0]
        self.assertEqual((buy["side"], buy["type"], buy["limit_price"], buy["qty"]), ("buy", "limit", "0.08", "31"))  # ask + 1c
        self.assertEqual((book["today"]["qty"], book["today"]["price"], book["today"]["take_price"]), (31, 0.08, 0.24))
        sym = buy["symbol"]
        c, book, _ = self.cycle(10, 15)
        take = c.orders[0]
        self.assertEqual((take["side"], take["limit_price"], take["qty"]), ("sell", "0.24", "31"))
        c, book, _ = self.cycle(15, 30, bid=0.03)                     # time exit: cancel the take, then sell
        self.assertEqual(c.cancelled, ["order-1"])
        self.assertEqual(c.closed, [sym])
        self.assertEqual(book["today"]["status"], "open")             # booked once the broker shows us flat
        self.pos[:] = []
        c, book, log = self.cycle(15, 40)
        self.assertEqual((book["today"]["status"], book["today"]["proceeds"]), ("closed", 93.0))  # 31 x $0.03
        self.assertEqual(c.closed, [])
        self.assertEqual([e for e in log if e["kind"] == "zdte_result"][-1]["dry_run"], False)

    def test_an_unsold_expired_ticket_is_booked_the_next_day(self):
        # 2026-10-08: the bid was $0 at 15:30, the market sell found no buyer and the call expired;
        # the next day's run must book the loss before a new ticket replaces it
        book = json.loads((self.root / "zdte.json").read_text())
        book.update(mode="live", history=[], today={
            "date": (TODAY - timedelta(days=1)).isoformat(), "status": "open", "symbol": "SPY261008C00781000",
            "qty": 13, "price": 0.07, "paid": 91.0, "filled": True, "take_price": 0.21, "take_order": True,
            "exit_sent": True, "exit_bid": 0.0})
        (self.root / "zdte.json").write_text(json.dumps(book))
        self.fills = ["fill"]
        c, book, log = self.cycle(10, 5)
        first = book["history"][0]
        self.assertEqual((first["status"], first["proceeds"], first["ret"]), ("closed", 0.0, -1.0))
        self.assertIn("末日补记", [e for e in log if e["kind"] == "zdte_result"][0]["why"])
        self.assertEqual(c.orders[0]["qty"], "25")                     # half of 500 - 91 = 204.50, at $0.08

    def test_unfilled_buy_is_requoted_once_at_the_new_ask(self):
        self.fills = ["none", "fill"]
        c, book, log = self.cycle(10, 5, asks=[0.07, 0.08])            # the ask moved up a cent
        self.assertEqual(c.cancelled, ["order-1"])
        self.assertEqual([(o["limit_price"], o["qty"]) for o in c.orders], [("0.08", "31"), ("0.09", "27")])
        self.assertEqual((book["today"]["status"], book["today"]["qty"], book["today"]["price"]), ("open", 27, 0.09))
        self.assertEqual([e["attempt"] for e in log if e["kind"] == "zdte_fill"], [2])

    def test_no_chase_above_the_cap_and_no_trade_that_day(self):
        self.fills = ["none"]
        c, book, log = self.cycle(10, 5, asks=[0.07, 0.10])            # 0.11 > 0.07 x 1.3
        self.assertEqual((len(c.orders), c.cancelled), (1, ["order-1"]))
        self.assertEqual(book["today"]["status"], "skipped")
        self.assertIn("超过上限", [e for e in log if e["kind"] == "zdte_skip"][-1]["why"])
        c, book, _ = self.cycle(15, 30)                                # nothing left to do at the exit
        self.assertEqual((c.orders, c.closed), ([], []))
        self.assertEqual(book["today"]["status"], "skipped")

    def test_two_misses_cancel_both_and_skip(self):
        self.fills = ["none", "none"]
        c, book, log = self.cycle(10, 5)
        self.assertEqual(c.cancelled, ["order-1", "order-2"])
        self.assertEqual(book["today"]["status"], "skipped")
        self.assertIn("两次挂单都没成交", [e for e in log if e["kind"] == "zdte_skip"][-1]["why"])

    def test_partial_fill_keeps_what_was_bought(self):
        self.fills = [10]
        c, book, _ = self.cycle(10, 5)
        self.assertEqual((len(c.orders), c.cancelled), (1, ["order-1"]))  # the rest is cancelled, no re-quote
        self.assertEqual((book["today"]["qty"], book["today"]["paid"]), (10, 80.0))
        c, _, _ = self.cycle(10, 15)
        self.assertEqual((c.orders[0]["side"], c.orders[0]["qty"]), ("sell", "10"))

    def test_sizes_on_its_own_ledger_not_a_bigger_account(self):
        # the paper account reset to $50,000: the book still bets half of its own $500, and does not
        # mistake the account for a 100x win and switch phase
        self.fills = ["fill"]
        orig = FakeClient.account
        FakeClient.account = lambda self: {"equity": "50000", "last_equity": "50000", "cash": "50000"}
        try:
            c, book, log = self.cycle(10, 5)
        finally:
            FakeClient.account = orig
        self.assertEqual(c.orders[0]["qty"], "31")                     # 250 / 8, not 25,000 / 8
        self.assertEqual(book["phase"], "zero_dte")
        self.assertNotIn("zdte_switch", [e["kind"] for e in log])

    def test_live_round_ends_below_the_line_and_restarts(self):
        book = json.loads((self.root / "zdte.json").read_text())
        book.update({"mode": "live", "live_from": "2026-10-07", "history": [
            {"date": "2026-10-07", "paid": 250.0, "proceeds": 0.0, "status": "closed"},
            {"date": "2026-10-08", "paid": 210.0, "proceeds": 0.0, "status": "closed"}]})   # $500 -> $40, below the $50 line
        book.pop("sim_cash", None)
        (self.root / "zdte.json").write_text(json.dumps(book))
        c, book, log = self.cycle(9, 45)                               # before the entry: bookkeeping only
        self.assertEqual(book["rounds"], 2)
        self.assertIn("第 2 轮", [e for e in log if e["kind"] == "zdte_attempt_end"][-1]["why"])
        from hero import zero_dte as zd
        b = zd.Book(self.root / "zdte.json", 500)
        self.assertEqual(b.live_cash(), 500.0)                         # the new round starts at $500

    def test_take_filled_at_the_broker(self):
        self.fills = ["fill"]
        self.cycle(10, 5)
        self.cycle(10, 15)
        self.oo[:], self.pos[:] = [], []                               # the take fills
        c, book, _ = self.cycle(11, 0)
        self.assertEqual((book["today"]["exit"], book["today"]["proceeds"]), ("止盈 3 倍（券商挂单成交）", 744.0))  # 31 x 0.24
        self.assertEqual(c.orders, [])                                 # no second trade the same day


class ZeroDTEShadow(unittest.TestCase):
    """Single-stock same-day rule on real quotes: logged, never ordered."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conf = {**cfg(options={"enabled": False}), "live_from": "2000-01-01", "launch_approved": True,
                     "zdte_shadow": {"enabled": True, "symbols": ["TSLA", "NVDA", "AAPL"], "offset": 0.006, "take": 3}}
        self.closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1)}

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, hh, mm, quote):
        from datetime import datetime

        class Broker(FakeClient):
            def option_contracts(self, und, **kw):
                if und == "AAPL":
                    return []                                          # no same-day expiry
                exp = kw["expiration_date"]
                t = "C" if kw["type"] == "call" else "P"
                return [{"symbol": f"{und}{exp}{t}{k}", "strike_price": str(k), "expiration_date": exp}
                        for k in (395, 400, 402.5, 405, 410)]

            def option_snapshots(self, symbols):
                return {s: {"latestQuote": quote(s)} for s in symbols}
        c = Broker(self.closes)
        c.snaps = {"TSLA": {"dailyBar": {"o": 398.0}, "latestTrade": {"p": 400.0}},   # up: a call
                   "NVDA": {"dailyBar": {"o": 404.0}, "latestTrade": {"p": 400.0}},   # down: a put
                   "AAPL": {"dailyBar": {"o": 200.0}, "latestTrade": {"p": 201.0}}}
        Engine(c, self.conf, Journal(self.root)).run(today=TODAY, now=datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm))
        book = json.loads((self.root / "zdte_shadow.json").read_text())["days"][TODAY.isoformat()]
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()]
        return c, book, log

    def test_logs_real_spreads_takes_and_time_exits_without_orders(self):
        c, book, log = self.cycle(10, 5, lambda s: {"bp": 0.40, "ap": 0.60})
        options = lambda c: [o for o in c.orders if o["symbol"] not in self.closes]  # the stock sleeve may trade
        self.assertEqual(options(c), [])
        self.assertEqual(book["AAPL"], {"status": "no_expiry"})
        self.assertEqual((book["TSLA"]["kind"], book["TSLA"]["strike"]), ("C", 402.5))   # first strike >= 402.4
        self.assertEqual((book["NVDA"]["kind"], book["NVDA"]["strike"]), ("P", 395.0))   # last strike <= 397.6
        self.assertEqual(book["TSLA"]["spread"], 0.4)                                    # 0.20 / 0.50 mid
        self.assertEqual(len([e for e in log if e["kind"] == "zdte_shadow_open"]), 2)
        c, book, log = self.cycle(11, 0, lambda s: {"bp": 1.85, "ap": 1.95} if "TSLA" in s else {"bp": 0.30, "ap": 0.35})
        self.assertEqual((book["TSLA"]["status"], book["TSLA"]["ret"]), ("closed", 2.0))  # bid 1.85 >= 3 x 0.60
        self.assertEqual(book["NVDA"]["status"], "open")
        c, book, log = self.cycle(15, 30, lambda s: {"bp": 0.10, "ap": 0.14})
        self.assertEqual((book["NVDA"]["exit"], book["NVDA"]["ret"]), ("time", round(0.10 / 0.60 - 1, 3)))
        self.assertEqual(options(c), [])
        self.assertTrue(all(e["dry_run"] for e in log if e["kind"].startswith("zdte_shadow")))
        from hero import zdte_shadow as zs
        summ = zs.Book(self.root / "zdte_shadow.json").summary()
        self.assertEqual((summ["TSLA"]["takes"], summ["NVDA"]["n"]), (1, 1))


class SleeveBroker:
    """A small broker for the sleeve tests: orders by client id, fills decided by `fill(order)`."""

    def __init__(self, quotes, fill=None, cash=50_000):
        self.quotes, self.cash = quotes, cash
        self.fill = fill or (lambda o: o["side"] == "buy")  # resting sells (takes) wait for the test
        self.orders, self.pos, self.cancelled = {}, {}, []
        self.snaps = {"SPY": {"dailyBar": {"o": 700.0, "l": 699.0}, "latestTrade": {"p": 702.0}, "prevDailyBar": {"c": 700.0}}}
        self.bars = {}
        self.untradable, self.whole_only = set(), set()   # assets Alpaca does not trade / will not split

    def asset(self, sym): return {"symbol": sym, "tradable": sym not in self.untradable, "fractionable": sym not in self.whole_only}
    def account(self): return {"cash": str(self.cash), "equity": str(self.cash), "last_equity": str(self.cash)}
    def positions(self): return [{"symbol": k, "qty": str(v)} for k, v in self.pos.items() if v]
    def open_orders(self): return [o for o in self.orders.values() if o["status"] == "new"]
    def stock_snapshots(self, syms): return {s: self.snaps[s] for s in syms if s in self.snaps}
    def option_snapshots(self, syms):
        return {s: {"latestQuote": {"bp": self.quotes(s)[0], "ap": self.quotes(s)[1]}} for s in syms}
    def option_contracts(self, und, **kw):
        exp, t = kw["expiration_date_gte"], kw.get("type", "call")
        return [{"symbol": f"{und}{exp}{t[0].upper()}{k}", "strike_price": str(k), "expiration_date": exp, "type": t}
                for k in range(690, 716)]
    def daily_bars(self, syms, start, adjustment="all"): return {s: self.bars.get(s, []) for s in syms}

    def submit_order(self, **o):
        oid = f"id{len(self.orders)}"
        rec = {**o, "id": oid, "status": "new", "filled_qty": "0", "filled_avg_price": None}
        self.orders[o["client_order_id"]] = rec
        if o["type"] == "market" or self.fill(rec):
            self._fill(rec, float(o.get("limit_price") or self.quotes(o["symbol"])[0 if o["side"] == "sell" else 1] or 1))
        return {"id": oid}

    def _fill(self, rec, px):
        qty = float(rec.get("qty") or (float(rec["notional"]) / px))
        rec.update(status="filled", filled_qty=str(qty), filled_avg_price=str(px))
        sign = 1 if rec["side"] == "buy" else -1
        self.pos[rec["symbol"]] = round(self.pos.get(rec["symbol"], 0) + sign * qty, 6)

    def order_by_client_id(self, c): return self.orders.get(c, {})
    def orders_since(self, after): return [dict(o) for o in reversed(list(self.orders.values()))]
    def cancel_order(self, oid):
        for r in self.orders.values():
            if r["id"] == oid and r["status"] == "new":
                r["status"] = "canceled"
                self.cancelled.append(r["client_order_id"])
        return "canceled"


class Sleeves(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.specs = [{"id": "spy0dte-500", "kind": "zdte", "start": 500, "und": "SPY", "offset": 0.006, "take": 3},
                      {"id": "spy0dte-1000", "kind": "zdte", "start": 1000, "und": "SPY", "offset": 0.006, "take": 3}]
        self.cfg = {"sleeves": {"enabled": True, "fill_wait_s": 0}}

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, broker, hh, mm, specs=None):
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        specs = specs or self.specs
        book = sleeves.Book(self.root / "sleeves.json", specs, {"spy0dte-500": 279.0})
        j = Journal(self.root)
        sleeves.Runner(broker, j, self.cfg, specs, book, sleeve_rules.RULES, datetime(2026, 10, 8, hh, mm), date(2026, 10, 8),
                       sleep=lambda s: None).run()
        book.save()
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()] if (self.root / "trades.jsonl").exists() else []
        return book, log

    def test_two_sleeves_buy_the_same_contract_on_their_own_money_and_take_profits(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, log = self.cycle(b, 10, 0)
        buys = [o for o in b.orders.values() if o["side"] == "buy"]
        self.assertEqual({o["symbol"] for o in buys}, {"SPY2026-10-08C707"})          # 702 x 1.006 = 706.2 -> 707
        self.assertEqual(sorted(int(o["qty"]) for o in buys), [12, 45])               # 139.5 / 11 and 500 / 11
        small = book.s("spy0dte-500")
        self.assertEqual((small["cash"], small["open"][0]["take"]), (round(279 - 12 * 11, 2), 0.33))  # 3 x $0.11
        takes = [o for o in b.orders.values() if o["side"] == "sell"]
        self.assertEqual(sorted(int(o["qty"]) for o in takes), [12, 45])
        for o in takes:                                                              # both takes fill
            b._fill(o, 0.33)
        book, log = self.cycle(b, 11, 0)
        self.assertEqual(book.s("spy0dte-500")["cash"], round(279 - 132 + 12 * 33, 2))
        self.assertEqual(book.s("spy0dte-1000")["cash"], round(1000 - 45 * 11 + 45 * 33, 2))
        self.assertEqual(b.pos["SPY2026-10-08C707"], 0)
        book, log = self.cycle(b, 12, 0)
        self.assertEqual(len([o for o in b.orders.values() if o["side"] == "buy"]), 2)  # one try a day

    def test_a_lost_ledger_is_rebuilt_from_the_broker_and_the_slot_is_not_bought_again(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, self.specs[:1])
        buy = next(o for o in b.orders.values() if o["side"] == "buy")
        self.assertTrue(buy["client_order_id"].startswith("sl-spy0dte-500-20261008-"))   # the slot is in the id
        (self.root / "sleeves.json").unlink()                                            # the cycle's commit was lost
        book, log = self.cycle(b, 10, 10, self.specs[:1])
        self.assertEqual(len([o for o in b.orders.values() if o["side"] == "buy"]), 1)   # not bought again
        s = book.s("spy0dte-500")
        t = s["open"][0]
        self.assertEqual((t["qty"], t["cost"], t["trail"], t["exit_at"]), (12, 0.11, True, "15:30"))
        self.assertEqual(s["cash"], round(279 - 12 * 11, 2))
        self.assertIn(next(o for o in b.orders.values() if o["side"] == "sell")["client_order_id"], b.cancelled)  # its take
        self.assertTrue(any(e["kind"] == "sleeve_recover" for e in log))
        self.assertTrue(any(e["kind"] == "sleeve_skip" and "不重复买" in e["why"] for e in log))
        book, log = self.cycle(b, 10, 20, self.specs[:1])                                # known now: nothing more
        self.assertEqual(len(book.s("spy0dte-500")["open"]), 1)

    def test_a_lost_ticket_already_sold_at_the_broker_is_booked_closed(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, self.specs[:1])
        b._fill(next(o for o in b.orders.values() if o["side"] == "sell"), 0.33)          # the take filled
        (self.root / "sleeves.json").unlink()
        book, log = self.cycle(b, 11, 0, self.specs[:1])
        s = book.s("spy0dte-500")
        self.assertEqual(s["open"], [])
        self.assertEqual((s["closed"][-1]["exit_price"], s["closed"][-1]["ret"]), (0.33, 2.0))
        self.assertEqual(s["cash"], round(279 - 132 + 12 * 33, 2))

    def test_orders_from_before_slot_keyed_ids_are_never_recovered(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        b.submit_order(symbol="SPY2026-10-08C707", qty="3", side="buy", type="limit", limit_price="0.11", time_in_force="day",
                       client_order_id="sl-spy0dte-500-0123456789-b1")                     # an old random ticket id
        book, log = self.cycle(b, 9, 0, self.specs[:1])
        self.assertEqual(book.s("spy0dte-500")["open"], [])
        self.assertFalse(any(e["kind"] == "sleeve_recover" for e in log))

    def test_the_runner_half_shares_the_buy_ticket_id(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, _ = self.cycle(b, 10, 0, [{**self.specs[1], "half_take": True}])
        tids = sorted(t["tid"] for t in book.s("spy0dte-1000")["open"])
        self.assertEqual(len(tids), 2)
        self.assertEqual(tids[1], tids[0] + "r")

    def test_stop_entries_keeps_selling_open_tickets_but_buys_nothing_new(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, self.specs[:1])                                             # one open ticket
        stopped = [{**self.specs[0], "stop_entries": True}]
        b.quotes = lambda s: (0.02, 0.03)
        book, log = self.cycle(b, 15, 30, stopped)
        self.assertEqual(book.s("spy0dte-500")["open"], [])                              # still sold at its time exit
        fresh = SleeveBroker(lambda s: (0.08, 0.10))
        (self.root / "sleeves.json").unlink()
        book, log = self.cycle(fresh, 10, 0, stopped)
        self.assertEqual(fresh.orders, {})                                               # nothing new bought

    def test_unfilled_is_requoted_once_then_dropped(self):
        b = SleeveBroker(lambda s: (0.08, 0.10), fill=lambda o: False)
        book, log = self.cycle(b, 10, 0, self.specs[:1])
        self.assertEqual(len([o for o in b.orders.values() if o["side"] == "buy"]), 2)
        self.assertEqual(len(b.cancelled), 2)
        self.assertEqual(book.s("spy0dte-500")["open"], [])
        self.assertEqual(book.s("spy0dte-500")["cash"], 279.0)

    def test_time_exit_cancels_the_take_and_sells_only_its_own_contracts(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0)
        b.quotes = lambda s: (0.02, 0.03)
        book, log = self.cycle(b, 15, 30)
        sells = [o for o in b.orders.values() if o["side"] == "sell" and o["type"] == "market"]
        self.assertEqual(sorted(int(o["qty"]) for o in sells), [12, 45])
        self.assertEqual(len(b.cancelled), 2)                                        # both takes first
        self.assertEqual(book.s("spy0dte-500")["cash"], round(279 - 132 + 12 * 2, 2))
        self.assertTrue(all(not s["open"] for s in book.d["sleeves"].values()))

    def test_with_no_bid_the_sell_is_a_one_cent_limit_then_booked_at_zero_once_expired(self):
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, self.specs[:1])
        sym = next(iter(b.pos))
        for o in b.orders.values():
            if o["side"] == "sell":
                o["status"] = "expired"                                              # the day's take never filled
        b.quotes = lambda s: (0.0, 0.03)
        real = b.submit_order

        def no_quote(**o):
            if o["side"] == "sell" and o["type"] == "market":
                raise RuntimeError("403: order has been rejected due to no available quote for symbol")
            return real(**o)
        b.submit_order = no_quote
        book, log = self.cycle(b, 15, 30, self.specs[:1])
        book, log = self.cycle(b, 15, 40, self.specs[:1])
        sells = [o for o in b.orders.values() if o["side"] == "sell" and o["status"] == "new"]
        self.assertEqual([(o["type"], o["limit_price"], o["qty"]) for o in sells], [("limit", "0.01", "12")])  # once, not again
        self.assertEqual([e for e in log if e["kind"] == "order_error"], [])
        self.assertIn("挂 $0.01 限价卖", [e for e in log if e["kind"] == "sleeve_exit"][-1]["why"])
        b.pos[sym] = 0                                                               # expired worthless overnight
        sells[0]["status"] = "expired"
        book = sleeves.Book(self.root / "sleeves.json", self.specs[:1])
        sleeves.Runner(b, Journal(self.root), self.cfg, self.specs[:1], book, sleeve_rules.RULES, datetime(2026, 10, 9, 9, 30),
                       date(2026, 10, 9), sleep=lambda s: None).run()
        s = book.s("spy0dte-500")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"], s["closed"][-1]["how"]), ([], 0.0, "券商已无持仓（到期作废）"))
        self.assertEqual(s["cash"], round(279 - 12 * 11, 2))

    def test_a_one_cent_sell_that_fills_is_booked_at_a_cent(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, self.specs[:1])
        for o in b.orders.values():
            if o["side"] == "sell":
                o["status"] = "expired"
        b.quotes = lambda s: (0.0, 0.03)
        b.fill = lambda o: o["side"] == "buy" or o.get("limit_price") == "0.01"     # someone takes the cent
        book, log = self.cycle(b, 15, 30, self.specs[:1])
        s = book.s("spy0dte-500")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"]), ([], 12.0))       # 12 contracts x $0.01 x 100

    def test_half_take_splits_into_a_take_ticket_and_a_trailing_runner(self):
        spec = [{**self.specs[0], "half_take": True}]
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, log = self.cycle(b, 10, 0, spec)
        s = book.s("spy0dte-500")
        self.assertEqual(sorted((t["qty"], bool(t.get("take_cid")), t["trail"]) for t in s["open"]), [(6, False, True), (6, True, False)])
        self.assertEqual(round(sum(t["paid"] for t in s["open"]), 2), 132.0)
        self.assertEqual([int(o["qty"]) for o in b.orders.values() if o["side"] == "sell"], [6])   # the take covers its half only
        self.assertIn("另 6 张翻倍后回撤 40% 卖", [e for e in log if e["kind"] == "sleeve_fill"][-1]["why"])
        b.quotes = lambda s: (0.30, 0.31)                                            # the runner's best bid: 0.30 > 2 x 0.11
        book, log = self.cycle(b, 11, 0, spec)
        b.quotes = lambda s: (0.15, 0.16)                                            # 50% off the best: the runner goes
        book, log = self.cycle(b, 11, 10, spec)
        s = book.s("spy0dte-500")
        self.assertEqual([(t["qty"], t["trail"]) for t in s["open"]], [(6, False)])  # the take half still rests
        self.assertEqual(s["closed"][-1]["proceeds"], 90.0)                           # 6 x $0.15 x 100

    def test_one_shot_goes_all_in_without_a_take_and_holds_to_1555(self):
        spec = [{"id": "oneshot-500", "kind": "oneshot", "start": 500, "und": "SPY", "offset": 0.01, "fraction": 1.0}]
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, log = self.cycle(b, 10, 0, spec)
        s = book.s("oneshot-500")
        self.assertEqual([(t["qty"], bool(t.get("take_cid")), t["expiry_out"]) for t in s["open"]], [(45, False, "15:55")])
        self.assertTrue(s["open"][0]["sym"].endswith("C710"))                         # 702 x 1.01 = 709.0 -> 710
        self.assertIn("全仓、不止盈，拿到 15:55", s["open"][0]["why"])
        book, _ = self.cycle(b, 15, 30, spec)
        self.assertEqual(len(book.s("oneshot-500")["open"]), 1)                      # still held at 15:30
        book, _ = self.cycle(b, 15, 55, spec)
        self.assertEqual(book.s("oneshot-500")["open"], [])

    def test_a_round_ends_below_ten_percent_and_restarts(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, _ = self.cycle(b, 10, 0, self.specs[:1])
        book.s("spy0dte-500")["cash"] = 20.0
        b.quotes = lambda s: (0.01, 0.02)                                            # sold 12 at $0.01: $32 < $50
        book.save()
        book, log = self.cycle(b, 15, 30, self.specs[:1])
        s = book.s("spy0dte-500")
        self.assertEqual((s["round"], s["zeros"], s["cash"]), (2, 1, 500.0))
        self.assertIn("归零", [e for e in log if e["kind"] == "sleeve_round"][-1]["why"])

    def test_cannot_afford_one_contract_skips(self):
        # a contract dearer than a whole fresh ledger: no restart would help (10-23), so it is skipped as before
        b = SleeveBroker(lambda s: (5.00, 6.00))                                      # $600 a contract on a $500 ledger
        book, log = self.cycle(b, 10, 0, self.specs[:1])
        self.assertEqual([o for o in b.orders.values()], [])
        self.assertEqual([e for e in log if e["kind"] == "sleeve_round"], [])
        self.assertIn("超过这笔可用", [e for e in log if e["kind"] == "sleeve_skip"][-1]["why"])
        from hero import research_plan_review as r                                   # owner, 10-19: follow it anyway
        skip = [e for e in log if e["kind"] == "sleeve_skip"]
        got = r.picks(skip, skip[-1]["ts"][:10])
        self.assertEqual((got[0]["qty"], got[0]["limit"], got[0]["shadow"]), (1, 6.0, "买不起一张"))

    def test_half_cannot_buy_one_but_the_ledger_can_goes_all_in_for_one(self):
        # owner, 10-22 (「不要把自己越改越保守了」): QQQ 0DTE sat out from 10-14 on -- half its ledger was below one contract
        b = SleeveBroker(lambda s: (1.50, 2.00))                                       # $201 a contract; half of $279 is $139.50
        book, log = self.cycle(b, 10, 0, self.specs[:1])
        buys = [o for o in b.orders.values() if o["side"] == "buy"]
        self.assertEqual([int(o["qty"]) for o in buys], [1])
        self.assertIn("押上账本买 1 张", [e for e in log if e["kind"] == "sleeve_buy"][-1]["why"])
        self.assertLess(book.s("spy0dte-500")["cash"], 279 - 139.5)                    # more than half went in
        from hero import sleeves
        self.assertEqual(sleeves.units(139.5, 279.0, 201.0), (1, True))
        self.assertEqual(sleeves.units(139.5, 200.0, 201.0), (0, False))               # not even the whole ledger: no trade
        self.assertEqual(sleeves.units(500.0, 1000.0, 201.0), (2, False))              # the half buys two: unchanged

    def test_a_flat_ledger_that_cannot_buy_one_contract_is_a_zero_and_restarts_at_once(self):
        # owner, 10-23 (「改」): QQQ 0DTE sat at $127 with a $175 contract -- above the zero line, below one contract, no samples
        from hero import sleeves
        b = SleeveBroker(lambda s: (2.50, 3.00))                                       # $301 a contract; the ledger has $279
        book, log = self.cycle(b, 10, 0, self.specs[:1])
        rounds = [e for e in log if e["kind"] == "sleeve_round"]
        self.assertEqual(len(rounds), 1)
        self.assertIn("连一张 $300.00 都买不起，算归零，从 $500 重开第 2 轮", rounds[0]["why"])   # $3.01 ticks to $3.00
        self.assertFalse(rounds[0]["dry_run"])
        buys = [o for o in b.orders.values() if o["side"] == "buy"]
        self.assertEqual([int(o["qty"]) for o in buys], [1])                          # half of the fresh $500 cannot: all in
        s = book.s("spy0dte-500")
        self.assertEqual((s["round"], s["zeros"], s["cash"], book.d["topups"]), (2, 1, 200.0, 221.0))
        self.assertIn("押上账本买 1 张", [e for e in log if e["kind"] == "sleeve_buy"][-1]["why"])
        # not flat, affordable, or dearer than a whole fresh ledger: no restart
        other = sleeves.Book(self.root / "b.json", self.specs, {"spy0dte-500": 279.0})
        self.assertIsNone(other.dead_zone_restart("spy0dte-500", 250.0))
        self.assertIsNone(other.dead_zone_restart("spy0dte-500", 501.0))
        other.s("spy0dte-500")["open"].append({"tid": "x", "paid": 10.0})
        self.assertIsNone(other.dead_zone_restart("spy0dte-500", 300.0))

    def test_every_round_is_kept_with_its_peak_and_length(self):
        from hero import sleeves
        book = sleeves.Book(self.root / "r.json", self.specs, {"spy0dte-500": 30.0})
        s = book.s("spy0dte-500")
        s["best"], s["closed"] = 900.0, [{"tid": "a"}, {"tid": "b"}, {"tid": "c"}]
        self.assertIn("归零", book.round_check("spy0dte-500", {"zero_line": 0.1, "target": 10000}, "2026-10-23"))
        self.assertEqual(s["rounds"], [{"round": 1, "end": "zero", "cash": 30.0, "peak": 900.0, "trades": 3, "day": "2026-10-23"}])
        self.assertEqual((s["round"], s["round_from"], s["cash"]), (2, 3, 500.0))
        s["cash"], s["best"] = 120.0, 500.0
        s["closed"].append({"tid": "d"})
        self.assertIn("算归零", book.dead_zone_restart("spy0dte-500", 300.0, "2026-10-26"))
        self.assertEqual(s["rounds"][-1], {"round": 2, "end": "zero", "cash": 120.0, "peak": 500.0, "trades": 1, "day": "2026-10-26"})
        s["cash"] = 10400.0
        self.assertIn("成功", book.round_check("spy0dte-500", {"zero_line": 0.1, "target": 10000}, "2026-10-27"))
        self.assertEqual((s["rounds"][-1]["end"], s["rounds"][-1]["peak"], s["heroes"]), ("hero", 10400.0, 1))

    def test_a_plan_ledger_in_the_dead_zone_restarts_the_same_way(self):
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        b = SleeveBroker(lambda s: (2.50, 3.00))
        specs = [{**self.specs[0], "id": "x-500"}]
        book = sleeves.Book(self.root / "plan.json", specs, {"x-500": 279.0})
        sleeves.Runner(b, Journal(self.root), self.cfg, specs, book, sleeve_rules.RULES, datetime(2026, 10, 8, 10, 0),
                       date(2026, 10, 8), sleep=lambda s: None).plan()
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()]
        rounds = [e for e in log if e["kind"] == "sleeve_round"]
        self.assertEqual(len(rounds), 1)
        self.assertTrue(rounds[0]["dry_run"] and rounds[0]["why"].endswith("重开第 2 轮（演算）"))
        s = book.s("x-500")
        self.assertEqual((s["round"], s["zeros"], s["open"][0]["qty"], s["cash"]), (2, 1, 1, 200.0))   # booked at the $3.00 ask
        self.assertEqual(b.orders, {})

    def test_plan_scales_a_share_pick(self):
        # a 3x inverse fund takes a third of the notional (hero.sleeve_rules.Peer)
        from datetime import datetime
        from hero import sleeves
        b = SleeveBroker(lambda s: (0.08, 0.10))
        b.snaps = {"SOXS": {"dailyBar": {"o": 50.0}, "latestTrade": {"p": 50.0}, "prevDailyBar": {"c": 45.0}}}
        rule = type("R", (), {"entries": lambda self, run, spec, s: ("2026-10-08", [
            {"sym": "SOXS", "asset": "stock", "und": "SMH", "ask": 50.0, "whole": False, "scale": 1 / 3, "exit_at": "15:50", "why": "x"}])})()
        spec = {"id": "p-900", "kind": "peer", "start": 900, "fraction": 1.0}
        book = sleeves.Book(self.root / "plan.json", [spec])
        sleeves.Runner(b, Journal(self.root), self.cfg, [spec], book, {"peer": rule}, datetime(2026, 10, 8, 9, 40),
                       date(2026, 10, 8), sleep=lambda s: None).plan()
        t = book.s("p-900")["open"][0]
        self.assertEqual((t["sym"], t["qty"], t["paid"]), ("SOXS", 6.0, 300.0))        # 900 / 3 = $300 at $50

    def test_plan_goes_all_in_the_same_way(self):
        b = SleeveBroker(lambda s: (2.00, 3.00))                                       # $301 a contract; half of $500 is $250
        book, log = self.plan_cycle(b, 10, 0, [{**self.specs[0], "id": "x-500"}])
        s = book.s("x-500")
        self.assertEqual(s["open"][0]["qty"], 1)
        self.assertIn("押上账本", [e for e in log if e["kind"] == "sleeve_plan"][-1]["why"])
        self.assertEqual(b.orders, {})

    def test_plan_only_logs_what_it_would_buy_and_sends_nothing(self):
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book = sleeves.Book(self.root / "plan.json", self.specs)
        sleeves.Runner(b, Journal(self.root), self.cfg, self.specs, book, sleeve_rules.RULES, datetime(2026, 10, 8, 10, 0),
                       date(2026, 10, 8), sleep=lambda s: None).plan()
        self.assertEqual(b.orders, {})
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()]
        self.assertEqual([e["kind"] for e in log], ["sleeve_plan", "sleeve_plan_fill", "sleeve_plan", "sleeve_plan_fill"])
        self.assertIn("45 张 × $0.11", log[-2]["why"])
        self.assertTrue(all(e["dry_run"] for e in log))

    def plan_cycle(self, broker, hh, mm, specs, day=None):
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        day = day or date(2026, 10, 8)
        book = sleeves.Book(self.root / "plan.json", specs)
        sleeves.Runner(broker, Journal(self.root), self.cfg, specs, book, sleeve_rules.RULES,
                       datetime(day.year, day.month, day.day, hh, mm), day, sleep=lambda s: None).plan()
        book.save()
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()] if (self.root / "trades.jsonl").exists() else []
        return book, log

    def test_plan_books_the_pick_at_the_ask_and_takes_profit_once_the_bid_gets_there(self):
        # owner, 10-21: a stopped rule must still show what it would have made
        b = SleeveBroker(lambda s: (0.08, 0.10))
        spec = [self.specs[1]]                                                          # $1,000, 0.6% out, 3x take
        book, log = self.plan_cycle(b, 10, 0, spec)
        s = book.s("spy0dte-1000")
        t = s["open"][0]
        self.assertEqual((t["qty"], t["cost"], t["paid"], t["take"], t["sim"]), (45, 0.10, 450.0, 0.30, True))  # sized at the limit, booked at the ask
        self.assertEqual(s["cash"], 550.0)
        self.assertEqual(b.orders, {})
        self.assertIn("按卖价 $0.10 记作买进 45 张", [e for e in log if e["kind"] == "sleeve_plan_fill"][-1]["why"])
        b.quotes = lambda sym: (0.30, 0.32)                                              # the bid reaches the take
        book, log = self.plan_cycle(b, 10, 10, spec)
        s = book.s("spy0dte-1000")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"], s["closed"][-1]["ret"], s["cash"]), ([], 1350.0, 2.0, 1900.0))
        res = [e for e in log if e["kind"] == "sleeve_plan_result"]
        self.assertEqual(len(res), 1)
        self.assertIn("3 倍止盈", res[0]["why"])
        self.assertTrue(res[0]["dry_run"])
        self.assertEqual(b.orders, {})                                                 # never an order
        self.assertEqual(len([e for e in log if e["kind"] == "sleeve_plan_fill"]), 1)  # one try a day

    def test_plan_lottery_sample_books_one_contract_a_find_and_alerts_once(self):
        # owner, 10-21「先大胆点 取样本」: every find a fixed 1 contract; the alert goes to the trade feed
        b = SleeveBroker(lambda s: (0.08, 0.10))
        b.snaps["SPY"] = {"dailyBar": {"o": 700.0, "l": 699.0}, "latestTrade": {"p": 702.0}, "prevDailyBar": {"c": 660.0}}  # +6%, above open
        b.most_actives = lambda top: []
        spec = [{"id": "fadelotto-plan", "kind": "fadelotto", "start": 10000, "gap": 0.03, "max_ask": 1.0, "scan_from": "10:00",
                 "scan_until": "15:00", "watch": ["SPY"], "every": True, "qty": 1, "alert": True, "alert_gap": 0.05,
                 "alert_max_ask": 0.5, "zero_line": 0, "target": 1000000000, "plan_only": True}]
        book, log = self.plan_cycle(b, 10, 30, spec)
        s = book.s("fadelotto-plan")
        self.assertEqual([(t["qty"], t["cost"], t["sym"][-4:], t.get("take")) for t in s["open"]], [(1, 0.10, "P700", 0.3)])
        self.assertEqual(s["cash"], 9990.0)
        kinds = [e["kind"] for e in log]
        self.assertEqual(kinds.count("lotto_alert"), 1)
        self.assertFalse([e for e in log if e["kind"] == "lotto_alert"][0]["dry_run"])   # pushed to Discord
        self.assertNotIn("alert_error", kinds)
        self.assertEqual(b.orders, {})
        book, log = self.plan_cycle(b, 10, 40, spec)
        self.assertEqual(len(book.s("fadelotto-plan")["open"]), 1)                       # SPY already taken today
        self.assertEqual([e["kind"] for e in log].count("lotto_alert"), 1)
        self.assertEqual((book.s("fadelotto-plan")["round"], book.s("fadelotto-plan")["keep"]), (1, 1000))

    def test_plan_runner_half_trails_and_the_take_half_leaves_at_its_time(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        spec = [{**self.specs[0], "half_take": True}]                                    # $500: 22 contracts at $0.10
        book, _ = self.plan_cycle(b, 10, 0, spec)
        self.assertEqual(sorted((t["qty"], t["trail"], t.get("take")) for t in book.s("spy0dte-500")["open"]),
                         [(11, False, 0.30), (11, True, None)])                    # the runner has no take, as live
        b.quotes = lambda sym: (0.25, 0.26)                                              # runner armed (2x), take not reached
        self.plan_cycle(b, 11, 0, spec)
        b.quotes = lambda sym: (0.14, 0.15)                                              # 44% off the best: the runner goes
        book, log = self.plan_cycle(b, 11, 10, spec)
        s = book.s("spy0dte-500")
        self.assertEqual([(t["qty"], t["trail"]) for t in s["open"]], [(11, False)])
        self.assertEqual(s["closed"][-1]["proceeds"], 154.0)                             # 11 x $0.14
        b.quotes = lambda sym: (0.05, 0.06)
        book, log = self.plan_cycle(b, 15, 30, spec)
        s = book.s("spy0dte-500")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"], s["cash"]), ([], 55.0, 489.0))   # 500 - 220 + 154 + 55
        self.assertIn("到时卖出", [e for e in log if e["kind"] == "sleeve_plan_result"][-1]["why"])

    def test_plan_one_shot_sells_at_the_last_cycle_of_its_expiry_day(self):
        # no cycle runs after 15:50, so a 15:55 exit would never come and an in-the-money ticket would be booked at $0
        spec = [{"id": "oneshot-500", "kind": "oneshot", "start": 500, "und": "SPY", "offset": 0.01, "fraction": 1.0}]
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.plan_cycle(b, 10, 0, spec)
        b.quotes = lambda sym: (1.20, 1.25)
        book, _ = self.plan_cycle(b, 15, 40, spec)
        self.assertEqual(len(book.s("oneshot-500")["open"]), 1)                           # still held at 15:40
        book, log = self.plan_cycle(b, 15, 50, spec)
        s = book.s("oneshot-500")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"]), ([], 45 * 120.0))      # 45 contracts at the 15:50 bid
        self.assertIn("到期日最后一轮", s["closed"][-1]["how"])

    def test_plan_waits_out_a_missing_quote_and_books_an_expired_contract_at_zero(self):
        b = SleeveBroker(lambda s: (0.08, 0.10))
        spec = [self.specs[1]]
        self.plan_cycle(b, 10, 0, spec)
        b.option_snapshots = lambda syms: {}                                              # the quote call came back empty
        book, log = self.plan_cycle(b, 15, 30, spec)
        self.assertEqual(len(book.s("spy0dte-1000")["open"]), 1)                           # not sold at $0 on a missing quote
        book, log = self.plan_cycle(b, 9, 40, spec, day=date(2026, 10, 9))                 # the next day it has expired
        s = book.s("spy0dte-1000")
        self.assertEqual((s["open"], s["closed"][-1]["proceeds"], s["closed"][-1]["how"]), ([], 0.0, "到期作废"))

    def test_finished_entries_count_a_buy_with_its_runner_once_and_skip_open_ones(self):
        from hero import sleeves
        s = {"open": [{"tid": "c1r"}], "closed": [
            {"tid": "a1", "paid": 100.0, "proceeds": 0.0},
            {"tid": "b1", "paid": 100.0, "proceeds": 150.0}, {"tid": "b1r", "paid": 100.0, "proceeds": 20.0},
            {"tid": "c1", "paid": 50.0, "proceeds": 500.0},                                 # its runner is still open
            {"tid": "d1s", "paid": 10.0, "proceeds": 5.0}]}                                 # a recovered sold part
        self.assertEqual(sleeves.finished_entries(s), (3, -135.0))

    def test_stop_if_losing_after_n_finished_entries_buys_nothing_new(self):
        from hero import sleeves
        spec = [{**self.specs[0], "stop_if_losing": {"after": 2, "twin": "spy0dte-plan-500"}}]

        def ledger(closed, open_=()):
            if (self.root / "sleeves.json").exists():
                (self.root / "sleeves.json").unlink()
            book = sleeves.Book(self.root / "sleeves.json", spec)
            book.s("spy0dte-500").update(closed=closed, open=list(open_))
            book.save()
        lost = [{"tid": "a1", "paid": 100.0, "proceeds": 0.0}, {"tid": "b1", "paid": 100.0, "proceeds": 150.0},
                {"tid": "b1r", "paid": 100.0, "proceeds": 20.0}]                               # two entries, -$130
        ledger(lost)
        b = SleeveBroker(lambda s: (0.08, 0.10))
        book, log = self.cycle(b, 10, 0, spec)
        self.assertEqual(b.orders, {})
        stop = [e for e in log if e["kind"] == "sleeve_skip"]
        self.assertEqual(len(stop), 1)
        self.assertIn("已做完 2 次，合计亏 $130.00", stop[0]["why"])
        self.assertIn("spy0dte-plan-500 照常演算", stop[0]["why"])
        book, log = self.cycle(b, 10, 10, spec)
        self.assertEqual(len([e for e in log if e["kind"] == "sleeve_skip"]), 1)       # logged once a day
        self.assertEqual(b.orders, {})
        (self.root / "trades.jsonl").unlink()
        ledger(lost[:1])                                                                   # one finished entry: keeps buying
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, spec)
        self.assertTrue([o for o in b.orders.values() if o["side"] == "buy"])
        ledger([{"tid": "a1", "paid": 100.0, "proceeds": 0.0}, {"tid": "b1", "paid": 100.0, "proceeds": 260.0}])  # +$60
        b = SleeveBroker(lambda s: (0.08, 0.10))
        self.cycle(b, 10, 0, spec)
        self.assertTrue([o for o in b.orders.values() if o["side"] == "buy"])          # ahead after 2: keeps going

    def test_letf_holds_above_its_average_and_sells_below(self):
        spec = [{"id": "tqqq-500", "kind": "letf", "start": 500, "sym": "TQQQ", "ma": 20, "fraction": 1.0}]
        b = SleeveBroker(lambda s: (100.0, 100.0))
        b.bars["TQQQ"] = [{"t": f"2026-09-{d:02d}T04:00:00Z", "c": 90.0} for d in range(1, 30)]
        b.snaps["TQQQ"] = {"dailyBar": {"o": 99}, "latestTrade": {"p": 100.0}, "latestQuote": {"bp": 100.0, "ap": 100.0}}
        book, log = self.cycle(b, 15, 50, spec)
        s = book.s("tqqq-500")
        self.assertEqual((s["open"][0]["qty"], s["cash"]), (5.0, 0.0))                 # $500 of shares at $100
        b.snaps["TQQQ"]["latestTrade"]["p"] = 80.0
        b.snaps["TQQQ"]["latestQuote"] = {"bp": 80.0, "ap": 80.0}
        b.quotes = lambda s: (80.0, 80.0)
        from datetime import datetime
        from hero import sleeve_rules, sleeves
        book = sleeves.Book(self.root / "sleeves.json", spec)
        sleeves.Runner(b, Journal(self.root), self.cfg, spec, book, sleeve_rules.RULES, datetime(2026, 10, 9, 15, 50),
                       date(2026, 10, 9), sleep=lambda s: None).run()
        self.assertEqual(book.s("tqqq-500")["cash"], 400.0)                           # sold 5 at $80


class EarningsShares(unittest.TestCase):
    """Owner, 10-19: earnings only in shares or 2x funds; no option held through a report."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def calendar(self, reports):
        (self.root / "data" / "earnings.json").write_text(json.dumps({"reports": reports}))

    def run_rule(self, broker, day, hh, mm, pool, spec):
        from types import SimpleNamespace
        from unittest import mock
        from hero import sleeve_rules as sr
        events = []
        r = SimpleNamespace(c=broker, today=day, hhmm=f"{hh:02d}:{mm:02d}", specs=[], cache={"pool": list(pool)},
                            j=SimpleNamespace(event=lambda kind, **k: events.append((kind, k))))
        with mock.patch.object(sr, "ROOT", self.root):
            got = sr.EarningsLotto().entries(r, spec, {"slots": {}})
        return got, events

    def broker(self):
        b = SleeveBroker(lambda s: (0.95, 1.05))
        b.snaps = {s: {"dailyBar": {"o": 100.0}, "latestTrade": {"p": p}, "prevDailyBar": {"c": 100.0}}
                   for s, p in (("NVDA", 180.0), ("NVDL", 30.0), ("AAA", 100.0), ("BBB", 50.0))}
        b.option_contracts = lambda und, **kw: [
            {"symbol": f"{und}X{t[0].upper()}{k}", "strike_price": str(k), "expiration_date": kw["expiration_date_lte"], "type": t}
            for k in range(20, 260, 5) for t in ("call", "put")]
        b.option_snapshots = lambda syms: {s: {"latestQuote": {"ap": 2.0, "bp": 1.9}} for s in syms}
        return b

    def test_2x_fund_when_tradable_else_shares_and_unverified_dates_skipped(self):
        self.calendar({"NVDA": [{"date": "2026-10-08", "time": "post-market", "confirmed": True}],
                       "AAA": [{"date": "2026-10-09", "time": "pre-market"}],
                       "BBB": [{"date": "2026-10-08", "time": "post-market", "disputed": True}]})
        b = self.broker()
        (slot, picks), ev = self.run_rule(b, date(2026, 10, 8), 15, 45, ("NVDA", "AAA", "BBB"), {"id": "earn-500", "n": 3})
        got = {p["und"]: p for p in picks}
        self.assertEqual(set(got), {"NVDA", "AAA"})                                    # BBB's date is disputed
        self.assertEqual((got["NVDA"]["sym"], got["NVDA"]["ask"], got["NVDA"]["asset"]), ("NVDL", 30.0, "stock"))
        self.assertEqual((got["AAA"]["sym"], got["NVDA"]["exit_day"], got["NVDA"]["exit_at"]), ("AAA", "2026-10-09", "09:35"))
        self.assertIn("两倍 ETF NVDL", got["NVDA"]["why"])
        self.assertTrue(any(k == "sleeve_skip" and "没核实" in e["why"] and e["symbol"] == "BBB" for k, e in ev))
        b.untradable.add("NVDL")                                                       # not on Alpaca: the stock
        b.whole_only.add("NVDA")
        (slot, picks), ev = self.run_rule(b, date(2026, 10, 8), 15, 45, ("NVDA",), {"id": "earn-500", "n": 3})
        self.assertEqual((picks[0]["sym"], picks[0]["whole"]), ("NVDA", True))

    def test_the_old_call_lottery_comes_back_only_for_its_plan_twin(self):
        # owner, 10-21: what would the stopped option version have made? (its plan twin records it)
        self.calendar({"NVDA": [{"date": "2026-10-08", "time": "post-market", "confirmed": True}]})
        (slot, picks), ev = self.run_rule(self.broker(), date(2026, 10, 8), 15, 45, ("NVDA",),
                                          {"id": "earn-opt-plan-500", "n": 3, "instrument": "option", "plan_only": True})
        p = picks[0]
        self.assertEqual((p["asset"], p["sym"], p["exit_day"], p["exit_at"]), ("option", "NVDAXC190", "2026-10-09", "09:35"))  # 180 x (1 + 2 x 2.2%) -> 190
        self.assertIn("10-19 前的财报彩票", p["why"])

    def runner(self, broker, specs, rules, now, day):
        from hero import sleeves
        book = sleeves.Book(self.root / "sleeves.json", specs)
        j = Journal(self.root)
        sleeves.Runner(broker, j, {"sleeves": {"enabled": True, "fill_wait_s": 0}}, specs, book, rules, now, day,
                       sleep=lambda s: None).run()
        book.save()
        log = [json.loads(l) for l in (self.root / "trades.jsonl").read_text().splitlines()] if (self.root / "trades.jsonl").exists() else []
        return book, log

    def test_whole_shares_bought_at_the_close_and_sold_at_the_next_0935(self):
        from datetime import datetime
        from unittest import mock
        from hero import sleeves

        class Rule:
            def entries(self, run, spec, s):
                if s["slots"]:
                    return None
                return ("d1", [{"sym": "NVDL", "asset": "stock", "und": "NVDA", "ask": 30.0, "whole": True,
                                "exit_day": "2026-10-09", "exit_at": "09:35", "why": "NVDA 财报"}])
        b = self.broker()
        specs = [{"id": "earn-500", "kind": "fake", "start": 500, "fraction": 0.5}]
        self.calendar({})
        with mock.patch.object(sleeves, "ROOT", self.root):
            book, log = self.runner(b, specs, {"fake": Rule()}, datetime(2026, 10, 8, 15, 45), date(2026, 10, 8))
            buy = [o for o in b.orders.values() if o["side"] == "buy"][0]
            self.assertEqual((buy["symbol"], buy.get("qty"), buy.get("notional")), ("NVDL", "8", None))   # $250 // $30
            t = book.s("earn-500")["open"][0]
            self.assertEqual((t["exit_day"], t["exit_at"], t["und"]), ("2026-10-09", "09:35", "NVDA"))
            book, log = self.runner(b, specs, {"fake": Rule()}, datetime(2026, 10, 8, 15, 55), date(2026, 10, 8))
            self.assertFalse([o for o in b.orders.values() if o["side"] == "sell"])                # not the same day
            book, log = self.runner(b, specs, {"fake": Rule()}, datetime(2026, 10, 9, 9, 40), date(2026, 10, 9))
            sells = [o for o in b.orders.values() if o["side"] == "sell"]
            self.assertEqual((sells[0]["symbol"], sells[0]["qty"]), ("NVDL", "8"))

    def test_no_option_held_through_a_report(self):
        from datetime import datetime
        from unittest import mock
        from hero import sleeves

        class Rule:
            def __init__(self, exit_day):
                self.exit_day = exit_day
            def entries(self, run, spec, s):
                if s["slots"]:
                    return None
                return ("d1", [{"sym": "AAA261009C00105000", "asset": "option", "und": "AAA", "ask": 1.0,
                                "expiry": "2026-10-09", "exit_day": self.exit_day, "exit_at": "15:30", "why": "x"}])
        specs = [{"id": "snipe-500", "kind": "fake", "start": 500}]
        self.calendar({"AAA": [{"date": "2026-10-08", "time": "post-market"}]})       # reacts 10-09
        with mock.patch.object(sleeves, "ROOT", self.root):
            b = self.broker()
            book, log = self.runner(b, specs, {"fake": Rule("2026-10-09")}, datetime(2026, 10, 8, 10, 0), date(2026, 10, 8))
            self.assertFalse(b.orders)
            self.assertTrue(any(e["kind"] == "sleeve_skip" and "财报前不拿期权" in e["why"] for e in log))
            (self.root / "sleeves.json").unlink()
            (self.root / "trades.jsonl").unlink()
            b = self.broker()                                                          # sold the same day: allowed
            book, log = self.runner(b, specs, {"fake": Rule("2026-10-08")}, datetime(2026, 10, 8, 10, 0), date(2026, 10, 8))
            self.assertTrue([o for o in b.orders.values() if o["side"] == "buy"])


class SleeveRules(unittest.TestCase):
    """Each rule's picks on a fake market (what to buy, when); the order handling is tested in Sleeves."""

    def run_at(self, day, hh, mm, broker, pool=("AAA", "BBB", "CCC")):
        from datetime import datetime
        from types import SimpleNamespace
        r = SimpleNamespace(c=broker, today=day, hhmm=f"{hh:02d}:{mm:02d}", j=SimpleNamespace(event=lambda *a, **k: None),
                            specs=[], cache={"pool": list(pool)})
        return r

    def broker(self):
        b = SleeveBroker(lambda s: (0.40, 0.50))
        b.snaps = {"AAA": {"dailyBar": {"o": 97.0, "l": 95.0}, "latestTrade": {"p": 100.0}, "prevDailyBar": {"c": 100.0}},
                   "BBB": {"dailyBar": {"o": 100.0, "l": 99.5}, "latestTrade": {"p": 100.0}, "prevDailyBar": {"c": 100.0}},
                   "CCC": {"dailyBar": {"o": 100.0, "l": 99.9}, "latestTrade": {"p": 100.0}, "prevDailyBar": {"c": 100.0}},
                   "NVDA": {"dailyBar": {"o": 100.0, "l": 97.0}, "latestTrade": {"p": 98.0}, "prevDailyBar": {"c": 99.0}}}
        b.option_contracts = lambda und, **kw: [
            {"symbol": f"{und}{kw['expiration_date_lte']}{t[0].upper()}{k}", "strike_price": str(k),
             "expiration_date": kw["expiration_date_lte"], "type": t}
            for k in range(70, 131) for t in (["call", "put"] if "type" not in kw else [kw["type"]])]
        rising = [{"t": f"2026-0{m}-{d:02d}T04:00:00Z", "c": 50.0 + m * 5 + d * 0.1} for m in (6, 7, 8, 9) for d in range(1, 29)]
        b.bars = {"AAA": rising, "BBB": rising[::-1], "CCC": [{**x, "c": 60.0} for x in rising]}
        return b

    def test_gap_picks_the_gap_down_with_a_trailing_call(self):
        from hero import sleeve_rules as sr
        r = self.run_at(date(2026, 10, 8), 9, 50, self.broker())
        slot, picks = sr.Gap().entries(r, {"n": 2}, {"slots": {}, "cash": 500.0})
        self.assertEqual([p["und"] for p in picks], ["AAA"])                         # 97 / 100: -3%
        self.assertTrue(picks[0]["trail"] and picks[0]["sym"].endswith("C101"))
        self.assertIsNone(sr.Gap().entries(r, {"n": 2}, {"slots": {slot: "x"}, "cash": 500.0}))       # once a day

    def test_gap_skips_a_contract_the_sleeve_cannot_afford(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["CCC"]["dailyBar"]["o"] = 98.0                                         # a smaller gap, -2%
        b.option_snapshots = lambda syms: {x: {"latestQuote": {"bp": 1, "ap": 6.0 if x.startswith("AAA") else 0.5}} for x in syms}
        r = self.run_at(date(2026, 10, 8), 9, 50, b)
        slot, picks = sr.Gap().entries(r, {"n": 1}, {"slots": {}, "cash": 500.0})      # AAA's $600 is more than the whole ledger
        self.assertEqual([p["und"] for p in picks], ["CCC"])
        # owner, 10-22: a contract above half the ledger but within all of it is no longer passed over (the runner goes all in)
        b.option_snapshots = lambda syms: {x: {"latestQuote": {"bp": 1, "ap": 5.0 if x.startswith("AAA") else 0.5}} for x in syms}
        slot, picks = sr.Gap().entries(self.run_at(date(2026, 10, 8), 9, 50, b), {"n": 1}, {"slots": {}, "cash": 500.0})
        self.assertEqual([p["und"] for p in picks], ["AAA"])

    def test_sniper_plan_twins_follow_the_second_and_third_candidates(self):
        # owner, 10-22 (samples too slow): rank 2 / 3 plan ledgers, on the sniper's page
        from hero import notify, sleeve_rules as sr
        b = self.broker()
        with tempfile.TemporaryDirectory() as d:
            Path(d, "2026-10-08.json").write_text(json.dumps({
                "sniper": {"symbol": "AAA", "direction": "call", "catalyst": "x", "confidence": 0.3},
                "candidates": [{"rank": 1, "symbol": "AAA", "direction": "call", "confidence": 0.3, "why": "a"},
                               {"rank": 2, "symbol": "BBB", "direction": "put", "confidence": 0.2, "why": "second"}]}))
            r = self.run_at(date(2026, 10, 8), 9, 40, b)
            slot, picks = sr.Sniper().entries(r, {"id": "sniper-500", "dir": d}, {"slots": {}})
            self.assertEqual((slot, picks[0]["und"]), ("2026-10-08", "AAA"))
            slot, picks = sr.Sniper().entries(r, {"id": "sniper-r2-plan-500", "dir": d, "rank": 2}, {"slots": {}})
            self.assertEqual(picks[0]["und"], "BBB")
            self.assertTrue(picks[0]["sym"].endswith("P100") or "P" in picks[0]["sym"])
            self.assertIn("候选第 2 名：second", picks[0]["why"])
            self.assertEqual(sr.Sniper().entries(r, {"id": "sniper-r3-plan-500", "dir": d, "rank": 3}, {"slots": {}}), ("2026-10-08", []))
        self.assertEqual(notify.direction("sniper-r2-plan-500"), "sniper")
        self.assertEqual(notify.direction("amd0dte-plan-500"), "amd0dte")

    def test_peer_follows_claude_c_legs_in_shares_with_inverse_funds(self):
        # owner, 10-22 (「C 的大盘方向好像都很准」「按你的来」): what the peer's three-leg call is worth, plan only
        from types import SimpleNamespace
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps.update({"SPY": {"dailyBar": {"o": 766.0}, "latestTrade": {"p": 765.0}, "prevDailyBar": {"c": 773.0}},
                        "SH": {"dailyBar": {"o": 40.0}, "latestTrade": {"p": 40.2}, "prevDailyBar": {"c": 39.8}},
                        "SOXS": {"dailyBar": {"o": 50.0}, "latestTrade": {"p": 55.0}, "prevDailyBar": {"c": 45.0}},
                        "XLP": {"dailyBar": {"o": 88.0}, "latestTrade": {"p": 88.5}, "prevDailyBar": {"c": 87.1}}})
        with tempfile.TemporaryDirectory() as d:
            spec = {"id": "peerc-500", "kind": "peer", "exchange_dir": d}
            r = self.run_at(date(2026, 10, 22), 9, 40, b)
            self.assertIsNone(sr.Peer().entries(r, spec, {"slots": {}}))              # file not archived yet: wait
            Path(d, "2026-10-22.morning.json").write_text(json.dumps({"direction_call": {"legs": [
                {"symbol": "SPY", "side": "看跌", "falsifier": "收盘高于 773.09 即错"},
                {"symbol": "SMH", "side": "BEARISH", "falsifier": "close above 556.70"},
                {"symbol": "XLP", "side": "看涨", "falsifier": "收盘低于 87.11 即错"},
                {"symbol": "XLE", "side": "中性"}]}}))
            seen = []
            r.j = SimpleNamespace(event=lambda kind, **k: seen.append((kind, k)))
            slot, picks = sr.Peer().entries(r, spec, {"slots": {}})
            self.assertEqual(slot, "2026-10-22")
            self.assertEqual([(p["sym"], p["und"], p["scale"], p["asset"]) for p in picks],
                             [("SH", "SPY", 1, "stock"), ("SOXS", "SMH", 1 / 3, "stock"), ("XLP", "XLP", 1, "stock")])
            self.assertEqual(picks[0]["exit_at"], "15:50")
            self.assertIn("它的证伪线：收盘高于 773.09 即错", picks[0]["why"])
            self.assertIn("反向 ETF SOXS（3 倍，只用 1/3 的钱）", picks[1]["why"])
            self.assertEqual(seen, [])                                                   # XLE 中性 is simply not a leg
            Path(d, "2026-10-22.morning.json").write_text(json.dumps({"direction_call": {"legs": [{"symbol": "XLP", "side": "看跌"}]}}))
            self.assertEqual(sr.Peer().entries(r, spec, {"slots": {}}), ("2026-10-22", []))  # no inverse fund for XLP
            self.assertIn("没有反向 ETF", seen[-1][1]["why"])
            self.assertIsNone(sr.Peer().entries(self.run_at(date(2026, 10, 22), 10, 40, b), spec, {"slots": {}}))  # past the cutoff

    def test_dip_call_only_the_day_after_a_one_percent_drop(self):
        from hero import sleeve_rules as sr
        spec = {"und": "SPY", "offset": 0.006, "take": 3, "side": "call", "after_drop": -0.01}
        b = self.broker()
        b.snaps["SPY"] = {"dailyBar": {"o": 100.0, "l": 99.0}, "latestTrade": {"p": 99.5}, "prevDailyBar": {"c": 100.0}}
        b.bars["SPY"] = [{"t": "2026-10-06T04:00:00Z", "c": 102.0}, {"t": "2026-10-07T04:00:00Z", "c": 100.5}]   # -1.5%
        slot, picks = sr.ZeroDTE().entries(self.run_at(date(2026, 10, 8), 10, 0, b), spec, {"slots": {}})
        self.assertEqual(slot, "2026-10-08")
        self.assertTrue(picks[0]["sym"].endswith("C101"))           # a call although SPY is under its open; 99.5 x 1.006
        self.assertIn("昨天 SPY 收跌 -1.5%", picks[0]["why"])
        b.bars["SPY"][-1]["c"] = 101.5                               # only -0.5%: sit the day out
        self.assertEqual(sr.ZeroDTE().entries(self.run_at(date(2026, 10, 8), 10, 0, b), spec, {"slots": {}}), ("2026-10-08", []))

    def test_zdte_min_move_sits_out_a_quiet_open(self):
        from types import SimpleNamespace
        from hero import sleeve_rules as sr
        spec = {"id": "spy0dte-500", "und": "SPY", "offset": 0.003, "take": 3, "min_move": 0.005}
        b = self.broker()
        b.snaps["SPY"] = {"dailyBar": {"o": 100.0, "l": 99.8}, "latestTrade": {"p": 100.25}, "prevDailyBar": {"c": 100.1}}
        r = self.run_at(date(2026, 10, 20), 10, 0, b)
        seen = []
        r.j = SimpleNamespace(event=lambda kind, **k: seen.append((kind, k)))
        self.assertEqual(sr.ZeroDTE().entries(r, spec, {"slots": {}}), ("2026-10-20", []))     # like 10-20: +0.25%, sat out
        self.assertEqual(seen[0][0], "sleeve_skip")
        self.assertIn("离开盘 +0.25%，不到 0.5%，今天不做", seen[0][1]["why"])
        b.snaps["SPY"]["latestTrade"]["p"] = 100.6                                             # +0.6%: a call
        slot, picks = sr.ZeroDTE().entries(self.run_at(date(2026, 10, 20), 10, 0, b), spec, {"slots": {}})
        self.assertTrue(picks[0]["sym"].endswith("C101"))                                     # 100.6 x 1.003 = 100.9 -> 101
        b.snaps["SPY"]["latestTrade"]["p"] = 99.4                                              # -0.6%: a put
        slot, picks = sr.ZeroDTE().entries(self.run_at(date(2026, 10, 20), 10, 0, b), spec, {"slots": {}})
        self.assertTrue(picks[0]["sym"].endswith("P99"))                                      # 99.4 x 0.997 = 99.1 -> 99
        del spec["min_move"]                                                                   # without it, +0.25% trades
        b.snaps["SPY"]["latestTrade"]["p"] = 100.25
        self.assertEqual(len(sr.ZeroDTE().entries(self.run_at(date(2026, 10, 20), 10, 0, b), spec, {"slots": {}})[1]), 1)

    def test_zdte_takes_the_nearest_expiry_when_there_is_none_today_and_still_sells_at_half_past_three(self):
        from hero import sleeve_rules as sr
        spec = {"und": "NVDA", "offset": 0.006, "take": 3}
        b = self.broker()
        listed = {"NVDA": ["2026-10-15", "2026-10-17"]}                                # Tuesday: no same-day NVDA expiry

        def contracts(und, **kw):
            exps = [e for e in listed[und] if kw["expiration_date_gte"] <= e <= kw["expiration_date_lte"]]
            return [{"symbol": f"{und}{e}P{k}", "strike_price": str(k), "expiration_date": e, "type": "put"} for e in exps for k in range(90, 100)]
        b.option_contracts = contracts
        slot, picks = sr.ZeroDTE().entries(self.run_at(date(2026, 10, 13), 10, 0, b), spec, {"slots": {}})
        self.assertEqual(picks[0]["sym"], "NVDA2026-10-15P97")                         # 98 x 0.994 = 97.4 -> 97
        self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-15", "2026-10-13", "15:30"))
        self.assertIn("最近到期（10-15", picks[0]["why"])
        listed["NVDA"] = ["2026-10-13", "2026-10-15"]                                  # a same-day expiry wins when there is one
        slot, picks = sr.ZeroDTE().entries(self.run_at(date(2026, 10, 13), 10, 0, b), spec, {"slots": {}})
        self.assertEqual((picks[0]["expiry"], "当天到期" in picks[0]["why"]), ("2026-10-13", True))

    def test_breakgap_buys_an_unusual_gap_beyond_the_20_day_high_and_holds_one_night(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        quiet = [{"t": f"2026-09-{d:02d}T04:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0 + (0.5 if d % 2 else -0.5)}
                 for d in range(1, 29)] + [{"t": "2026-10-12T04:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0}]
        b.bars = {"AAA": quiet, "BBB": quiet, "CCC": quiet}                                  # ~1% a day, 20-day high 101
        b.snaps["AAA"] = {"dailyBar": {"o": 104.0, "l": 103.5}, "latestTrade": {"p": 104.5}, "prevDailyBar": {"c": 100.0}}  # +4%: 4 swings, above 101
        b.snaps["BBB"] = {"dailyBar": {"o": 102.0, "l": 101.5}, "latestTrade": {"p": 102.5}, "prevDailyBar": {"c": 100.0}}  # +2%: only 2 swings
        b.snaps["CCC"] = {"dailyBar": {"o": 105.0, "l": 103.0}, "latestTrade": {"p": 103.5}, "prevDailyBar": {"c": 100.0}}  # +5% but fading
        r = lambda hh, mm: self.run_at(date(2026, 10, 13), hh, mm, b)
        self.assertIsNone(sr.BreakGap().entries(r(9, 30), {}, {"slots": {}, "cash": 500.0}))
        slot, picks = sr.BreakGap().entries(r(9, 40), {}, {"slots": {}, "cash": 500.0})
        self.assertEqual([p["und"] for p in picks], ["AAA"])
        self.assertTrue(picks[0]["sym"].endswith("C106"))                                     # 104.5 x 1.006 = 105.1 -> 106
        self.assertEqual((picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-14", "15:50"))
        self.assertIn("平时一天波动的 4.1 倍", picks[0]["why"])

    def test_dipweek_buys_the_biggest_gap_down_a_call_3pct_out_for_a_week(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 97.0, "l": 95.5}, "latestTrade": {"p": 96.0}, "prevDailyBar": {"c": 100.0}}   # -3%, still below
        b.snaps["BBB"] = {"dailyBar": {"o": 97.5, "l": 97.0}, "latestTrade": {"p": 98.0}, "prevDailyBar": {"c": 100.0}}   # -2.5%, back above
        b.snaps["CCC"] = {"dailyBar": {"o": 99.0, "l": 98.5}, "latestTrade": {"p": 99.0}, "prevDailyBar": {"c": 100.0}}   # -1%: no gap
        r = lambda hh, mm: self.run_at(date(2026, 10, 16), hh, mm, b)
        s = {"slots": {}, "cash": 500.0, "open": []}
        self.assertIsNone(sr.DipWeek().entries(r(10, 0), {}, s))                                   # 15:45 to 15:58 only
        slot, picks = sr.DipWeek().entries(r(15, 50), {}, s)
        self.assertEqual([p["und"] for p in picks], ["AAA"])
        self.assertTrue(picks[0]["sym"].endswith("C99") and picks[0]["trail"])                     # 96 x 1.03 = 98.9 -> 99
        self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-28", "2026-10-23", "15:50"))
        self.assertIn("买价 $0.40 / 卖价 $0.50（价差占中间价 22%）", picks[0]["why"])
        self.assertIn("收盘前仍低于开盘", picks[0]["why"])
        s["open"] = [{"und": "AAA"}]
        self.assertEqual(sr.DipWeek().entries(r(15, 50), {}, s)[1][0]["und"], "BBB")               # AAA already held

    def test_dipweek_exit_variants_and_the_spread_cap(self):
        # owner, 10-23 (docs/hero-plan.md): hold to the fifth close / all out at 2x, and skip quotes wider than the cap
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 97.0, "l": 95.5}, "latestTrade": {"p": 96.0}, "prevDailyBar": {"c": 100.0}}
        b.snaps["BBB"] = {"dailyBar": {"o": 97.5, "l": 97.0}, "latestTrade": {"p": 98.0}, "prevDailyBar": {"c": 100.0}}
        r = lambda: self.run_at(date(2026, 10, 16), 15, 50, b)
        s = {"slots": {}, "cash": 500.0, "open": []}
        slot, picks = sr.DipWeek().entries(r(), {"exit": "hold"}, s)
        self.assertEqual((picks[0]["trail"], picks[0].get("take"), picks[0]["exit_day"]), (False, None, "2026-10-23"))
        self.assertIn("拿到 10-23 15:50 卖", picks[0]["why"])
        slot, picks = sr.DipWeek().entries(r(), {"exit": "take2"}, s)
        self.assertEqual((picks[0]["trail"], picks[0]["take"]), (False, 2))
        self.assertIn("2 倍止盈，否则 10-23 15:50 卖", picks[0]["why"])
        slot, picks = sr.DipWeek().entries(r(), {}, s)
        self.assertTrue(picks[0]["trail"] and "take" not in picks[0])                            # the default is unchanged
        self.assertEqual(sr.DipWeek().entries(r(), {"max_spread": 0.10}, s), ("2026-10-16", []))   # 0.40 / 0.50 is 22% wide: both skipped
        b.option_snapshots = lambda syms: {x: {"latestQuote": {"bp": 0.48, "ap": 0.50}} for x in syms}
        slot, picks = sr.DipWeek().entries(r(), {"max_spread": 0.10}, s)
        self.assertEqual(picks[0]["und"], "AAA")                                                 # 4% wide: taken

    def test_dipweek_sits_out_a_calm_market_and_overnight_shares_the_spread_cap(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 97.0, "l": 95.5}, "latestTrade": {"p": 96.0}, "prevDailyBar": {"c": 100.0}}
        quiet = [{"t": f"2026-09-{d:02d}T04:00:00Z", "c": 100.0 + (0.1 if d % 2 else 0.0)} for d in range(1, 29)]          # ~1% vol
        wild = [{"t": f"2026-09-{d:02d}T04:00:00Z", "c": 100.0 * (1.03 if d % 2 else 1.0)} for d in range(1, 29)]          # ~45% vol
        s = {"slots": {}, "cash": 500.0, "open": []}
        b.bars["SPY"] = quiet
        r = lambda: self.run_at(date(2026, 10, 16), 15, 50, b)
        self.assertEqual(sr.DipWeek().entries(r(), {"min_vol": 0.103}, s), ("2026-10-16", []))      # calm: sits out
        self.assertEqual(sr.DipWeek().entries(r(), {}, s)[1][0]["und"], "AAA")                       # no gate: trades
        b.bars["SPY"] = wild
        self.assertEqual(sr.DipWeek().entries(r(), {"min_vol": 0.103}, s)[1][0]["und"], "AAA")      # moving: trades
        b.bars["SPY"] = []
        self.assertEqual(sr.DipWeek().entries(r(), {"min_vol": 0.103}, s)[1][0]["und"], "AAA")      # no data: trades
        # overnight momentum: the same cap
        b.snaps["AAA"] = {"dailyBar": {"o": 100.0, "l": 99.0, "h": 103.2}, "latestTrade": {"p": 103.0}, "prevDailyBar": {"c": 100.0}}
        b.bars["AAA"] = [{"t": f"2026-0{m}-{d:02d}T04:00:00Z", "c": 50.0 + m * 5 + d * 0.1} for m in (6, 7, 8, 9) for d in range(1, 29)]
        self.assertEqual(sr.Overnight().entries(r(), {"max_spread": 0.10}, s), ("2026-10-16", []))  # 0.40 / 0.50: 22% wide
        slot, picks = sr.Overnight().entries(r(), {}, s)
        self.assertEqual(picks[0]["und"], "AAA")
        b.option_snapshots = lambda syms: {x: {"latestQuote": {"bp": 0.48, "ap": 0.50}} for x in syms}
        self.assertEqual(sr.Overnight().entries(r(), {"max_spread": 0.10}, s)[1][0]["und"], "AAA")
        self.assertIn("买价 $0.48 / 卖价 $0.50", sr.Overnight().entries(r(), {"max_spread": 0.10}, s)[1][0]["why"])

    def test_hikerally_records_a_month_out_put_after_a_fast_run_only_while_hiking(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        run_up = [{"t": f"2026-09-{d:02d}T04:00:00Z", "c": 100.0 if d <= 16 else 100.0 + (d - 16) * 0.8} for d in range(1, 31)]
        b.bars = {"SMH": run_up, "QQQ": run_up}
        b.snaps["SMH"] = {"dailyBar": {"o": 111.0, "l": 110.5}, "latestTrade": {"p": 112.0}, "prevDailyBar": {"c": 111.2}}  # +12% on 15 sessions
        b.snaps["QQQ"] = {"dailyBar": {"o": 105.0, "l": 104.5}, "latestTrade": {"p": 105.0}, "prevDailyBar": {"c": 104.0}}  # +5%: under 7%
        spec = {"hiking": True, "unds": {"SMH": 0.10, "QQQ": 0.07}}
        s = {"slots": {}, "cash": 500.0, "open": []}
        r = lambda d, hh, mm: self.run_at(d, hh, mm, b)
        self.assertIsNone(sr.HikeRally().entries(r(date(2026, 10, 16), 10, 0), spec, s))           # 15:45 to 15:58 only
        self.assertEqual(sr.HikeRally().entries(r(date(2026, 10, 16), 15, 50), {**spec, "hiking": False}, s), ("2026-10-16", []))
        slot, picks = sr.HikeRally().entries(r(date(2026, 10, 16), 15, 50), spec, s)
        self.assertEqual([p["und"] for p in picks], ["SMH"])
        self.assertTrue(picks[0]["sym"].endswith("P112") and picks[0]["trail"])                    # the put nearest the price
        self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-11-30", "2026-11-13", "15:50"))
        self.assertIn("15 个交易日涨 +12.0%", picks[0]["why"])
        self.assertEqual(s["hike_last"], {"SMH": "2026-10-16"})
        self.assertEqual(sr.HikeRally().entries(r(date(2026, 10, 19), 15, 50), spec, s), ("2026-10-19", []))  # one ticket a run-up

    def test_fade_lotto_finds_cheap_fades_alerts_once_and_samples_every_name(self):
        # owner, 10-21: SPCX up 8.6%, still above its open at noon, the 160 put due that day at $0.25 -> $3.15
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 108.0, "l": 107.0}, "latestTrade": {"p": 109.0}, "prevDailyBar": {"c": 100.0}}  # +8%, above open
        b.snaps["BBB"] = {"dailyBar": {"o": 106.0, "l": 104.0}, "latestTrade": {"p": 105.0}, "prevDailyBar": {"c": 100.0}}  # +6%, faded already
        b.snaps["CCC"] = {"dailyBar": {"o": 94.0, "l": 92.0}, "latestTrade": {"p": 93.0}, "prevDailyBar": {"c": 100.0}}     # -6%, below open
        b.snaps["SPCX"] = {"dailyBar": {"o": 103.5, "l": 103.0}, "latestTrade": {"p": 104.0}, "prevDailyBar": {"c": 100.0}}  # +3.5%
        b.most_actives = lambda top: ["SPCX", "BBB"]
        events = []

        def run(hh, mm, day=date(2026, 10, 22)):
            r = self.run_at(day, hh, mm, b)
            r.j.event = lambda kind, **k: events.append((kind, k))
            return r
        game = {"gap": 0.05, "max_ask": 0.5}
        s = {"slots": {}, "cash": 500.0}
        self.assertIsNone(sr.FadeLotto().entries(run(10, 50), game, s))                     # 11:00 to 14:00
        slot, picks = sr.FadeLotto().entries(run(11, 0), game, s)
        self.assertEqual((slot, [p["und"] for p in picks]), ("2026-10-22", ["AAA"]))          # the first find of the day
        p = picks[0]
        self.assertTrue(p["sym"].endswith("P108"))                                           # the put at the open
        self.assertEqual((p["take"], p["half"], p["exit_day"], p["exit_at"], p["ask"]), (3, True, "2026-10-22", "15:50", 0.5))
        self.assertIn("还在开盘上方", p["why"])
        s["slots"][slot] = "x"
        self.assertIsNone(sr.FadeLotto().entries(run(11, 10), game, s))                     # once a day
        self.assertIsNone(sr.FadeLotto().entries(run(11, 0), {**game, "max_ask": 0.3}, {"slots": {}, "cash": 500.0}))  # too dear
        sample = {"gap": 0.03, "max_ask": 1.0, "scan_from": "10:00", "scan_until": "15:00", "every": True, "qty": 1,
                  "alert": True, "alert_gap": 0.05, "alert_max_ask": 0.5}
        s2 = {"slots": {}, "cash": 10000.0}
        slot, picks = sr.FadeLotto().entries(run(10, 30), sample, s2)
        self.assertEqual(slot, "2026-10-22 10:30")
        self.assertEqual(sorted(x["und"] for x in picks), ["AAA", "CCC", "SPCX"])
        ccc = next(x for x in picks if x["und"] == "CCC")
        self.assertTrue(ccc["sym"].endswith("C94"))                                          # gap down: the call at the open
        alerts = [k for kind, k in events if kind == "lotto_alert"]
        self.assertEqual(sorted(a["symbol"][:3] for a in alerts), ["AAA", "CCC"])            # SPCX's 3.5% is a sample only
        self.assertTrue(all(not a["dry_run"] and "只记录、不下单" in a["why"] and not a["why"].startswith("彩票") for a in alerts))
        self.assertIsNone(sr.FadeLotto().entries(run(10, 40), sample, s2))                  # nothing new: no ticket, no alert
        self.assertEqual(len([1 for kind, _ in events if kind == "lotto_alert"]), 2)
        self.assertEqual(s2["keep"], 1000)
        slot, picks = sr.FadeLotto().entries(run(10, 30, date(2026, 10, 23)), sample, s2)      # a new day starts over
        self.assertEqual(len(picks), 3)
        self.assertEqual(list(s2["taken"]), ["2026-10-23"])

    def test_open_gap_takes_the_largest_gap_the_open_keeps_and_holds_a_later_expiry_overnight(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 97.0, "l": 95.0}, "latestTrade": {"p": 96.0}, "prevDailyBar": {"c": 100.0}}    # -3%, still falling
        b.snaps["BBB"] = {"dailyBar": {"o": 104.0, "l": 103.0}, "latestTrade": {"p": 103.5}, "prevDailyBar": {"c": 100.0}}  # +4% but fading
        b.snaps["CCC"] = {"dailyBar": {"o": 102.0, "l": 101.9}, "latestTrade": {"p": 102.5}, "prevDailyBar": {"c": 100.0}}  # +2%, holding
        slot, picks = sr.OpenGap().entries(self.run_at(date(2026, 10, 13), 9, 40, b), {}, {"slots": {}, "cash": 500.0})
        self.assertEqual(picks[0]["und"], "AAA")                                         # BBB's gap fades: skipped
        self.assertTrue(picks[0]["sym"].endswith("P95") and picks[0]["trail"])           # 96 x 0.994 = 95.4 -> 95
        self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-20", "2026-10-14", "15:50"))
        self.assertIn("留一晚", picks[0]["why"])
        self.assertIsNone(sr.OpenGap().entries(self.run_at(date(2026, 10, 13), 10, 0, b), {}, {"slots": {}, "cash": 500.0}))

    def test_sniper_buys_the_pre_market_pick_once_the_open_agrees(self):
        from hero import sleeve_rules as sr
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"dir": tmp}
            b = self.broker()
            r = lambda hh, mm: self.run_at(date(2026, 10, 13), hh, mm, b)
            self.assertEqual(sr.Sniper().entries(r(9, 30), spec, {"slots": {}}), ("2026-10-13", []))      # no file: no pick
            (Path(tmp) / "2026-10-13.json").write_text(json.dumps({"sniper": {"symbol": "AAA", "direction": "put",
                                                                              "catalyst": "a downgrade", "confidence": 0.4}}))
            self.assertIsNone(sr.Sniper().entries(r(9, 30), spec, {"slots": {}}))     # AAA is above its open: wait
            b.snaps["AAA"]["latestTrade"]["p"] = 96.0
            b.option_snapshots = lambda syms: {x: {"latestQuote": {"ap": 0.3}, "greeks": {"delta": -(1 - abs(int(x[-2:]) - 96) / 10)}}
                                               for x in syms}
            slot, picks = sr.Sniper().entries(r(9, 40), spec, {"slots": {}})
            self.assertTrue(picks[0]["sym"].endswith("P95") and picks[0]["trail"])    # a quiet stock: the first strike 0.3%+ out
            self.assertEqual((picks[0]["exit_day"], picks[0]["exit_at"]), (picks[0]["expiry"], "15:30"))
            self.assertIn("a downgrade", picks[0]["why"])
            self.assertIsNone(sr.Sniper().entries(r(10, 0), spec, {"slots": {}}))     # past the cutoff

    def test_sniper_holds_overnight_or_to_a_named_expiry_when_the_pick_says_so(self):
        # 10-23: the T pick said 10-30 in prose; the rule bought the day's $0.01 contract. Now the file can say it.
        from hero import sleeve_rules as sr
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"dir": tmp}
            b = self.broker()
            b.option_contracts = lambda und, **kw: [
                {"symbol": f"{und}{e}{kw['type'][0].upper()}{k}", "strike_price": str(k), "expiration_date": e, "type": kw["type"]}
                for e in (kw["expiration_date_gte"], "2026-10-15", "2026-10-17") for k in range(70, 131)]
            b.snaps["AAA"]["latestTrade"]["p"] = 96.0
            b.option_snapshots = lambda syms: {x: {"latestQuote": {"ap": 0.3}, "greeks": {"delta": -(1 - abs(int(x[-2:]) - 96) / 10)}}
                                               for x in syms}
            f = Path(tmp) / "2026-10-13.json"
            base = {"symbol": "AAA", "direction": "put", "catalyst": "x"}
            f.write_text(json.dumps({"sniper": base}))
            slot, picks = sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}})
            self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-13", "2026-10-13", "15:30"))
            f.write_text(json.dumps({"sniper": {**base, "overnight": True}}))
            slot, picks = sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}})
            self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-15", "2026-10-14", "15:50"))
            self.assertTrue(picks[0]["sym"].startswith("AAA2026-10-15P") and picks[0]["trail"])
            self.assertIn("拿到 10-14 15:50 卖", picks[0]["why"])
            f.write_text(json.dumps({"sniper": {**base, "expiry": "2026-10-16"}}))
            slot, picks = sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}})
            self.assertEqual((picks[0]["expiry"], picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-17", "2026-10-14", "15:50"))
            f.write_text(json.dumps({"sniper": {**base, "expiry": "2026-10-30"}}))                  # nothing listed that far
            self.assertEqual(sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}}), ("2026-10-13", []))

    def test_sniper_goes_half_a_daily_swing_out_but_not_past_a_0_30_delta(self):
        from hero import sleeve_rules as sr
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"dir": tmp}
            b = self.broker()
            b.bars["AAA"] = [{"t": f"2026-09-{d:02d}T04:00:00Z", "c": 100.0 * (1.05 if d % 2 else 1.0)} for d in range(1, 29)]  # ~5% a day
            (Path(tmp) / "2026-10-13.json").write_text(json.dumps({"sniper": {"symbol": "AAA", "direction": "put", "catalyst": "x"}}))
            b.snaps["AAA"]["latestTrade"]["p"] = 96.0
            b.option_snapshots = lambda syms: {x: {"latestQuote": {"ap": 0.3}, "greeks": {"delta": -(1 - abs(int(x[-2:]) - 96) / 10)}}
                                               for x in syms}
            slot, picks = sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}})
            self.assertTrue(picks[0]["sym"].endswith("P93"))                          # 2.5% out: 96 x 0.975 = 93.6 -> 93
            b.option_snapshots = lambda syms: {x: {"latestQuote": {"ap": 0.3}, "greeks": {"delta": -(1 - abs(int(x[-2:]) - 96) / 4)}}
                                               for x in syms}
            slot, picks = sr.Sniper().entries(self.run_at(date(2026, 10, 13), 9, 40, b), spec, {"slots": {}})
            self.assertTrue(picks[0]["sym"].endswith("P94"))                          # 93 is only 0.25 delta: step in to 94 (0.50)
            self.assertIn("delta 0.50", picks[0]["why"])

    def test_overnight_buys_the_biggest_strong_close_above_its_average_and_holds_three_sessions(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["AAA"] = {"dailyBar": {"o": 100.0, "l": 99.0, "h": 103.2}, "latestTrade": {"p": 103.0}, "prevDailyBar": {"c": 100.0}}  # +3%, top
        b.snaps["BBB"] = {"dailyBar": {"o": 100.0, "l": 100.0, "h": 110.0}, "latestTrade": {"p": 105.0}, "prevDailyBar": {"c": 100.0}}  # +5%, mid-range
        b.snaps["CCC"] = {"dailyBar": {"o": 100.0, "l": 100.0, "h": 106.0}, "latestTrade": {"p": 106.0}, "prevDailyBar": {"c": 100.0}}  # +6%, top
        b.bars["CCC"] = [{**x, "c": 200.0} for x in b.bars["CCC"]]                       # but far under its 50-day average
        r = lambda hh, mm: self.run_at(date(2026, 10, 13), hh, mm, b)
        self.assertIsNone(sr.Overnight().entries(r(15, 40), {}, {"slots": {}, "open": [], "cash": 500.0}))
        slot, picks = sr.Overnight().entries(r(15, 50), {}, {"slots": {}, "open": [], "cash": 500.0})
        self.assertEqual(picks[0]["sym"], "AAA2026-10-25C107")                          # 103 x 1.03 = 106.1 -> 107, 12 days out
        self.assertEqual((picks[0]["trail"], picks[0]["exit_day"], picks[0]["exit_at"]), (True, "2026-10-16", "15:50"))
        self.assertEqual(sr.Overnight().entries(r(15, 50), {}, {"slots": {}, "open": [{"und": "AAA"}], "cash": 500.0}),
                         ("2026-10-13", []))                                             # already holding AAA

    def test_pead_remembers_reaction_days_and_buys_the_day_after_a_gap_that_held(self):
        from hero import sleeve_rules as sr
        with tempfile.TemporaryDirectory() as tmp:
            cal = Path(tmp) / "earnings.json"
            cal.write_text(json.dumps({"reports": {"AAA": [{"date": "2026-10-09", "time": "post-market"}],
                                                   "BBB": [{"date": "2026-10-12", "time": "pre-market"}]}}))
            spec, s = {"earnings": str(cal)}, {"slots": {}, "open": [], "cash": 500.0, "pead_watch": {"2026-09-01": ["OLD"]}}
            b = self.broker()
            self.assertIsNone(sr.Pead().entries(self.run_at(date(2026, 10, 9), 9, 30, b), spec, s))   # before 10:00: only remembers
            self.assertEqual(s["pead_watch"], {"2026-10-12": ["AAA", "BBB"]})               # Friday after the close -> Monday
            b.bars["AAA"] = [{"t": "2026-10-09T04:00:00Z", "o": 99.0, "c": 100.0}, {"t": "2026-10-12T04:00:00Z", "o": 106.0, "c": 107.0}]
            b.bars["BBB"] = [{"t": "2026-10-09T04:00:00Z", "o": 99.0, "c": 100.0}, {"t": "2026-10-12T04:00:00Z", "o": 108.0, "c": 105.0}]
            b.snaps["AAA"]["latestTrade"]["p"] = 108.0
            slot, picks = sr.Pead().entries(self.run_at(date(2026, 10, 13), 10, 0, b), spec, s)
            self.assertEqual([p["und"] for p in picks], ["AAA"])                            # BBB gapped 8% but faded
            self.assertEqual((picks[0]["sym"], picks[0]["exit_day"]), ("AAA2026-10-25C112", "2026-10-16"))   # 108 x 1.03 = 111.2
            b.bars["AAA"][-1]["c"] = 105.0
            self.assertEqual(sr.Pead().entries(self.run_at(date(2026, 10, 13), 10, 0, b), spec, {**s, "slots": {}}),
                             ("2026-10-13", []))

    def test_vixspike_buys_a_spy_call_only_the_day_after_a_fear_day(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.snaps["SPY"] = {"dailyBar": {"o": 100.0, "l": 99.0}, "latestTrade": {"p": 99.5}, "prevDailyBar": {"c": 100.0}}
        b.bars["SPY"] = [{"t": "2026-10-06T04:00:00Z", "c": 102.0}, {"t": "2026-10-07T04:00:00Z", "c": 100.0}]   # -2%
        b.bars["VIXY"] = [{"t": "2026-10-06T04:00:00Z", "c": 20.0}, {"t": "2026-10-07T04:00:00Z", "c": 22.0}]    # +10%
        slot, picks = sr.VixSpike().entries(self.run_at(date(2026, 10, 8), 10, 0, b), {}, {"slots": {}})
        self.assertTrue(picks[0]["sym"].startswith("SPY") and picks[0]["sym"].endswith("C101"))   # under its open, a call anyway
        self.assertEqual((picks[0]["take"], picks[0]["exit_at"]), (3, "15:30"))
        self.assertIn("恐慌反弹", picks[0]["why"])
        b.bars["VIXY"][-1]["c"] = 21.0                                                    # VIXY only +5%: sit out
        self.assertEqual(sr.VixSpike().entries(self.run_at(date(2026, 10, 8), 10, 0, b), {}, {"slots": {}}), ("2026-10-08", []))

    def test_plan_only_sleeves_stay_out_of_the_live_run(self):
        from types import SimpleNamespace
        from hero import engine, sleeves
        seen, planned, order = [], [], []

        class Runner:
            def __init__(self, c, j, cfg, specs, book, rules, now, today):
                seen.extend(x["id"] for x in specs)
            def run(self):
                order.append("live")
        cfg = {"sleeves": {"list": [{"id": "a-500", "start": 500}, {"id": "dip-500", "start": 500, "plan_only": True}]}}
        with tempfile.TemporaryDirectory() as tmp:
            fake = SimpleNamespace(cfg=cfg, dry=False, c=None, now=None, j=SimpleNamespace(root=Path(tmp), event=lambda *a, **k: None),
                                   _sleeves_plan=lambda today, only_plan_only=False: (planned.append(only_plan_only), order.append("plan")))
            old = sleeves.Runner
            sleeves.Runner = Runner
            try:
                engine.Engine._sleeves(fake, date(2026, 10, 12))
            finally:
                sleeves.Runner = old
            self.assertEqual(seen, ["a-500"])
            self.assertEqual(planned, [True])
            self.assertEqual(order, ["live", "plan"])                                     # the plan only watches, after the real orders
            self.assertNotIn("dip-500", json.loads((Path(tmp) / "sleeves.json").read_text())["sleeves"])

    def test_flush_needs_two_percent_under_the_open(self):
        from hero import sleeve_rules as sr
        r = self.run_at(date(2026, 10, 8), 10, 0, self.broker())
        slot, picks = sr.Flush().entries(r, {"n": 2}, {"slots": {}, "cash": 500.0})
        self.assertEqual([p["und"] for p in picks], ["NVDA"])                         # 97 / 100: 3% down
        self.assertEqual((picks[0]["take"], picks[0]["exit_at"]), (2, "15:50"))
        self.assertIsNone(sr.Flush().entries(self.run_at(date(2026, 10, 8), 9, 50, self.broker()), {}, {"slots": {}, "cash": 500.0}))

    def test_weekly_trend_and_momentum_buy_this_weeks_last_expiry(self):
        from hero import sleeve_rules as sr
        r = self.run_at(date(2026, 10, 12), 10, 0, self.broker())                      # a Monday
        slot, picks = sr.Weekly().entries(r, {"mode": "momentum", "n": 1}, {"slots": {}})
        self.assertEqual(slot, "2026-W42")
        self.assertEqual((picks[0]["und"], picks[0]["expiry"], picks[0]["exit_day"]), ("AAA", "2026-10-16", "2026-10-15"))
        slot, picks = sr.Weekly().entries(r, {"mode": "trend", "n": 3}, {"slots": {}})
        kinds = {p["und"]: p["sym"][13] for p in picks}                                # BBB + 2026-10-16 + C/P
        self.assertEqual(kinds.get("BBB"), "P")                                       # falling: a put
        self.assertIsNone(sr.Weekly().entries(self.run_at(date(2026, 10, 14), 10, 0, self.broker()), {"mode": "trend"}, {"slots": {}}))

    def test_earnings_buys_the_close_before_the_reaction(self):
        from hero import sleeve_rules as sr
        import hero.sleeve_rules as mod
        b = self.broker()
        orig = mod.ROOT
        tmp = Path(self.id().replace(".", "_"))
        try:
            mod.ROOT = Path(tempfile.mkdtemp())
            (mod.ROOT / "data").mkdir()
            (mod.ROOT / "data" / "earnings.json").write_text(json.dumps({"reports": {
                "AAA": [{"date": "2026-10-08", "time": "post-market"}], "BBB": [{"date": "2026-10-09", "time": "pre-market"}],
                "CCC": [{"date": "2026-10-20", "time": "pre-market"}]}}))
            r = self.run_at(date(2026, 10, 8), 15, 50, b)
            slot, picks = sr.EarningsLotto().entries(r, {"n": 3}, {"slots": {}})
        finally:
            mod.ROOT = orig
        self.assertEqual(sorted(p["und"] for p in picks), ["AAA", "BBB"])
        self.assertEqual((picks[0]["exit_day"], picks[0]["exit_at"]), ("2026-10-09", "09:35"))
        # owner, 10-19: shares (no 2x fund for these names), not a call
        self.assertEqual({(p["sym"], p["asset"], p["ask"]) for p in picks}, {("AAA", "stock", 100.0), ("BBB", "stock", 100.0)})

    def test_relay_bets_the_weeks_strongest_reporter_then_switches_to_soxl(self):
        from hero import sleeve_rules as sr
        import hero.sleeve_rules as mod
        b = self.broker()
        days = [(date(2026, 1, 1) + timedelta(days=i)) for i in range(0, 300)]
        days = [d for d in days if d.weekday() < 5 and d < date(2026, 10, 8)]
        b.bars["AAA"] = [{"t": d.isoformat() + "T04:00:00Z", "c": 50.0 + i * 0.2} for i, d in enumerate(days)]   # strong
        b.bars["BBB"] = [{"t": d.isoformat() + "T04:00:00Z", "c": 100.0} for d in days]                         # flat
        orig = mod.ROOT
        try:
            mod.ROOT = Path(tempfile.mkdtemp())
            (mod.ROOT / "data").mkdir()
            (mod.ROOT / "data" / "earnings.json").write_text(json.dumps({"reports": {
                "BBB": [{"date": "2026-10-08", "time": "post-market"}],      # today, but weaker
                "AAA": [{"date": "2026-10-09", "time": "post-market"}]}}))   # tomorrow, stronger: wait for it
            spec = {"id": "relay-500", "switch_at": 3}
            s = {"slots": {}, "open": [], "cash": 500.0, "start": 500.0, "round": 1}
            self.assertIsNone(sr.Relay().entries(self.run_at(date(2026, 10, 8), 15, 45, b), spec, s))
            b.snaps["AAA"]["latestTrade"]["p"] = 99.9                     # 10% above: 109.89, the 110 strike
            slot, picks = sr.Relay().entries(self.run_at(date(2026, 10, 9), 15, 45, b), spec, s)
        finally:
            mod.ROOT = orig
        self.assertEqual(slot, "relay-2026-W41")
        self.assertEqual((picks[0]["und"], picks[0]["exit_day"], picks[0]["exit_at"]), ("AAA", "2026-10-12", "09:35"))
        self.assertEqual((picks[0]["sym"], picks[0]["asset"]), ("AAA", "stock"))     # shares since 10-19, not a call
        self.assertEqual(s["relay_phase"], "lotto")
        s["cash"] = 1600.0                                                 # tripled: from now on SOXL
        self.assertEqual(sr.Relay().phase(spec, s), "trend")
        s["round"], s["cash"] = 2, 500.0                                   # a new round starts with the lottery again
        self.assertEqual(sr.Relay().phase(spec, s), "lotto")

    def test_relay_trend_leg_holds_soxl_while_smh_is_above_its_200_day(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        days = [(date(2025, 6, 1) + timedelta(days=i)) for i in range(0, 495)]
        days = [d for d in days if d.weekday() < 5 and d < date(2026, 10, 8)]
        b.bars["SMH"] = [{"t": d.isoformat() + "T04:00:00Z", "c": 200.0 + i * 0.5} for i, d in enumerate(days)]
        b.snaps["SMH"] = {"dailyBar": {"o": 400.0}, "latestTrade": {"p": 400.0}, "prevDailyBar": {"c": 400.0}}
        b.snaps["SOXL"] = {"dailyBar": {"o": 40.0}, "latestTrade": {"p": 41.0}, "prevDailyBar": {"c": 40.0}}
        s = {"slots": {}, "open": [], "cash": 1600.0, "start": 500.0, "round": 1, "relay_round": 1, "relay_phase": "trend"}
        r = self.run_at(date(2026, 10, 8), 15, 50, b)
        slot, picks = sr.Relay().entries(r, {"id": "relay-500"}, s)
        self.assertEqual((picks[0]["sym"], picks[0]["asset"], picks[0]["ask"]), ("SOXL", "stock", 41.0))
        r.specs = [{"id": "relay-500"}]
        b.snaps["SMH"]["latestTrade"]["p"] = 150.0                          # below its 200-day average
        self.assertIn("SMH 跌破 200 日均线", sr.Relay().should_exit(r, "relay-500", {"asset": "stock", "sym": "SOXL"}) or "")
        self.assertIsNone(sr.Relay().should_exit(r, "relay-500", {"asset": "stock", "sym": "AAA"}))   # the lotto's shares: 09:35

    def test_net_waits_for_a_wild_week(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.bars["SPY"] = [{"t": (date(2024, 1, 1) + timedelta(days=i)).isoformat() + "T04:00:00Z", "c": 500.0 + i * 0.1}
                         for i in range(0, 640) if (date(2024, 1, 1) + timedelta(days=i)).weekday() < 5]
        r = self.run_at(date(2026, 10, 12), 10, 0, b)
        self.assertEqual(sr.Net().entries(r, {"n": 3}, {"slots": {}}), ("2026-W42", []))   # a calm, steady market

    def test_a_failing_rule_does_not_stop_the_others(self):
        from datetime import datetime
        from hero import sleeves
        class Boom:
            def entries(self, *a):
                raise RuntimeError("bad data")
        tmp = Path(tempfile.mkdtemp())
        specs = [{"id": "x-500", "kind": "boom", "start": 500}]
        book = sleeves.Book(tmp / "s.json", specs)
        sleeves.Runner(SleeveBroker(lambda s: (0, 0)), Journal(tmp), {"sleeves": {}}, specs, book, {"boom": Boom()},
                       datetime(2026, 10, 8, 10, 0), date(2026, 10, 8), sleep=lambda s: None).run()
        log = [json.loads(l) for l in (tmp / "trades.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["kind"], "alert_error")

    def test_zero_dte_skips_a_name_without_an_expiry_today(self):
        from hero import sleeve_rules as sr
        b = self.broker()
        b.option_contracts = lambda und, **kw: []
        r = self.run_at(date(2026, 10, 8), 10, 0, b)
        self.assertEqual(sr.ZeroDTE().entries(r, {"und": "AAA", "offset": 0.006, "take": 3}, {"slots": {}}), ("2026-10-08", []))


class NetShadow(unittest.TestCase):
    """The simulated net: casts only in a wild week, never sends an order, books the 20x take."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        spy = [100.0] * 388 + [96.0, 92.0, 89.0, 88.0, 90.0, 91.0, 92.0, 92.5, 93.0, 93.0, 93.0, 93.0]  # -12%, then +5.7%
        self.closes = {"SPY": spy, "UP1": series(0.002, seed=1), "UP2": series(0.001, seed=2), "DOWN": series(-0.001, seed=3)}
        self.conf = {**cfg(options={"enabled": False}), "live_from": "2000-01-01", "launch_approved": False,
                     "attempt": {"number": 1, "start_capital": 500, "end_loss": 0.9},
                     "net_shadow": {"enabled": True, "net": 15, "otm": 0.2, "take": 20, "fraction": 0.5, "start_capital": 500}}

    def tearDown(self):
        self.tmp.cleanup()

    def cycle(self, hh, mm, bid, ask=0.50):
        from datetime import datetime
        closes = self.closes

        class Broker(FakeClient):
            def daily_bars(self, symbols, start, adjustment="all"):
                return {s: bars(closes[s]) for s in symbols if s in closes}

            def option_contracts(self, und, **kw):
                return [{"symbol": f"{und}261030C00{int(float(kw['strike_price_gte']) + 1):03d}000",
                         "strike_price": str(int(float(kw["strike_price_gte"]) + 1)), "expiration_date": "2026-10-30"}]

            def option_snapshots(self, symbols):
                return {s: {"latestQuote": {"bp": bid, "ap": ask}} for s in symbols}
        c = Broker(self.closes, equity=500, last_equity=500)
        c.snaps = {s: {"latestTrade": {"p": closes[s][-1]}, "dailyBar": {"o": closes[s][-1]}} for s in closes}
        Engine(c, self.conf, Journal(Path(self.tmp.name))).run(today=TODAY, now=datetime(2026, 10, 1, hh, mm))
        book = json.loads((Path(self.tmp.name) / "net.json").read_text())
        log = [json.loads(l) for l in (Path(self.tmp.name) / "trades.jsonl").read_text().splitlines()]
        return c, book, log

    def test_wild_week_casts_the_net_and_books_the_take(self):
        c, book, log = self.cycle(9, 45, bid=0.4)
        self.assertEqual(book["open"], [])                      # before 10:00: nothing yet
        c, book, log = self.cycle(10, 5, bid=0.4)
        self.assertEqual(book["last_signal"]["fired"][0], "rebound")
        self.assertEqual(sorted(t["underlying"] for t in book["open"]), ["DOWN", "UP1", "UP2"])
        self.assertAlmostEqual(book["cash"], 250.0, places=1)   # half of $500 spread over the net
        self.assertEqual(c.orders, [])                          # a simulation: no orders, ever
        self.assertTrue(all(e["dry_run"] for e in log if e["kind"].startswith("net_")))
        c, book, log = self.cycle(10, 15, bid=10.5)             # bid reaches 20 x $0.50
        self.assertEqual(book["open"], [])
        self.assertAlmostEqual(book["cash"], 250.0 + 20 * 250.0, delta=1)
        self.assertEqual(len([e for e in log if e["kind"] == "net_buy"]), 1)   # once a week


class ExchangePage(unittest.TestCase):
    def test_messages_rendered_newest_first_and_escaped(self):
        from hero import exchange_page as ep
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for who, name, body in (("claude", "2026-10-05.json", '{"to": "codex", "note": "<script>x</script>"}'),
                                    ("claude-b", "2026-10-06.intro.json", '{"to": ["claude"], "reply_to": "exchange/claude/x.json"}'),
                                    ("codex", "README.txt", "规则")):
                (root / "exchange" / who).mkdir(parents=True, exist_ok=True)
                (root / "exchange" / who / name).write_text(body)
            (root / "exchange" / "codex" / "pack").mkdir()
            (root / "exchange" / "codex" / "pack" / "report.md").write_text("# r")
            data = ep.collect(root)
            self.assertEqual(data["messages"][0]["name"], "2026-10-06.intro.json")
            self.assertEqual([p["name"] for p in data["packs"]], ["pack/report.md"])
            page = ep.render(data)
            self.assertNotIn("<script>x</script>", page)
            self.assertIn("&lt;script&gt;", page)


class MacroHourly(unittest.TestCase):
    def test_gauges_recorded_once_an_hour(self):
        from datetime import datetime
        closes = {"SPY": series(0.001, seed=5), "UP1": series(0.002, seed=1), "UP2": series(0.001, seed=2),
                  "DOWN": series(-0.001, seed=3), "TLT": series(-0.003, seed=4), "USO": series(0.0, seed=6),
                  "VIXY": series(0.0, seed=7), "UUP": series(0.002, seed=8)}
        with tempfile.TemporaryDirectory() as d:
            for hh, mm in ((10, 5), (10, 45), (11, 5)):
                Engine(FakeClient(closes), cfg(), Journal(Path(d)), dry_run=True).run(
                    today=TODAY, now=datetime(2026, 10, 1, hh, mm))
            log = [json.loads(l) for l in (Path(d) / "trades.jsonl").read_text().splitlines()]
            ms = [e for e in log if e["kind"] == "macro"]
            self.assertEqual(len(ms), 2)                       # 10:05 and 11:05, not 10:45
            self.assertEqual(set(ms[0]["macro"]), {"rates", "oil", "fear", "dollar"})
            self.assertIsNotNone(dashboard.collect(Path(d), cfg())["macro"])


class Safety(unittest.TestCase):
    def test_refuses_live_endpoint(self):
        with self.assertRaises(AlpacaError):
            Alpaca(base_url="https://api.alpaca.markets")



class ResearchRelayVariants(unittest.TestCase):
    def test_one_bet_a_week_by_the_pick_and_dropping_the_best_weeks(self):
        from hero.research_relay_variants import stream
        t = lambda sym, day, mom, r, cheap=1.0: {"symbol": sym, "reaction_day": day, "strategy": "价外 10%", "mom126": mom,
                                                  "cheapness": cheap, "ret_open": r}
        lot = {"trades": [t("A", "2026-10-06", 0.5, 3.0), t("B", "2026-10-07", 0.1, -1.0, 0.5),
                          t("C", "2026-10-13", 0.2, 0.5), t("D", "2026-10-20", 0.3, -0.2)]}
        self.assertEqual(stream(lot, "价外 10%"), [("2026-10-06", 3.0), ("2026-10-13", 0.5), ("2026-10-20", -0.2)])
        self.assertEqual(stream(lot, "价外 10%", pick="cheap")[0], ("2026-10-07", -1.0))
        self.assertEqual(stream(lot, "价外 10%", drop_best=1)[0], ("2026-10-06", -1.0))


class ResearchSpydipRobust(unittest.TestCase):
    def test_groups_split_the_dips_by_threshold_year_and_the_ten_oclock_side(self):
        from hero.research_spydip_robust import calls, groups
        r = lambda day, prev, side, c: {"day": day, "prev_ret": prev, "side": side, "res": {"C|0.006|3": c}}
        rows = [r("2024-03-01", -0.012, "C", 2.0), r("2025-03-03", -0.02, "P", -1.0), r("2025-04-01", -0.006, "C", 0.5),
                r("2026-01-02", 0.015, "C", -1.0)]
        g = groups(rows)
        self.assertEqual(calls(g["昨天跌 1.00% 以上"], "0.006", 3), [2.0, -1.0])
        self.assertEqual(calls(g["昨天跌 0.5%–1%"], "0.006", 3), [0.5])
        self.assertEqual(calls(g["跌 1% 以上，2025 年"], "0.006", 3), [-1.0])
        self.assertEqual(calls(g["跌 1% 以上，10:00 已高于开盘"], "0.006", 3), [2.0])
        self.assertEqual(calls(g["对照：昨天涨 1% 以上"], "0.006", 3), [-1.0])


class ResearchZdteNearest(unittest.TestCase):
    def test_days_without_a_same_day_expiry_are_grouped_apart(self):
        from hero.research_zdte_nearest import gap_group, table
        r = lambda und, day, dte, x: {"und": und, "day": day, "dte": dte, "res": {"3": x, "5": x}}
        rep = {"rows": [r("TSLA", "2026-10-09", 0, 2.0), r("TSLA", "2026-10-13", 2, -1.0), r("NVDA", "2025-03-04", 3, -0.5)]}
        t = table(rep)
        self.assertEqual((gap_group(0), gap_group(2), gap_group(3)), ("当天到期", "1–2 天后到期", "3 天以上到期"))
        self.assertEqual(t["TSLA|当天到期|3"]["mean"], 2.0)
        self.assertEqual(t["合计|新规则补上的日子（非当天）|3"]["n"], 2)
        self.assertEqual(t["合计|2026 年：新规则补上的日子|3"]["mean"], -1.0)


class ResearchZdteTiming(unittest.TestCase):
    def test_exits_on_minute_closes(self):
        from hero.research_zdte_timing import play
        opt = {"09:35": {"c": 1.0}, "09:50": {"c": 2.3}, "10:10": {"c": 3.4}, "10:30": {"c": 5.6}, "11:00": {"c": 3.0},
               "15:30": {"c": 0.2}}
        r = play(opt, "09:35")                                                  # cost $1.10
        self.assertEqual((r["take3"], r["take5"]), (2, 4))
        self.assertAlmostEqual(r["trail2"], (3.0 - 0.3) / 1.1 - 1, places=3)    # 3.0 is 46% under the best 5.6
        self.assertAlmostEqual(r["take3_trail"], (2 + r["trail2"]) / 2, places=3)
        self.assertAlmostEqual(r["hold"], (0.2 - 0.02) / 1.1 - 1, places=3)
        self.assertIsNone(play({"15:00": {"c": 1.0}}, "09:35"))


class ResearchOpenCatalyst(unittest.TestCase):
    def test_groups_daily_pick_and_report(self):
        from hero.research_open_catalyst import daily_pick, groups, markdown, table, utc
        self.assertEqual((utc("2026-10-12", "16:00"), utc("2026-01-12", "09:30")), ("2026-10-12T20:00:00Z", "2026-01-12T14:30:00Z"))
        res = lambda x: {k: x for k in ("take3", "take5", "trail2", "trail15", "take3_trail", "hold")}
        rows = [{"und": "META", "day": "2026-10-13", "gap": 0.03, "kind": "C", "with_gap": True, "news": 4, "headline": "Muse",
                 "exp": "2026-10-16", "res": res(2.0), "next": 3.0},
                {"und": "TSLA", "day": "2026-10-13", "gap": -0.02, "kind": "C", "with_gap": False, "news": 0, "headline": "",
                 "exp": "2026-10-13", "res": res(-1.0), "next": None}]
        g = groups(rows)
        self.assertEqual(([r["und"] for r in g["　开盘前有新闻"]], [r["und"] for r in g["　没有新闻"]]), (["META"], ["TSLA"]))
        self.assertEqual([r["und"] for r in daily_pick(rows)], ["META"])                 # the larger gap that day
        t = table({"rows": rows})
        self.assertEqual(t["跳空 2% 以上（全部）|take3"]["mean"], 0.5)
        self.assertIn("| 2026-10-13 | META | +3.0% | 看涨 | 4 | +200% | Muse |",
                      markdown({"generated": "x", "start": "a", "end": "b", "rows": rows}, t))


class ResearchAnalyst(unittest.TestCase):
    def test_strict_pre_market_ratings_and_the_confirm_minute(self):
        from types import SimpleNamespace
        from hero.research_analyst import confirm_minute, events, markdown, table
        news = [{"headline": "Melius Research Upgrades Microsoft to Buy, Announces $665 Price Target", "symbols": ["MSFT"],
                 "created_at": "2026-10-05T11:02:00Z"},                                     # 07:02 ET: that morning
                {"headline": "Citi Downgrades Microsoft to Neutral", "symbols": ["MSFT"], "created_at": "2026-10-09T21:30:00Z"},  # Fri 17:30
                {"headline": "UBS Upgrades Microsoft to Buy", "symbols": ["MSFT"], "created_at": "2026-10-07T15:00:00Z"},        # in session
                {"headline": "Microsoft's AI upgrade to Copilot", "symbols": ["MSFT"], "created_at": "2026-10-06T11:00:00Z"}]
        ev = events(SimpleNamespace(news_range=lambda syms, a, b: news), "MSFT", "2026-10-01", "2026-10-12")
        self.assertEqual({d: e["up"] for d, e in ev.items()}, {"2026-10-05": True, "2026-10-12": False})   # Friday night -> Monday
        m = {"09:30": {"o": 100.0, "c": 100.0}, "09:50": {"c": 100.5}, "10:20": {"c": 101.2}, "11:00": {"c": 99.0}}
        self.assertEqual((confirm_minute(m, True), confirm_minute(m, False)), ("10:20", "11:00"))   # 99 is -1%
        res = {k: 2.0 for k in ("take3", "take5", "trail2", "trail15", "take3_trail", "hold")}
        rep = {"generated": "x", "start": "a", "end": "b",
               "rows": [{"und": "MSFT", "day": "2026-10-05", "up": True, "gap": 0.012, "headline": "Melius Research Upgrades Microsoft to Buy",
                         "exp": "2026-10-09", "confirm_at": None, "res": {"open": res}, "next": {"open": 1.5}}]}
        t = table(rep)
        self.assertEqual((t["上调|open|take3"]["mean"], t["上调|open|next"]["mean"], t["上调|confirm|take3"]), (2.0, 1.5, None))
        self.assertIn("| 2026-10-05 | MSFT | 上调 | +1.2% | +200% |", markdown(rep, t))


class ResearchPremarket(unittest.TestCase):
    def test_scan_keeps_todays_movers_with_their_headlines(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from hero.research_premarket import markdown, run
        snaps = {"AAA": {"latestTrade": {"p": 105.0, "t": "2026-10-14T12:50:00Z"}, "dailyBar": {"c": 100.0, "t": "2026-10-13T04:00:00Z"}},
                 "BBB": {"latestTrade": {"p": 101.0, "t": "2026-10-14T12:50:00Z"}, "dailyBar": {"c": 100.0, "t": "2026-10-13T04:00:00Z"}},
                 "CCC": {"latestTrade": {"p": 90.0, "t": "2026-10-13T19:59:00Z"}, "dailyBar": {"c": 100.0, "t": "2026-10-13T04:00:00Z"}},
                 "DDD": {"latestTrade": {"p": 1.0, "t": "2026-10-14T12:50:00Z"}, "dailyBar": {"c": 2.0, "t": "2026-10-13T04:00:00Z"}}}

        def d(path, params):
            return {"gainers": [{"symbol": "AAA", "percent_change": 5.0}], "losers": []} if "movers" in path else {"most_actives": []}
        c = SimpleNamespace(_d=d, stock_snapshots=lambda syms: {s: snaps[s] for s in syms if s in snaps},
                            news=lambda syms, start, limit=50: [{"symbols": ["AAA"], "headline": "AAA wins a contract"}])
        rep = run(c, datetime(2026, 10, 14, 12, 55, tzinfo=timezone.utc))
        self.assertEqual([(r["symbol"], r["move"], r["news"]) for r in rep["rows"]], [("AAA", 0.05, ["AAA wins a contract"])])
        self.assertIn("| AAA | $105.00 | +5.0% | 是 | AAA wins a contract |", markdown(rep))


class ResearchIdeas(unittest.TestCase):
    def test_intraday_and_multiday_exits(self):
        from hero.research_ideas import cell, intraday, multiday, report
        m = {"15:25": {"c": 0.10}, "15:40": {"c": 0.25}, "15:50": {"c": 0.40}, "15:55": {"c": 0.20}, "15:58": {"c": 0.05}}
        r = intraday(m, "15:25", "15:58")                                          # cost 0.11
        self.assertEqual((r["take2"], r["take3"], r["take5"]), (1, 2, round((0.04) / 0.11 - 1, 4)))
        self.assertAlmostEqual(r["trail2"], (0.18) / 0.11 - 1, places=3)           # 0.20 is half the best 0.40
        d = [{"o": 1.5, "c": 2.4}, {"o": 2.4, "c": 1.2}, {"o": 1.2, "c": 1.0}]
        x = multiday(1.1, d)
        self.assertEqual((x["take2"], round(x["next_open"], 3)), (1.0, round(1.35 / 1.1 - 1, 3)))
        self.assertAlmostEqual(x["trail2"], 1.08 / 1.1 - 1, places=3)               # 1.2 is half the best 2.4
        rows = [{"day": "2026-10-13", "res": {"take2": 1.0}}, {"day": "2026-10-13", "res": {"take2": -1.0}}]
        self.assertEqual(cell(rows, "take2")["n"], 2)
        self.assertIn("| a | 2 | +0% / 50%", report("t", "i", {"a": rows}, {"take2": "x"})[1])

    def test_overnight_signals_and_event_days(self):
        from hero.research_eventday import kind_of
        from hero.research_overnight import signals
        flat = [{"t": f"2026-{m:02d}-{d:02d}T04:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100.0} for m in (6, 7, 8) for d in range(1, 29)]
        up = {"t": "2026-09-01T04:00:00Z", "o": 100, "h": 105, "l": 99.5, "c": 104.8}
        sig = signals({"AAA": flat + [up]})
        self.assertEqual([(s["und"], s["kind"], s["trend"]) for s in sig], [("AAA", "C", True)])
        self.assertEqual((kind_of("2026-09-16"), kind_of("2024-02-13"), kind_of("2024-02-14")), ("Fed", "CPI", ""))


class Robust(unittest.TestCase):
    def test_a_mean_carried_by_three_big_wins_is_flagged(self):
        from hero import robust
        xs = [(f"2025-{m:02d}-01", -0.5) for m in range(1, 13)] + [("2024-06-01", 9.0), ("2024-07-01", 8.0), ("2024-08-01", 7.0),
                                                                    ("2024-09-01", -0.5), ("2024-10-01", -0.5)]
        s = robust.summary(xs, end="2025-12-31")
        self.assertGreater(s["mean"], 0)
        self.assertEqual((s["n"], s["trim"], s["median"]), (17, -0.5, -0.5))
        self.assertEqual(s["years"]["2025"], (-0.5, 12))
        self.assertEqual(s["recent"], (-0.5, 3))                                  # Oct, Nov, Dec 2025
        text = "；".join(s["flags"])
        for part in ("样本少", "去掉最好 3 笔就不赚", "中位数", "各年份不一致"):
            self.assertIn(part, text)
        self.assertTrue(robust.fragile(s))
        self.assertIn("| a | 17 |", robust.table({"a": s, "b": None}))
        self.assertIn("| b | 0 |", robust.table({"a": s, "b": None}))

    def test_extra_cost_and_a_steady_edge(self):
        from hero import robust
        self.assertAlmostEqual(robust.with_cost(0.0, 0.05), 0.95 / 1.05 - 1)
        self.assertEqual(robust.with_cost(-1.0, 0.05), -1.0)
        thin = robust.summary([(f"2025-01-{d:02d}", 0.05 if d % 2 else 0.03) for d in range(1, 31)])
        self.assertIn("买卖再各多 5% 成本就不赚", "；".join(thin["flags"]))
        steady = robust.summary([(f"2025-{m:02d}-{d:02d}", 0.3) for m in range(1, 13) for d in (1, 10, 20)])
        self.assertEqual(steady["flags"], [])
        self.assertFalse(robust.fragile(steady))
        self.assertIsNone(robust.summary([]))

    def test_price_basis_catches_a_split(self):
        from hero import robust
        pairs = [("2024-05-01", "NVDA", 850.0, 85.0), ("2025-05-01", "NVDA", 110.0, 110.5)]
        self.assertEqual([b["day"] for b in robust.price_basis(pairs)], ["2024-05-01"])


class ResearchFills(unittest.TestCase):
    def test_fills_against_the_last_bar_before_them(self):
        from hero import research_fills as rf
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "journal").mkdir()
            (root / "journal-s500").mkdir()
            fill = lambda key, sym, side, qty, px, t: json.dumps({"order_key": key, "execution_key": key + t[-3:], "symbol": sym,
                                                                    "side": side, "qty": qty, "price": px, "transaction_time": t})
            (root / "journal-s500" / "fills.jsonl").write_text("\n".join([
                fill("b1", "SPY261019P00762000", "buy", "2", "0.40", "2026-10-19T14:00:05Z"),    # 10:00 ET, two parts
                fill("b1", "SPY261019P00762000", "buy", "2", "0.44", "2026-10-19T14:00:06Z"),
                fill("s1", "SPY261019P00762000", "sell", "4", "0.90", "2026-10-19T19:30:02Z"),   # 15:30 ET
                fill("x1", "SPY", "buy", "1", "700", "2026-10-19T14:00:05Z")]) + "\n")         # a stock: left out
            (root / "journal" / "fills.jsonl").write_text(fill("m1", "XLV261120C00175000", "buy", "5", "6.20", "2026-10-16T13:30:40Z") + "\n")
            (root / "journal" / "trades.jsonl").write_text("\n".join(json.dumps(e) for e in [
                {"kind": "order", "intent_key": "i1", "evidence": {"bid": 6.08, "ask": 6.43, "mid": 6.255}},
                {"kind": "order_ack", "intent_key": "i1", "order_key": "m1"}]) + "\n")

            orders = rf.load_orders(root)
            self.assertEqual([(o["order_key"], o["qty"], o["price"]) for o in orders],
                             [("m1", 5.0, 6.2), ("b1", 4.0, 0.42), ("s1", 4.0, 0.9)])

            def bar(day, hhmm, c):
                h, m = map(int, hhmm.split(":"))
                return {"t": f"{day}T{h + 4:02d}:{m:02d}:00Z", "c": c}

            class Client:
                def clock(self): return {"is_open": False}

                def option_bars(self, syms, start, end, timeframe=None):
                    day = start[:10]
                    return {"SPY261019P00762000": [bar(day, "09:58", 0.38), bar(day, "09:59", 0.40), bar(day, "10:00", 0.42),
                                                   bar(day, "15:00", 1.20)]} if day == "2026-10-19" else {}

            with mock.patch("time.sleep"):
                rep = rf.run(Client(), root)
        rows = {r["order_key"]: r for r in rep["rows"]}
        self.assertEqual((rows["b1"]["ref"], rows["b1"]["age"]), (0.40, 1))         # 09:59, not its own 10:00 minute
        self.assertAlmostEqual(rows["b1"]["cost"], 0.05)                            # paid 0.42 against 0.40
        self.assertEqual((rows["b1"]["horizon"], rows["b1"]["bucket"]), ("当天到期", "$0.10–0.50"))
        self.assertNotIn("cost", rows["s1"])                                        # last bar 15:00 is 30 minutes old
        self.assertAlmostEqual(rows["m1"]["vs_mid"], 6.2 / 6.255 - 1, places=4)     # a buy below the mid: negative cost
        self.assertEqual(rows["m1"]["horizon"], "8 天以上到期")
        md = rf.report(rep)
        self.assertIn("| 买入 | 当天到期 | 1 | +5.0%", md)
        self.assertIn("XLV261120C00175000", md)
        self.assertAlmostEqual(rf.side_cost("sell", 0.9, 1.0), 0.1)


class ResearchEventTurn(unittest.TestCase):
    DAYS = [(date(2026, 8, 3) + timedelta(days=k)).isoformat() for k in range(40)]

    def closes(self):
        xs = [100.0] * 40
        for k, c in zip(range(19, 25), (100, 98, 97, 96, 95, 94)):   # a 6% slide into the event on day 25
            xs[k] = float(c)
        xs[25], xs[26] = 93.0, 95.0                                    # down on the event day, up the day after
        for k in range(27, 40):
            xs[k] = 95.0 + (k - 26)
        return xs

    def test_signals_take_the_first_close_each_way_after_the_event(self):
        from hero import research_event_turn as et
        xs = self.closes()
        sig = et.signals(self.DAYS, xs, {self.DAYS[25]: "Fed", self.DAYS[5]: "CPI"})   # day 5 is too early for a 20-day average
        got = {(s["kind"], s["day"]) for s in sig}
        self.assertEqual(got, {("P", self.DAYS[25]), ("C", self.DAYS[26])})
        c = next(s for s in sig if s["kind"] == "C")
        self.assertEqual((c["pre"], c["below"], c["ev"]), (round(94 / 100 - 1, 4), True, "Fed"))
        self.assertEqual(et.stock_moves(xs, 26, "C")["s5"], round(100 / 95 - 1, 4))
        plain = et.ordinary(self.DAYS, xs, {self.DAYS[25]: "Fed"})
        self.assertNotIn(self.DAYS[26], {s["day"] for s in plain})            # the event's sessions are left out
        self.assertIn(("P", self.DAYS[21]), {(s["kind"], s["day"]) for s in plain})

    def test_exits_on_daily_closes(self):
        from hero.research_event_turn import exits
        path = [1.5, 2.4, 3.3, 3.5, 1.9] + [1.0] * 15
        r = exits(1.0, path)
        self.assertAlmostEqual(r["trail2"], 1.9 * 0.95 - 1, places=4)          # 1.9 is below 60% of the best 3.5
        self.assertAlmostEqual(r["take3_trail"], 0.5 * (3.3 * 0.95 - 1) + 0.5 * (1.9 * 0.95 - 1), places=4)
        self.assertAlmostEqual(r["d20"], 0.95 - 1, places=4)
        self.assertEqual(set(exits(1.0, [1.2, 1.4, 1.1])), set())              # still open, no day reached
        self.assertEqual(set(exits(1.0, [1.2] * 7)), {"d5"})

    def test_run_buys_the_month_out_option_nearest_the_price(self):
        from unittest import mock
        from hero import research_event_turn as et
        xs = self.closes()
        bars = [{"t": d + "T04:00:00Z", "c": c} for d, c in zip(self.DAYS, xs)]

        class Client:
            def daily_bars(self, syms, start, adjustment="all", feed="iex"):
                return {s: bars for s in syms}

            def option_contracts(self, und, status, type, **kw):
                if status != "inactive":
                    return []
                return [{"symbol": f"{und}-{type}-{e}-{k}", "expiration_date": e, "strike_price": str(k)}
                        for e in ("2026-09-25", "2026-10-02") for k in (90, 95, 100)]

            def option_bars(self, syms, start, end, timeframe="1Day"):
                return {syms[0]: [{"t": d + "T04:00:00Z", "c": 1.0 + 0.1 * k} for k, d in enumerate(self.days) if d >= start]}

        cl = Client()
        cl.days = self.DAYS
        with mock.patch.object(et, "EVENTS", {self.DAYS[25]: "Fed"}), mock.patch.object(et, "START", self.DAYS[0]), \
                mock.patch.object(et, "latest", lambda name: {"rows": [{"und": "AAA"}]}), mock.patch.object(et.time, "sleep"):
            rep = et.run(cl)
        c = next(r for r in rep["rows"] if r["und"] == "SMH" and r["kind"] == "C")
        self.assertEqual(c["option"], "SMH-call-2026-09-25-95")              # 27 days out beats 34; strike nearest 95
        self.assertAlmostEqual(c["cost"], (1.0 + 0.1 * 26) * 1.05, places=4)
        self.assertIn("d5", c["res"])
        gs = et.rules(rep["rows"])
        self.assertEqual(len(gs["反弹：事件前 5 天跌 5%+"]), 3)                  # SMH, QQQ, AAA
        self.assertEqual(len(gs["　反弹 5%+：个股"]), 1)
        from hero.research_ideas import checks
        self.assertIn("稳健性检查：第 5 天收盘卖", checks(gs, et.EXITS, rep["end"]))


class ResearchDipWeek(unittest.TestCase):
    def test_pace_goes_slow_while_the_market_is_open(self):
        from unittest import mock
        from hero.research_event_turn import Pace

        class Open:
            def clock(self): return {"is_open": True}
        with mock.patch("time.sleep") as slept:
            Pace(Open()).wait()
            Pace(object()).wait()                                   # no clock: the quick pace
        self.assertEqual([c.args[0] for c in slept.call_args_list], [2.0, 0.1])

    def test_events_are_gap_downs_split_by_whether_the_bounce_held(self):
        from hero.research_dip_week import events
        days = [f"2026-09-{d:02d}" for d in range(1, 31)]
        closes = [100.0] * 30
        opens = [100.0] * 30
        opens[22], closes[22] = 97.0, 96.0        # -3% open, closed lower: failed
        opens[25], closes[25] = 97.5, 99.0        # -2.5% open (from 100), closed above it: held
        closes[27:] = [104.0] * 3
        opens[28:] = [104.0] * 2                  # no gap after it
        ev = events(days, opens, closes)
        self.assertEqual([(e["day"], e["failed"]) for e in ev], [(days[22], True), (days[25], False)])
        self.assertEqual(ev[0]["stock"]["s5"], round(104 / 96 - 1, 4))

    def test_option_path_fills_quiet_days_and_values_expiry_from_the_stock(self):
        from hero.research_dip_week import exits, option_path
        sessions = ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
        marks = {"2026-10-13": 2.5, "2026-10-15": 1.0}
        path, done = option_path(1.0, marks, sessions, "2026-10-16", 100.0, {"2026-10-16": 103.0})
        self.assertEqual((path, done), ([2.5, 2.5, 1.0, 3.0], True))           # 10-14 keeps 2.5; expiry worth 103 - 100
        r = exits(1.0, path, done, 0.05)
        self.assertAlmostEqual(r["take2"], 2.5 * 0.95 - 1, places=4)
        self.assertAlmostEqual(r["trail2"], 1.0 * 0.95 - 1, places=4)           # 1.0 is under 60% of the best 2.5
        self.assertAlmostEqual(r["d5"], 3.0 * 0.95 - 1, places=4)               # expiry before the 5th session
        path, done = option_path(1.0, {}, sessions[:2], "2026-10-23", 100.0, {})
        self.assertEqual((path, done), ([1.0, 1.0], False))
        self.assertEqual(set(exits(1.0, path, done, 0.05)), {"d1"})

    def test_run_buys_the_week_out_calls_at_the_close(self):
        from unittest import mock
        from hero import research_dip_week as dw
        days = [(date(2026, 8, 3) + timedelta(days=k)).isoformat() for k in range(40)]
        bars = [{"t": d + "T04:00:00Z", "o": 100.0, "c": 100.0} for d in days]
        bars[30] = {**bars[30], "o": 96.0, "c": 95.0}

        class Client:
            def daily_bars(self, syms, start, adjustment="all", feed="iex"):
                return {s: bars for s in syms}

            def option_contracts(self, und, status, type, **kw):
                if status != "inactive":
                    return []
                return [{"symbol": f"{und}-{e}-{k}", "expiration_date": e, "strike_price": str(k)}
                        for e in (days[36], days[38]) for k in (95, 97.5, 100)]

            def option_bars(self, syms, start, end, timeframe="1Day"):
                return {s: [{"t": d + "T04:00:00Z", "c": 1.0 + 0.2 * k} for k, d in enumerate(days[30:])] for s in syms}

        with mock.patch.object(dw, "START", days[0]), mock.patch.object(dw, "EXTRA", ()), \
                mock.patch.object(dw, "latest", lambda name: {"rows": [{"und": "AAA"}]}), mock.patch("time.sleep"):
            rep = dw.run(Client())
        got = {r["leg"]: r for r in rep["rows"]}
        self.assertEqual((got["atm"]["option"], got["otm3"]["option"]), (f"AAA-{days[36]}-95", f"AAA-{days[36]}-100"))   # 95 x 1.03 = 97.85 -> 100
        self.assertAlmostEqual(got["atm"]["res"]["d1"], 1.2 * 0.95 / 1.05 - 1, places=4)
        self.assertEqual(len(dw.groups(rep["rows"])["抄底失败（低开 2%+、收盘低于开盘）· 平值"]), 1)


class EarningsForecast(unittest.TestCase):
    def test_due_reports_implied_move_baseline_and_scoring(self):
        from unittest import mock
        from hero import research_earnings_forecast as ef
        rows = [{"symbol": "NFLX", "reportDate": "2026-10-20", "timeOfTheDay": "post-market"},
                {"symbol": "TMO", "reportDate": "2026-10-21", "timeOfTheDay": "pre-market"},
                {"symbol": "TSLA", "reportDate": "2026-10-21", "timeOfTheDay": "post-market"},   # tomorrow after the close
                {"symbol": "PENY", "reportDate": "2026-10-20", "timeOfTheDay": "post-market"},
                {"symbol": "NOOPT", "reportDate": "2026-10-20", "timeOfTheDay": "post-market"},
                {"symbol": "MNTH", "reportDate": "2026-10-21", "timeOfTheDay": "pre-market"},    # monthly options only
                {"symbol": "BOOM", "reportDate": "2026-10-20", "timeOfTheDay": "post-market"},    # the lookup fails
                {"symbol": "WHEN", "reportDate": "2026-10-20", "timeOfTheDay": ""}]               # no time given
        due = ef.due(rows, date(2026, 10, 20))
        self.assertEqual([(r["symbol"], r["reaction_day"]) for r in due],
                         [("NFLX", "2026-10-21"), ("TMO", "2026-10-21"), ("PENY", "2026-10-21"), ("NOOPT", "2026-10-21"),
                          ("MNTH", "2026-10-21"), ("BOOM", "2026-10-21")])
        asked = {}

        class Client:
            def stock_snapshots(self, syms):
                px = {"NFLX": 70.0, "TMO": 690.0, "PENY": 3.0, "NOOPT": 50.0, "MNTH": 40.0, "BOOM": 20.0}
                return {s: {"latestTrade": {"p": px[s]}} for s in syms if s in px}

            def option_contracts(self, und, **kw):
                asked[und] = kw
                if und == "NOOPT":
                    return []
                if und == "BOOM":
                    raise RuntimeError("429")
                spot = {"NFLX": 70.0, "TMO": 690.0, "MNTH": 40.0}[und]
                exp = "2026-11-20" if und == "MNTH" else "2026-10-23"
                return [{"symbol": f"{und}{t[0]}{k}", "type": t, "strike_price": str(k), "expiration_date": exp}
                        for k in (round(spot * 0.98), round(spot)) for t in ("call", "put")]

            def option_snapshots(self, syms):
                return {s: {"latestQuote": {"bp": 3.0, "ap": 3.2}} for s in syms}

        with mock.patch.object(ef, "history", lambda: {"NFLX": [0.15] * 8}):
            rep = ef.forecast(Client(), rows, date(2026, 10, 20))
        got = {r["symbol"]: r for r in rep["reports"]}
        self.assertEqual(set(got), {"NFLX", "TMO", "MNTH"})
        self.assertEqual(asked["MNTH"]["expiration_date_lte"], "2026-12-05")   # looks 45 days past the reaction day
        self.assertEqual(rep["skipped"], {"no_price": 0, "under_5": 1, "no_options": 1, "error": 1})
        self.assertEqual(rep["skipped_symbols"], {"under_5": ["PENY"], "no_options": ["NOOPT"], "error": ["BOOM"]})
        self.assertEqual(rep["calendar_no_time"], ["WHEN"])
        self.assertEqual((got["NFLX"]["weekly"], got["MNTH"]["weekly"], got["MNTH"]["expiry_days"]), (True, False, 30))
        self.assertAlmostEqual(got["NFLX"]["implied_move"], round(6.2 / 70, 4))
        self.assertEqual((got["NFLX"]["p_big"], got["TMO"]["p_big"]), (0.51, 0.40))   # its own reactions run hot
        md = ef.markdown(rep)
        self.assertIn("（月，2026-11-20）", md)
        self.assertIn("45 天内没有期权：NOOPT", md)
        self.assertIn("日历没写盘前盘后（没记）：WHEN", md)
        bars = [{"t": "2026-10-20T04:00:00Z", "o": 70, "c": 70}, {"t": "2026-10-21T04:00:00Z", "o": 63, "c": 61.6}]
        res = ef.score_row(got["NFLX"], bars)
        self.assertEqual((res["gap"], res["move"], res["big"], res["up"], res["up_close"]), (-0.1, -0.12, True, False, False))
        # direction is judged at the open: a gap up that closes red counts as up
        flip = ef.score_row(got["NFLX"], [{"t": "2026-10-20T04:00:00Z", "o": 70, "c": 70},
                                          {"t": "2026-10-21T04:00:00Z", "o": 72, "c": 69}])
        self.assertEqual((flip["up"], flip["up_close"]), (True, False))
        mres = ef.score_row(got["MNTH"], [{"t": "2026-10-20T04:00:00Z", "o": 40, "c": 40},
                                          {"t": "2026-10-21T04:00:00Z", "o": 44, "c": 44}])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            got["NFLX"]["result"] = res
            got["MNTH"]["result"] = mres
            (root / "2026-10-20.json").write_text(json.dumps({"date": "2026-10-20", "reports": [got["NFLX"], got["MNTH"]]}))
            (root / "2026-10-20.judgment.json").write_text(json.dumps({"date": "2026-10-20", "written": "2026-10-20T15:40-04:00",
                                                                     "calls": [{"symbol": "NFLX", "direction": "down", "big": True,
                                                                                "confidence": 0.6, "why": "x"}]}))
            md = ef.scorecard(root)
            self.assertIn("| 方向（按开盘） | 1 | 100% |", md)
            self.assertIn("| 方向（按收盘，参考） | 1 | 100% |", md)
            self.assertIn("| 对 / 对 |", md)
            self.assertIn("| 一周内到期的全部 | 1 | 100% | 51% |", md)   # the monthly straddle is kept apart
            self.assertIn("| 月度到期（隐含偏大，只作参考） | 1 | ", md)
            self.assertIn("| 按判断方向，全部 | 1 | 100% | +10.00% | +10.00% | +20.00% |", md)   # a down call, -10% gap
            self.assertIn("| 对照：全部财报都买（不看判断） | 2 | 50% | +0.00% |", md)             # -10% and +10%
            # in after the 16:00 ET cutoff (or with no time): kept on file, not scored
            for written in ("2026-10-20T16:05-04:00", None):
                (root / "2026-10-20.judgment.json").write_text(json.dumps({"date": "2026-10-20", "written": written, "calls": [
                    {"symbol": "NFLX", "direction": "down", "big": True, "confidence": 0.6, "why": "x"}]}))
                md = ef.scorecard(root)
                self.assertIn("这次有 1 条晚交，没算。", md)
                self.assertNotIn("| 方向（按开盘） |", md)
        self.assertTrue(ef.on_time("2026-10-20T19:59:00+00:00", "2026-10-20"))    # 15:59 ET
        self.assertFalse(ef.on_time("2026-10-21T06:30-04:00", "2026-10-20"))     # after a 06:00 pre-market report


class EarningsForecastCalendarChecks(unittest.TestCase):
    def test_disputed_rows_are_left_out_and_confirmed_dates_marked(self):
        from hero import research_earnings_forecast as ef
        rows = [{"symbol": "GE", "reportDate": "2026-10-20", "timeOfTheDay": "pre-market", "checked": "nasdaq"},
                {"symbol": "NOC", "reportDate": "2026-10-20", "timeOfTheDay": "pre-market"},
                {"symbol": "MAT", "reportDate": "2026-10-20", "timeOfTheDay": "", "disputed": True}]

        class Client:
            def stock_snapshots(self, syms):
                return {s: {"latestTrade": {"p": 100.0}} for s in syms}

            def option_contracts(self, und, **kw):
                return [{"symbol": f"{und}{t[0]}", "type": t, "strike_price": "100", "expiration_date": "2026-10-23"}
                        for t in ("call", "put")]

            def option_snapshots(self, syms):
                return {s: {"latestQuote": {"bp": 2.0, "ap": 2.2}} for s in syms}

        ys = {"GE": {"date": "2026-10-20", "confirmed": True}, "NOC": {"date": "2026-10-26", "confirmed": True}}
        rep = ef.forecast(Client(), rows, date(2026, 10, 19), confirmed=lambda syms: ys)
        got = {r["symbol"]: r for r in rep["reports"]}
        self.assertEqual(set(got), {"GE", "NOC"})
        self.assertEqual(rep["calendar_disputed"], ["MAT"])
        self.assertEqual(rep["calendar_no_time"], [])
        self.assertTrue(got["GE"]["confirmed"])
        self.assertEqual((got["NOC"]["confirmed"], got["NOC"]["yahoo_date"]), (False, "2026-10-26"))
        md = ef.markdown(rep)
        self.assertIn("| GE | 盘前 | 2026-10-20 | ✓ |", md)
        self.assertIn("估计（Yahoo：2026-10-26）", md)
        self.assertIn("Nasdaq 没有的（日期可能过时，没记）：MAT", md)
        # Yahoo unreachable: nothing marked, nothing breaks
        rep = ef.forecast(Client(), rows, date(2026, 10, 19), confirmed=lambda syms: 1 / 0)
        self.assertNotIn("confirmed", rep["reports"][0])


class EarningsForecastEveryCall(unittest.TestCase):
    def test_from_10_20_every_report_needs_a_call_and_missing_ones_count_as_wrong(self):
        from hero import research_earnings_forecast as ef
        res = lambda up: {"gap": 0.01 if up else -0.01, "move": 0.0, "big": False, "up": up, "up_close": up}
        rep = {"date": "2026-10-20", "reports": [
            {"symbol": s, "time": "pre-market", "implied_move": 0.05, "weekly": True, "p_big": 0.4, "result": res(up)}
            for s, up in (("AAA", True), ("BBB", False), ("CCC", True), ("DDD", True))]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "2026-10-20.json").write_text(json.dumps(rep))
            (root / "2026-10-20.judgment.json").write_text(json.dumps({"date": "2026-10-20", "written": "2026-10-20T15:30-04:00",
                "calls": [{"symbol": "AAA", "direction": "up", "big": False, "confidence": 0.6},
                          {"symbol": "BBB", "direction": "up", "big": False, "confidence": 0.5},
                          {"symbol": "CCC", "direction": "none", "big": False, "confidence": 0.3}]}))   # DDD: no call
            md = ef.scorecard(root)
        self.assertIn("| 方向按开盘，全部财报（没给或晚交算错） | 4 | 25% |", md)   # AAA right; BBB wrong; CCC, DDD missing
        self.assertIn("| 给了涨跌的比例 | 4 | 50% |", md)
        self.assertIn("| 方向（按开盘） | 2 | 50% |", md)


class ResearchEarningsControl(unittest.TestCase):
    def test_picks_controls_and_blocked_nights(self):
        from hero import research_earnings_control as rc
        days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(500)]
        days = [d for d in days if date.fromisoformat(d).weekday() < 5]
        flat = lambda gap_day=None, gap=0.0: {d: {"o": 100.0 * (1 + (gap if d == gap_day else 0)), "c": 100.0} for d in days}
        r_day = days[300]                                       # AAA reacts here: +10% at the open
        bars = {"AAA": flat(r_day, 0.10), "BBB": flat(r_day, 0.02), "CCC": flat()}
        bars["CCC"] = {d: {"o": 100.0 + i, "c": 100.0 + i} for i, d in enumerate(days)}   # strong momentum, no report
        events = [{"symbol": "AAA", "reaction_day": r_day, "timing": "after close", "mom126": 0.5},
                  {"symbol": "BBB", "reaction_day": r_day, "timing": "after close", "mom126": 0.1}]
        picks = rc.weekly_picks(events, bars)
        self.assertEqual([(p["symbol"], p["entry"], round(p["ret"], 4)) for p in picks], [("AAA", days[299], 0.1)])
        block = {s: rc.blocked([e for e in events if e["symbol"] == s], sorted(b)) for s, b in bars.items()}
        self.assertEqual(block["AAA"], {days[298], days[299], days[300]})
        before = rc.same_name(picks, bars, block, -5)
        self.assertEqual((before[0]["entry"], before[0]["ret"]), (days[294], 0.0))
        self.assertEqual(rc.same_name(picks, bars, block, 1), [])          # the reaction day's night is blocked
        mom = rc.momentum_pick(picks, bars, block)
        self.assertEqual(mom[0]["symbol"], "CCC")                           # AAA / BBB are blocked that night
        v, dd = rc.compound([0.1, -0.5, 0.2], 2)
        self.assertAlmostEqual(v, 0.0)                                      # a -50% night at 2x wipes the ledger
        self.assertEqual(dd, 1.0)
        v, dd = rc.compound([0.1, -0.05], 1)
        self.assertAlmostEqual(v, 500 * 1.1 * 0.95)
        self.assertAlmostEqual(dd, 0.05)


class EarningsForecastAroundTheReport(unittest.TestCase):
    def test_before_open_report_is_judged_against_the_last_trade_before_it(self):
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        from hero import research_earnings_forecast as ef
        et = ZoneInfo("America/New_York")
        row = {"symbol": "GE", "time": "pre-market", "report_date": "2026-10-20", "reaction_day": "2026-10-20",
               "implied_move": 0.06}
        # 8-K filings: last quarter's, and this one at 06:31 ET
        filed = [dt(2026, 7, 17, 6, 30, tzinfo=et), dt(2026, 10, 20, 6, 31, tzinfo=et)]
        rel = ef.release_time(row, filed)
        self.assertEqual(rel, dt(2026, 10, 20, 6, 31, tzinfo=et))
        utc = lambda h, m, d=20: f"2026-10-{d:02d}T{h + 4:02d}:{m:02d}:00Z"   # EDT -> UTC
        mins = [{"t": utc(19, 50, 19), "c": 101.0},        # 10-19 after hours
                {"t": utc(4, 10), "c": 103.0},             # 10-20 pre-market drift, before the report
                {"t": utc(5, 59), "c": 104.0},             # the last trade more than 30 minutes before the filing
                {"t": utc(6, 20), "c": 104.5},             # inside the 30 minutes (the release may already be out)
                {"t": utc(7, 31), "c": 108.0}]             # an hour after
        daily = [{"t": "2026-10-19T04:00:00Z", "o": 99, "c": 100.0}, {"t": "2026-10-20T04:00:00Z", "o": 103.0, "c": 97.0}]
        res = ef.score_row(row, daily, mins, rel)
        self.assertEqual((res["before"], res["gap"], res["gap_pre"], res["after_1h"]), (104.0, 0.03, -0.0096, 0.0385))
        self.assertFalse(res["up"])          # opened above Friday's close but below the price right before the report
        self.assertIn("财报前最后成交 10-20 05:59", res["before_src"])
        # an after-close report lands after 16:00: the price to beat is the regular close
        post = {**row, "time": "post-market", "report_date": "2026-10-19"}
        res = ef.score_row(post, daily, mins, dt(2026, 10, 19, 16, 5, tzinfo=et))
        self.assertEqual((res["before"], res["before_src"], res["gap_pre"]), (100.0, "前收", 0.03))
        self.assertTrue(res["up"])
        # no filing time found: the previous close
        self.assertEqual(ef.score_row(row, daily, mins, None)["before"], 100.0)
        # owner, 10-22 (META +5% after hours, -6% at the open): an after-close report with no filing time still gets
        # its first reaction, read an hour after 16:05 on the report date
        mins2 = mins + [{"t": utc(17, 10, 19), "c": 105.0}, {"t": utc(19, 55, 19), "c": 102.0}]
        res = ef.score_row(post, daily, mins2, None)
        self.assertEqual((res["before"], res["after_1h"], res["ah_close"], res["gap_pre"]), (100.0, 0.05, 0.02, 0.03))
        self.assertIsNone(ef.score_row(row, daily, mins2, rel)["ah_close"])   # a before-open report has no 20:00 point

    def test_score_due_reads_filings_and_minute_bars(self):
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        from hero import research_earnings_forecast as ef
        et = ZoneInfo("America/New_York")
        asked = []

        class Client:
            def daily_bars(self, syms, start, **kw):
                return {"GE": [{"t": "2026-10-19T04:00:00Z", "o": 99, "c": 100.0},
                               {"t": "2026-10-20T04:00:00Z", "o": 103.0, "c": 97.0}]}

            def stock_bars(self, syms, start, end, **kw):
                asked.append((syms, start, end, kw.get("feed")))
                return {"GE": [{"t": "2026-10-20T09:59:00Z", "c": 104.0}]}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "2026-10-19.json").write_text(json.dumps({"date": "2026-10-19", "reports": [
                {"symbol": "GE", "time": "pre-market", "report_date": "2026-10-20", "reaction_day": "2026-10-20",
                 "implied_move": 0.06}]}))
            ef.score_due(Client(), date(2026, 10, 21), root,
                         sec=lambda syms, since: {"GE": [dt(2026, 10, 20, 6, 31, tzinfo=et)]})
            r = json.loads((root / "2026-10-19.json").read_text())["reports"][0]["result"]
        self.assertEqual(asked, [(["GE"], "2026-10-19T15:59:00-04:00", "2026-10-20T09:31:00-04:00", "sip")])
        self.assertEqual((r["before"], r["release"], r["up"]), (104.0, "2026-10-20T06:31-04:00", False))


class DiscordPost(unittest.TestCase):
    def test_page_to_cards_tables_footer_and_refusals(self):
        from hero import discord_post as dp
        page = ("# 财报预测 10-19\n\n今晚和明早。\n\n## 判断\n\n| 股票 | 定价 | 方向 |\n|---|---|---|\n"
                "| GE | 6.0% | 涨 |\n| HAL | 5.2% | 不判 |\n\n## 记分\n\n还没有。\n")
        cs = dp.cards(page, 1)
        self.assertEqual([c["title"] for c in cs], ["财报预测 10-19", "判断", "记分"])
        self.assertEqual(cs[1]["description"], "**股票 · 定价 · 方向**\nGE · 6.0% · 涨\nHAL · 5.2% · 不判\n")
        self.assertTrue(all(c["footer"]["text"] == dp.FOOTER for c in cs))
        for ok in ("PANW261023C00200000", "SPY261019P00762000", "AKAM261023P00050000"):
            self.assertIsNone(dp.unsafe(ok))
        for bad in ("https://discord.com/api/webhooks/1/x", "00000000-1111-4222-8333-444444444444", "PKABCDEFGHIJ12345678"):
            self.assertTrue(dp.unsafe(bad))
        self.assertEqual(dp._url("https://h.invalid/api/webhooks/1/t?thread_id=5", "9"),
                         "https://h.invalid/api/webhooks/1/t/messages/9?thread_id=5")

    def test_send_then_edit_in_place_then_delete(self):
        from unittest import mock
        from hero import discord_post as dp
        server, calls = {}, []

        def create(url, pl):
            mid = str(len(server) + 100)
            server[mid] = pl
            calls.append(("create", mid))
            return mid

        def edit(url, mid, pl):
            calls.append(("edit", mid))
            if mid not in server:
                return None                              # deleted by hand in Discord
            server[mid] = pl
            return True

        def delete(url, mid):
            calls.append(("delete", mid))
            server.pop(mid, None)
            return True

        long_page = "# 长页\n\n" + "\n\n".join(f"## 段 {i}\n\n" + "x" * 3000 for i in range(4))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "posts" / "earnings").mkdir(parents=True)
            page = root / "posts" / "earnings" / "a.md"
            page.write_text(long_page)
            (root / "posts" / "earnings" / "b.md").write_text("# b\n\n00000000-1111-4222-8333-444444444444\n")
            (root / "notes.md").write_text("# n\n")
            rec = root / "posts" / ".sent" / "earnings" / "a.md.json"
            with mock.patch.object(dp, "ROOT", root), mock.patch.object(dp, "POSTS", root / "posts"), \
                    mock.patch.object(dp, "create", create), mock.patch.object(dp, "edit", edit), \
                    mock.patch.object(dp, "delete", delete), \
                    mock.patch.dict(os.environ, {"DC_WEBHOOK_EARNINGS": "https://h.invalid/hook"}, clear=False):
                self.assertEqual(dp.main(["earnings", "posts/earnings/a.md"]), 0)
                first = json.loads(rec.read_text())["ids"]
                self.assertGreater(len(first), 1)                                 # long cards: several messages
                calls.clear()
                page.write_text("# 短页\n\n改过了。\n")
                self.assertEqual(dp.main(["earnings", "posts/earnings/a.md"]), 0)   # same page again: edit in place
                self.assertEqual(calls, [("edit", first[0])] + [("delete", m) for m in first[1:]])
                self.assertEqual(json.loads(rec.read_text())["ids"], [first[0]])
                self.assertIn("edited", json.loads(rec.read_text()))
                server.clear()                                                     # someone deleted it in Discord
                calls.clear()
                self.assertEqual(dp.main(["earnings", "posts/earnings/a.md"]), 0)
                self.assertEqual([c[0] for c in calls], ["edit", "create"])
                self.assertEqual(dp.main(["--delete", "earnings", "posts/earnings/a.md"]), 0)
                self.assertFalse(rec.exists())
                self.assertEqual(server, {})
                self.assertEqual(dp.main(["earnings", "posts/earnings/b.md"]), 1)      # holds an id: refused
                self.assertEqual(dp.main(["earnings", "notes.md"]), 2)                 # only pages under posts/
                self.assertEqual(dp.main(["earnings", "posts/../notes.md"]), 2)
                self.assertEqual(dp.main(["earnings", "posts/.sent/earnings/x.md"]), 2)
                os.environ.pop("DC_WEBHOOK_DAILY", None)
                self.assertEqual(dp.main(["daily", "posts/earnings/a.md"]), 0)        # no secret yet: skipped

    def test_chinese_pages_kept_under_the_byte_cap(self):
        from hero import discord_post as dp
        page = "# 财报\n\n## 理由\n\n" + "\n".join(f"- 第 {i} 只：" + "门槛和判断" * 30 for i in range(20)) + "\n\n## 记分\n\n还没有。\n"
        cs = dp.cards(page, 1)
        self.assertEqual([c["title"] for c in cs], ["理由", "记分"])   # under 3,800 characters: one card by the old rule
        ms = dp.messages(cs)
        embeds = [e for m in ms for e in m["embeds"]]
        self.assertEqual([e["title"] for e in embeds], ["理由", "理由（续）", "记分"])   # ...but too many bytes: split
        self.assertTrue(all(dp.nbytes(e) <= dp.CARD_BYTES for e in embeds))
        self.assertTrue(all(dp.nbytes(m) <= dp.MESSAGE_BYTES for m in ms))
        self.assertEqual(len(ms), 2)
        self.assertEqual("\n".join(e["description"] for e in embeds[:2]), cs[0]["description"])   # nothing lost

    def test_rate_limit_waits_and_errors_are_printed(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from hero import discord_post as dp

        class R:
            def __init__(self, code, body):
                self.status_code, self.body, self.headers = code, body, {}

            def json(self):
                return self.body

        replies = [R(429, {"retry_after": 0.01}), R(200, {"id": "7"}),        # create: limited once, then sent
                   R(400, {"message": "Invalid Form Body"}),                   # edit: refused
                   R(404, {"message": "Unknown Message"}),                     # edit: deleted by hand
                   R(500, {}), R(500, {})]                                     # delete: Discord's error twice
        out = io.StringIO()
        with mock.patch.object(dp.requests, "request", side_effect=replies) as req, \
                mock.patch.object(dp.time, "sleep") as nap, redirect_stdout(out):
            self.assertEqual(dp.create("https://h.invalid/api/webhooks/1/t", {"embeds": []}), "7")
            self.assertIs(dp.edit("https://h.invalid/api/webhooks/1/t", "5", {"embeds": []}), False)
            self.assertIsNone(dp.edit("https://h.invalid/api/webhooks/1/t", "6", {"embeds": []}))
            self.assertFalse(dp.delete("https://h.invalid/api/webhooks/1/t", "8"))
        self.assertEqual(req.call_count, 6)
        self.assertEqual([c.args for c in nap.call_args_list], [(0.5,), (2,)])
        self.assertIn("delete 8 -> HTTP 500", out.getvalue())
        self.assertIn("edit 5 -> HTTP 400 Invalid Form Body", out.getvalue())
        self.assertNotIn("webhooks", out.getvalue())                             # the address never printed


class FeaturePages(unittest.TestCase):
    def test_one_page_per_direction_with_ledgers_trades_and_why(self):
        from hero import feature_pages as fp
        self.assertEqual([fp.family(x) for x in ("spy0dte-500", "spy0dte-old-plan-1000", "gap-plan-500", "earn-opt-plan-500",
                                                 "spy0dte-near-1000", "oneshot-500")],
                         ["spy0dte", "spy0dte", "gap", "earn", "spy0dte-near", "oneshot"])
        specs = [{"id": "spy0dte-500", "kind": "zdte", "start": 500, "name": "SPY 末日单", "offset": 0.003},
                 {"id": "spy0dte-1000", "kind": "zdte", "start": 1000, "name": "SPY 末日单", "offset": 0.003},
                 {"id": "spy0dte-old-plan-500", "kind": "zdte", "start": 500, "name": "旧规则（只演算）", "plan_only": True},
                 {"id": "gap-500", "kind": "gap", "start": 500, "name": "低开反弹；停开新仓", "stop_entries": True},
                 {"id": "sniper-500", "kind": "sniper", "start": 500, "name": "狙击", "stop_if_losing": {"after": 2}},
                 {"id": "oneshot-500", "kind": "oneshot", "start": 500, "name": "一把到（只演算）", "plan_only": True}]
        closed = {"tid": "t1", "sym": "SPY261020C00773000", "asset": "option", "qty": 4, "cost": 0.11, "paid": 44.0,
                  "opened": "2026-10-20 10:00", "closed": "2026-10-20 10:20", "proceeds": 132.0, "ret": 2.0,
                  "why": "顺势买看涨 773", "how": "3 倍止盈", "sell_cid": "sl-secret-cid"}
        held = {"tid": "t2", "sym": "SPY261021C00778000", "asset": "option", "qty": 2, "cost": 0.10, "paid": 20.0,
                "opened": "2026-10-21 10:00", "why": "又买"}
        lost = lambda tid: {"tid": tid, "sym": "CLF261023C00014500", "asset": "option", "qty": 1, "cost": 1.0, "paid": 100.0,
                            "opened": "2026-10-1" + tid[-1] + " 09:30", "closed": "2026-10-1" + tid[-1] + " 15:00",
                            "proceeds": 10.0, "ret": -0.9}
        live = {"sleeves": {"spy0dte-500": {"start": 500, "cash": 568.0, "round": 1, "open": [held], "closed": [closed]},
                            "gap-500": {"start": 500, "cash": 500, "open": [], "closed": []},
                            "sniper-500": {"start": 500, "cash": 320, "open": [], "closed": [lost("s5"), lost("s6")]}}}
        plan = {"sleeves": {"spy0dte-old-plan-500": {"start": 500, "cash": 450.0, "open": [],
                                                     "closed": [dict(closed, sim=True, proceeds=-0.0, ret=-1.0)]}}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sub in ("config", "journal-s500", "research"):
                (root / sub).mkdir()
            (root / "config" / "s500.json").write_text(json.dumps({"sleeves": {"list": specs, "fraction": 0.5, "zero_line": 0.1}}))
            j = root / "journal-s500"
            (j / "sleeves.json").write_text(json.dumps(live))
            (j / "sleeves_plan.json").write_text(json.dumps(plan))
            (j / "snapshot.json").write_text(json.dumps({"ts": "2026-10-21T19:30:00+00:00", "positions": [
                {"symbol": "SPY261021C00778000", "current_price": "0.30"}]}))
            (j / "trades.jsonl").write_text("\n".join(json.dumps(e) for e in [
                {"kind": "sleeve_skip", "sleeve": "spy0dte-500", "ts": "2026-10-21T14:00:00+00:00", "why": "离开盘不到 0.5%，不做"},
                {"kind": "sleeve_plan", "dry_run": True, "sleeve": "oneshot-500", "ts": "2026-10-21T14:00:00+00:00",
                 "why": "oneshot-500（演算）：QQQ 看涨 731"},
                {"kind": "order", "symbol": "SPY", "ts": "2026-10-21T14:00:00+00:00"}]) + "\n")
            (root / "research" / "2026-10-21-plan-results.json").write_text(json.dumps({"rows": [
                {"day": "2026-10-14", "at": "10:00", "sleeve": "oneshot-500", "sym": "QQQ261014C00700000", "qty": 10,
                 "paid": 500.0, "got": 0.0, "pnl": -500.0, "ret": -1.0, "how": "到期作废", "src": "backfill"},
                {"day": "2026-10-15", "at": "10:00", "sleeve": "oneshot-500", "sym": "QQQ261015C00700000", "qty": 10,
                 "paid": 500.0, "got": 5000.0, "pnl": 4500.0, "ret": 9.0, "how": "15:50 卖", "src": "backfill"},
                {"day": "2026-10-21", "sleeve": "oneshot-500", "sym": "X", "src": "engine"}],
                "ledgers": {"oneshot-500": {"n": 2, "pnl": -463.0, "cash": 500.0, "zeros": 1, "heroes": 0, "topups": 463.0, "skipped": 0}}}))
            data = fp.collect(root)
            fam = data["families"]
            self.assertEqual(set(fam), {"spy0dte", "gap", "sniper", "oneshot"})
            spy = fam["spy0dte"]
            self.assertEqual([r["id"] for r in spy["ledgers"]], ["spy0dte-500", "spy0dte-1000", "spy0dte-old-plan-500"])
            l500 = spy["ledgers"][0]
            self.assertAlmostEqual(l500["now"], 60.0)                     # 2 contracts x $0.30 x 100
            self.assertAlmostEqual(l500["value"], 628.0)
            self.assertEqual((l500["realized"], l500["trades"], l500["wins"], l500["open"]), (88.0, 1, 1, 1))
            self.assertEqual(spy["ledgers"][1]["value"], 1000)             # no ledger yet: its starting state
            self.assertEqual(spy["ledgers"][2]["realized"], -44.0)         # plan twin: only sim trades
            self.assertEqual((spy["status"], fam["gap"]["status"], fam["oneshot"]["status"]),
                             ("实盘", "实盘已停开新仓", "只演算（不下单）"))
            self.assertEqual(fam["sniper"]["tone"], "stopped")             # 2 finished, net -$180
            self.assertEqual(spy["curve"]["spy0dte-500"], [("2026-10-20", 88.0)])
            self.assertEqual([e["why"] for e in spy["events"]], ["离开盘不到 0.5%，不做"])
            self.assertEqual(len(fam["oneshot"]["backfill"]), 2)            # engine rows are not backfill
            paths = fp.write(root / "site" / "f", root)
            names = sorted(p.name for p in paths)
            self.assertEqual(names, ["gap.html", "index.html", "oneshot.html", "sniper.html", "spy0dte.html"])
            index = (root / "site" / "f" / "index.html").read_text()
            page = (root / "site" / "f" / "spy0dte.html").read_text()
            one = (root / "site" / "f" / "oneshot.html").read_text()
        self.assertIn('href="spy0dte.html"', index)
        self.assertIn("2 笔赚 1", index)                                    # backfill as counts, never a summed $
        self.assertIn("按账本", index)                                       # ...and the ledger played in order
        self.assertIn("-$463</span>（归零 1）", index)
        self.assertIn("SPY 10/20 773C", page)
        self.assertIn("顺势买看涨 773", page)
        self.assertIn("持有中", page)
        self.assertIn("离开盘不到 0.5%，不做", page)
        self.assertIn('href="../s500.html"', page)
        self.assertNotIn("sl-secret-cid", page)                            # client order ids stay out of the page
        self.assertIn("中位收益", one)
        self.assertIn("归零 1 次", one)
        self.assertIn("QQQ 看涨 731", one)


class EarningsPage(unittest.TestCase):
    def test_calendar_forecast_and_holdings(self):
        from hero import earnings_page as ep
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sub in ("data", "journal", "journal-s500", "research/earnings-forecast"):
                (root / sub).mkdir(parents=True)
            (root / "data" / "earnings.json").write_text(json.dumps({"fetched_at": "2026-10-19T09:00", "reports": {
                "META": [{"date": "2026-10-21", "time": "post-market", "confirmed": True}],
                "GE": [{"date": "2026-10-20", "time": "pre-market", "disputed": True}],
                "AMZN": [{"date": "2026-10-29", "time": "unknown"}], "OLD": [{"date": "2026-10-01", "time": "post-market"}],
                "FAR": [{"date": "2026-12-20", "time": "post-market"}]}}))
            (root / "journal" / "snapshot.json").write_text(json.dumps({"positions": [{"symbol": "META"}]}))
            (root / "journal-s500" / "snapshot.json").write_text(json.dumps({"positions": [{"symbol": "GE261023C00320000"}]}))
            fc = root / "research" / "earnings-forecast"
            (fc / "2026-10-19.json").write_text(json.dumps({"date": "2026-10-19", "reports": [
                {"symbol": "GE", "time": "pre-market", "report_date": "2026-10-20", "reaction_day": "2026-10-20", "spot": 313.2,
                 "implied_move": 0.06, "weekly": True, "prior_avg_abs": 0.053, "p_big": 0.4,
                 "result": {"gap": 0.03, "gap_pre": -0.01, "move": -0.02, "big": False, "up": False}},
                {"symbol": "CBSH", "time": "pre-market", "report_date": "2026-10-20", "reaction_day": "2026-10-20", "spot": 55.2,
                 "implied_move": None, "weekly": False, "prior_avg_abs": None, "p_big": 0.4}]}))
            (fc / "2026-10-19.judgment.json").write_text(json.dumps({"date": "2026-10-19", "calls": [
                {"symbol": "GE", "direction": "up", "big": False, "confidence": 0.5}]}))
            (fc / "scorecard.md").write_text("# 记分卡\n\n| 组 | 次数 |\n|---|---|\n| 全部 | 1 |\n")
            data = ep.collect(root, date(2026, 10, 19))
            self.assertEqual(data["hold"], {"META": ["主账户"], "GE": ["多账本"]})
            self.assertEqual([r["date"] for r in data["pool"]], ["2026-10-20", "2026-10-21", "2026-10-29"])
            page = ep.render(data)
        self.assertIn("META✓★", page)                                  # confirmed and held
        self.assertIn("GE⚠★", page)
        self.assertIn("10-20 周二", page)
        self.assertIn("涨 · 超过定价不会 · 信心 0.50", page)
        self.assertIn("开盘 -1.0% · 收盘 -2.0% · 方向错", page)       # judged at the open against the price before
        self.assertNotIn("—（月）", page)
        self.assertIn("<td>全部</td>", page)


class ResearchSniperFactors(unittest.TestCase):
    def test_breakout_compares_the_open_on_the_split_adjusted_scale(self):
        from hero.research_sniper_factors2 import tag
        st = {"swing20": 0.02, "swing120": 0.02, "adv20": 1e6, "ma50": 95.0, "hi20": 104.0, "lo20": 90.0,
              "prev_close": 100.0, "prev_day": "2024-03-04"}
        # a 10:1 split comes later: the raw minute open is ten times the adjusted daily scale
        mins = {"09:30": {"o": 970.0, "c": 975.0, "v": 1000}, "09:34": {"o": 975.0, "c": 980.0, "v": 1000}}
        row = {"und": "AAA", "day": "2024-03-05", "gap": -0.03, "kind": "C", "res": {}}
        self.assertFalse(tag(row, st, mins, [], None)["breakout"])                      # 97 adjusted, below the high
        self.assertTrue(tag(dict(row, gap=0.05), st, mins, [], None)["breakout"])       # 105 > 104
        self.assertTrue(tag(dict(row, gap=-0.12, kind="P"), st, mins, [], None)["breakout"])   # 88 < 90


class ResearchCpiOpex(unittest.TestCase):
    def test_expiry_is_the_third_friday_or_the_day_before_a_holiday(self):
        from hero.research_cpi_opex import expiries
        days = [d.isoformat() for d in (date(2025, 4, 1) + timedelta(days=k) for k in range(80))
                if d.weekday() < 5 and d.isoformat() not in ("2025-04-18", "2025-05-26", "2025-06-19")]
        self.assertEqual(expiries(days), ["2025-04-17", "2025-05-16"])        # Good Friday -> Thursday
        self.assertEqual(expiries(days + ["2025-06-20"]), ["2025-04-17", "2025-05-16", "2025-06-20"])
        # data ending 2025-06-18: June's expiry (the 20th) has not come, so no Thursday stands in for it
        self.assertNotIn("2025-06-18", expiries(days))

    def test_cpi_week_rows_and_groups(self):
        from hero import research_cpi_opex as co
        self.assertEqual(co.cpi_in_week("2024-02-16", ["2024-02-13", "2024-03-12"]), "2024-02-13")
        self.assertIsNone(co.cpi_in_week("2024-01-19", ["2024-01-11"]))           # the week before
        days = [d for d in (date(2024, 1, 1) + timedelta(days=k) for k in range(70)) if d.weekday() < 5]
        bars = {d.isoformat(): {"o": 100.0, "c": 100.0 + (-1 if d.isoformat() == "2024-02-16" else 0.5) * (k % 2),
                                "div": 0.0} for k, d in enumerate(days)}
        rows = co.rows_for(bars, ["2024-02-13", "2024-03-12"], "2024-03-10")
        g = co.groups(rows)
        self.assertEqual([r["day"] for r in g["CPI 周的到期日：全部"]], ["2024-02-16"])
        self.assertEqual([r["day"] for r in g["对照：其他到期日"]], ["2024-01-19"])   # March's expiry is past "today"
        self.assertAlmostEqual(co.binom_tail(8, 13, 0.5), sum(math.comb(13, j) for j in range(8, 14)) / 2 ** 13)
        self.assertEqual(co.binom_tail(0, 4000, 0.45), 1.0)


class ResearchZdteMinMove(unittest.TestCase):
    def test_run_records_the_move_and_groups_by_it(self):
        from unittest import mock
        from hero import research_zdte_minmove as mm

        def minute(day, hhmm, px):   # an ET minute on a 2026-10 day (EDT, UTC-4)
            h, m = map(int, hhmm.split(":"))
            return {"t": f"{day}T{h + 4:02d}:{m:02d}:00Z", "o": px, "h": px, "l": px, "c": px, "v": 100}

        class Client:
            def clock(self): return {"is_open": False}

            def stock_bars(self, syms, start, end, feed=None):
                out = {}
                for s, day, ten in (("NVDA", "2026-10-14", 99.88), ("NVDA", "2026-10-15", 101.0)):
                    if start[:10] <= day < end[:10] and s in syms:
                        out.setdefault(s, []).extend([minute(day, "09:30", 100.0), minute(day, "09:35", ten),
                                                      minute(day, "10:00", ten), minute(day, "15:30", ten)])
                return out

            def option_contracts(self, und, status, expiration_date_gte, **kw):
                return [{"symbol": f"{und}{expiration_date_gte}{t}{k}", "expiration_date": expiration_date_gte,
                         "strike_price": str(k), "type": t} for k in (97, 98, 99, 100, 101, 102, 103) for t in ("call", "put")]

            def option_bars(self, syms, start, end, timeframe=None):
                day = start[:10]
                path = [("09:35", 1.0), ("10:00", 1.0), ("10:30", 4.0), ("11:00", 2.0), ("15:30", 1.5)]
                return {s: [minute(day, t, px) for t, px in path] for s in syms}

        with mock.patch.object(mm, "START", "2026-10-01"), mock.patch.object(mm, "NAMES", ("NVDA",)), \
                mock.patch("time.sleep"):
            rep = mm.run(Client())
        got = {r["day"]: r for r in rep["rows"]}
        self.assertAlmostEqual(got["2026-10-14"]["move"]["10:00"], -0.0012, places=5)   # like 10-16: 0.12% below
        r = got["2026-10-14"]["res"]["10:00|0.006"]
        self.assertAlmostEqual(r["take3"], 2.0)
        self.assertAlmostEqual(r["take5_trail"], r["trail2"])          # 5x never came: all on the trail
        self.assertEqual(mm.with_take5({"take5": 4, "trail2": 1.0})["take5_trail"], 2.5)
        g = mm.groups(rep["rows"], "NVDA", "10:00", 0.006, "take3_trail")
        self.assertEqual(len(g["现行：每天都做"]), 2)
        self.assertEqual([d for d, _ in g["不到 0.5% 就不做"]], ["2026-10-15"])    # 1.0% up
        self.assertEqual([d for d, _ in g["　开盘到 10:00 走了 0.1%–0.2%"]], ["2026-10-14"])
        table, md = mm.report(rep)
        self.assertIn("NVDA 末日单", md)


class ResearchZdteCost(unittest.TestCase):
    def minute(self, day, hhmm, px):   # an ET minute on a 2026-10 day (EDT, UTC-4)
        h, m = map(int, hhmm.split(":"))
        return {"t": f"{day}T{h + 4:02d}:{m:02d}:00Z", "o": px, "h": px, "l": px, "c": px, "v": 100}

    def test_play_cost_prices_both_sides(self):
        from hero.research_0dte import et_minutes
        from hero.research_zdte_cost import play_cost
        day = "2026-10-20"
        # 10:00 at $0.50, up to $3.00 (6x), back to $1.20 (40% off the best), then $0.10 at 15:30.
        opt = et_minutes([self.minute(day, t, px) for t, px in
                          (("10:00", 0.5), ("10:20", 3.0), ("11:10", 1.7), ("11:12", 1.2), ("15:30", 0.1))])
        free = play_cost(opt, "10:00", 0.0, 0.0)
        self.assertAlmostEqual(free["take3"], 2.0)                    # 3x on the half
        self.assertAlmostEqual(free["take3_trail"], (2.0 + (1.7 / 0.5 - 1)) / 2)   # 1.70 <= 0.6 x 3.00
        self.assertAlmostEqual(free["hold"], 0.1 / 0.5 - 1)
        dear = play_cost(opt, "10:00", 0.10, 0.10)                    # in at 0.55, out 10% under
        self.assertAlmostEqual(dear["take3_trail"], (2.0 + (1.7 * 0.9 / 0.55 - 1)) / 2, places=4)
        self.assertLess(dear["take3_trail"], free["take3_trail"])
        cheap = et_minutes([self.minute(day, t, px) for t, px in (("10:00", 0.05), ("15:30", 0.05))])
        self.assertAlmostEqual(play_cost(cheap, "10:00", 0.10, 0.10)["hold"], 0.04 / 0.06 - 1, places=4)   # a cent each way

    def test_minutes_by_day_keeps_the_current_month(self):
        from hero.research_0dte_names import minutes_by_day
        minute = self.minute

        class Client:     # like SIP: a request that runs past "now" (10-20) is refused
            def stock_bars(self, syms, start, end, feed=None):
                if end[:10] > "2026-10-20":
                    raise RuntimeError("subscription does not permit querying recent SIP data")
                return {"SPY": [minute(d, "10:00", 1.0) for d in ("2026-09-30", "2026-10-19") if start[:10] <= d < end[:10]]}

        got = minutes_by_day(Client(), "SPY", "2026-09-01", "2026-10-19")
        self.assertEqual(sorted(got), ["2026-09-30", "2026-10-19"])

    def test_run_and_report_with_live_trades(self):
        import tempfile
        from unittest import mock
        from hero import research_zdte_cost as zc
        minute = self.minute

        class Client:
            def stock_bars(self, syms, start, end, feed=None):
                day = "2026-10-16"
                if not (start[:10] <= day < end[:10]):
                    return {}
                return {"SPY": [minute(day, "09:30", 765.67), minute(day, "10:00", 767.58), minute(day, "15:30", 769.0)]}

            def option_contracts(self, und, status, expiration_date_gte, **kw):
                return [{"symbol": f"SPY{k}{t[0].upper()}", "expiration_date": expiration_date_gte, "strike_price": str(k), "type": t}
                        for k in range(760, 776) for t in ("call", "put")]

            def option_bars(self, syms, start, end, timeframe=None):
                path = [("10:00", 0.5), ("10:20", 1.6), ("11:00", 0.9), ("15:30", 0.2)]
                return {s: [minute(start[:10], t, px) for t, px in path] for s in syms}

        with mock.patch.object(zc, "START", "2026-10-01"), mock.patch("time.sleep"):
            rep = zc.run(Client())
        self.assertEqual(len(rep["rows"]), 1)
        r = rep["rows"][0]
        self.assertEqual(r["kind"], "C")
        self.assertEqual(r["syms"][0.003], "SPY770C")                 # first strike 0.3% above 767.58
        self.assertEqual(r["syms"][0.006], "SPY773C")
        self.assertEqual(set(r["res"]["0.003"]), {"0.1/0.1", "0.05/0.05", "0/0.1", "0/0"})
        g = zc.groups(rep["rows"], 0.003, "0/0")
        self.assertEqual(len(g["10:00 离开盘 0.2%–0.3%"]), 1)
        self.assertEqual(len(g["不到 0.5% 就不做"]), 0)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "journal-s500").mkdir()
            (Path(d) / "journal-s500" / "sleeves.json").write_text(json.dumps({"sleeves": {"spy0dte-near-1000": {"closed": [
                {"kind": "zdte", "opened": "2026-10-16 10:00", "paid": 260.0, "ret": 2.0},
                {"kind": "zdte", "opened": "2026-10-16 10:00", "paid": 260.0, "ret": 1.38}]}}}))
            self.assertAlmostEqual(zc.live_trades(Path(d), "spy0dte-near-1000")["2026-10-16"], 1.69)
            table, md = zc.report(json.loads(json.dumps(rep)), Path(d))
        self.assertIn("SPY 末日单 0.3%", md)
        self.assertIn("| 2026-10-16 | +0.25% | SPY770C | +169% |", md)


class EarningsReasons(unittest.TestCase):
    def test_tally_joins_results_and_finds_usable_signs(self):
        from unittest import mock
        from hero import earnings_reasons as er
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            reports, calls, lines = [], [], []
            for i in range(6):
                sym = f"S{i}"
                gap = 0.03 if i < 4 else -0.02                   # S0-S3 open up, S4-S5 down
                res = {"gap_pre": gap, "move": gap * 2, "up": gap > 0} if i < 5 else None   # S5 not scored yet
                reports.append({"symbol": sym, "implied_move": 0.05, **({"result": res} if res else {})})
                calls.append({"symbol": sym, "direction": "up", "confidence": 0.55})
                lines.append({"date": "2026-10-19", "symbol": sym, "last_guide": "raise" if i < 4 else "cut",
                              "run20": -0.08 if i < 3 else 0.06, "eps": "beat", "guide": "raise" if i < 4 else "cut",
                              "tags": ["成本"] if i == 4 else [], "reason": f"r{i}"})
            (folder / "2026-10-19.json").write_text(json.dumps({"date": "2026-10-19", "reports": reports}))
            (folder / "2026-10-19.judgment.json").write_text(json.dumps({"date": "2026-10-19", "calls": calls}))
            lines.append({**lines[0], "reason": "corrected"})           # a later line replaces the earlier one
            (folder / "reasons.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
            got = er.outcomes(folder)[("2026-10-19", "S4")]
            self.assertFalse(got["up"])
            self.assertFalse(got["right"])
            rows = [{**r, **er.outcomes(folder).get((r["date"], r["symbol"]), {})} for r in er.load_reasons(folder)]
            self.assertEqual(len(rows), 6)
            t = dict((v, (n, up)) for v, n, up, _, _ in er.tally([r for r in rows if r.get("gap") is not None], "last_guide"))
            self.assertEqual(t["上调"], (4, 1.0))
            self.assertEqual(t["下调"], (1, 0.0))
            with mock.patch.object(er, "MIN_N", 4):
                rules = er.usable([r for r in rows if r.get("gap") is not None])
            self.assertTrue(any("上次指引「上调」" in x and "判涨" in x for x in rules))
            md = er.render(folder)
            self.assertIn("超预期 + 指引上调", md)
            self.assertIn("| 2026-10-19 | S5 | 涨 | 待记分 |", md)
            self.assertIn("corrected", md)
            self.assertIn("| 成本 | 1 | 0% |", md)


class EarningsFillMissing(unittest.TestCase):
    def test_yahoo_adds_what_both_calendars_missed(self):
        from hero import earnings as earn
        self.assertEqual(earn.yahoo_time(1792526400), "post-market")       # 2026-10-20 20:00 UTC = 16:00 ET
        self.assertEqual(earn.yahoo_time(1792585800), "pre-market")        # 2026-10-21 12:30 UTC = 08:30 ET
        self.assertEqual(earn.yahoo_time(None), "")
        rows = [{"symbol": "NFLX", "reportDate": "2026-10-20", "timeOfTheDay": "post-market"}]
        asked = []
        look = lambda syms: asked.extend(syms) or {"SPCX": {"date": "2026-10-20", "ts": 1792526400, "confirmed": True},
                                                    "LATE": {"date": "2026-11-20", "ts": 1795200000}}
        out, added = earn.fill_missing(rows, date(2026, 10, 20), ["NFLX", "SPCX", "LATE", "SPCX"], lookup=look)
        self.assertEqual(asked, ["LATE", "SPCX"])                          # NFLX is already in the calendar
        self.assertEqual(added, ["SPCX"])                                  # LATE reports outside the window
        self.assertEqual(out[-1], {"symbol": "SPCX", "reportDate": "2026-10-20", "timeOfTheDay": "post-market", "estimate": None,
                                   "fiscalDateEnding": None, "checked": "yahoo", "confirmed": True})

    def test_big_names_for_the_forecast(self):
        from unittest import mock
        from hero import research_earnings_forecast as ef
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            (root / "data" / "universe.json").write_text(json.dumps({"stocks": ["NVDA", "MU"]}))
            (root / "journal").mkdir()
            (root / "journal" / "snapshot.json").write_text(json.dumps({"positions": [{"symbol": "META"}, {"symbol": "XLE261113C00066500"}]}))
            (root / "out").mkdir()
            (root / "out" / "extra_watch.json").write_text(json.dumps({"symbols": ["SPCX"]}))
            client = type("C", (), {"most_actives": lambda self, n: ["TSLA", "BRK.B"]})()
            with mock.patch.object(ef, "OUT", root / "out"):
                got = ef.big_names(client, root)
        self.assertEqual(got, ["META", "MU", "NVDA", "SPCX", "TSLA", "XLE"])     # BRK.B left out (Yahoo symbol differs)


class EarningsProfiles(unittest.TestCase):
    def test_page_joins_the_history_scored_reports_and_notes(self):
        from hero import earnings_profiles as ep
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "research" / "earnings-forecast").mkdir(parents=True)
            ev = [{"symbol": "NFLX", "reaction_day": day, "timing": "after close", "gap": g, "move": g / 2, "implied_move": 0.08}
                  for day, g in (("2026-01-21", -0.054), ("2026-04-17", -0.106), ("2026-07-17", -0.125), ("2025-10-22", 0.03))]
            (root / "research" / "2026-10-02-earnings-reactions.json").write_text(json.dumps({"events": ev}))
            (root / "research" / "earnings-forecast" / "2026-10-20.json").write_text(json.dumps({"date": "2026-10-20", "reports": [
                {"symbol": "NFLX", "time": "post-market", "reaction_day": "2026-10-21", "implied_move": 0.096,
                 "result": {"gap_pre": -0.04, "move": -0.05}},
                {"symbol": "ISRG", "time": "post-market", "reaction_day": "2026-10-21", "implied_move": 0.076}]}))
            (root / "research" / "earnings-forecast" / "companies.json").write_text(json.dumps(
                {"NFLX": {"watch": "收入对自己的预测", "habit": "常常只是符合", "notes": [{"date": "10-20", "text": "x"}]}}, ensure_ascii=False))
            (root / "research" / "earnings-forecast" / "reasons.jsonl").write_text(
                json.dumps({"date": "2026-10-20", "symbol": "NFLX", "reason": "全年落在下沿"}, ensure_ascii=False) + "\n")
            self.assertEqual(ep.top_symbols("2026-10-20", root), ["NFLX", "ISRG"])
            md = ep.render(["NFLX", "ISRG"], "2026-10-21", root)
        self.assertIn("市场盯什么：收入对自己的预测", md)
        self.assertIn("最近 5 次：开盘涨 1 次、跌 4 次；最近连续 4 次跌", md)       # the scored 10-21 report joins the history
        self.assertIn("| 2026-10-21 | 盘后 | -4.0% | -5.0% | 9.6% |", md)
        self.assertIn("复盘 2026-10-20：全年落在下沿", md)
        self.assertIn("还没有手写的档案", md.split("## ISRG")[1])
        self.assertIn("没有过去的反应数据", md.split("## ISRG")[1])

    def test_ahead_takes_the_largest_and_the_ones_we_watch(self):
        from types import SimpleNamespace
        from hero import earnings_profiles as ep
        self.assertEqual(ep.weekdays_after(date(2026, 10, 23), 2), ["2026-10-26", "2026-10-27"])     # Friday -> Mon, Tue

        def get(url, params=None, headers=None, timeout=None):
            rows = [{"symbol": f"S{i}", "time": "time-after-hours", "marketCap": f"${(100 - i) * 1e9:,.0f}"} for i in range(30)]
            rows.append({"symbol": "IREN", "time": "time-pre-market", "marketCap": "$1,000,000"})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"rows": rows}})
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "journal-s500").mkdir()
            (root / "journal-s500" / "snapshot.json").write_text(json.dumps({"positions": [{"symbol": "IREN261023P00027500"}]}))
            (root / "research" / "earnings-forecast").mkdir(parents=True)
            (root / "research" / "earnings-forecast" / "extra_watch.json").write_text(json.dumps({"symbols": ["SPCX"]}))
            asked = []
            yahoo = lambda syms: asked.extend(syms) or {"SPCX": {"date": "2026-10-21", "ts": 1792612800}}   # 10-21 16:00 ET
            got = ep.ahead(1, date(2026, 10, 20), root, get, top=3, lookup=yahoo)
        self.assertEqual(asked, ["SPCX"])                                                        # IREN was listed
        self.assertEqual([r["symbol"] for r in got["2026-10-21"]], ["S0", "S1", "S2", "IREN", "SPCX"])   # + a holding, + Yahoo
        self.assertEqual(got["2026-10-21"][-1]["time"], "post-market")
        got["2026-10-21"].pop()
        self.assertEqual(got["2026-10-21"][-1]["time"], "pre-market")
        md = ep.page("NVDA", [], {"bar": "超预期还不够，要指引比买方期待高"}, [])
        self.assertIn("本季门槛：超预期还不够", md)


class ResearchZdteConfirm(unittest.TestCase):
    def test_gauges_are_signed_by_spy_and_split_in_and_out_of_sample(self):
        from unittest import mock
        from hero import research_zdte_confirm as zc

        def minute(day, hhmm, px):
            h, m = map(int, hhmm.split(":"))
            return {"t": f"{day}T{h + 4:02d}:{m:02d}:00Z", "o": px, "h": px, "l": px, "c": px, "v": 100}

        days = [f"2025-06-{d:02d}" for d in range(2, 28)] + [f"2026-06-{d:02d}" for d in range(1, 11)]
        rows = []
        for i, d in enumerate(days):
            kind = "C" if i % 2 == 0 else "P"
            win = 2.0 if i % 3 == 0 else -0.9
            rows.append({"day": d, "move": 0.003 if kind == "C" else -0.003, "kind": kind,
                         "res": {"0.003": {"0/0": {"take3_trail": win}, "0/0.1": {"take3_trail": win - 0.05}}}})

        class Client:      # IWM goes with SPY on the winning days, against it otherwise; the rest stay flat
            def stock_bars(self, syms, start, end, feed=None):
                sym, out = syms[0], []
                for i, d in enumerate(days):
                    if not (start[:10] <= d < end[:10]):
                        continue
                    up = (i % 2 == 0) == (i % 3 == 0) if sym == "IWM" else None
                    ten = 100.0 if up is None else (100.5 if up else 99.5)
                    out += [minute(d, "09:30", 100.0), minute(d, "10:00", ten)]
                return {sym: out}

        with mock.patch.object(zc, "latest_replay", lambda: {"start": "2025-06-01", "end": "2026-06-30", "rows": rows}):
            data = zc.run(Client())
        self.assertEqual(len(data["days"]), len(days))
        d0 = data["days"][0]                         # a call day that won: IWM +0.5% -> same direction
        self.assertAlmostEqual(d0["g"]["IWM"], 0.005)
        self.assertEqual(zc.bucket(d0["g"]["SMH"]), "持平")
        d1 = data["days"][1]                         # a put day that lost: IWM up 0.5% while SPY fell -> against
        self.assertAlmostEqual(d1["g"]["IWM"], -0.005)
        g = zc.groups(data["days"])
        self.assertTrue(all(d["main"] == 2.0 for d in g["小盘 IWM 同向"]))
        table, md = zc.report(data["days"], data["end"])
        self.assertEqual(table["小盘 IWM 同向"]["verdict"], "笔数太少，不下结论")   # 9 found, 3 to check
        self.assertIn("| 每天都做 | 36 |", md)
        self.assertEqual(zc.verdict((0.3, 40), (0.2, 10), (0.0, 400), (0.0, 100), 0.1), "样本内外都更好，去掉最好 3 笔也赚：值得实盘小试")
        self.assertEqual(zc.verdict((0.3, 40), (-0.1, 10), (0.0, 400), (0.0, 100), 0.1), "只在 2024–25 更好，2026 没重现")


if __name__ == "__main__":
    unittest.main()


class ResearchVerdict(unittest.TestCase):
    """Owner, 10-23 (「想加快进度」): each ledger against the backtest that already answers it, plus a 10-trade stop line."""

    def docs(self):
        days = [f"2026-{m:02d}-{d:02d}" for m in (7, 8, 9) for d in (1, 8, 15, 22)]
        mm = {"rows": [{"und": "SPY", "day": d, "dte": 0, "move": {"09:35": 0.001, "10:00": 0.006 if i % 2 else 0.001},
                        "res": {"10:00|0.003": {"take3_trail": 2.0 if i % 3 == 0 else -0.9, "take5_trail": -0.5},
                                "09:35|0.003": {"take3_trail": -0.8}}} for i, d in enumerate(days)]}
        nm = {"trades": {"IWM|0.003|3": [[d, -0.9] for d in days]}}
        st = {"rows": [{"day": d, "symbol": "SPY", "prev_ret": -0.02 if i % 2 else 0.01, "side": "C", "res": {"C|0.003|3": 0.5, "C|0.006|3": -0.2}}
                       for i, d in enumerate(days)]}
        dw = {"rows": [{"day": d, "leg": "otm3", "res": {"trail2": 0.3}} for d in days] + [{"day": days[0], "leg": None, "res": {}}]}
        pd_ = {"rows": [{"day": d, "gap": 0.12 if i % 2 else 0.06, "held": 0.01, "res": {"trail2": 0.4}} for i, d in enumerate(days)]}
        return {"minmove": mm, "names": nm, "streak": st, "dipweek": dw, "pead": pd_}

    def test_each_ledger_maps_to_its_series(self):
        from hero import research_verdict as v
        led = v.ledgers(self.docs())
        self.assertEqual(len(led["spy0dte-near-500"][1]), 12)                       # every day
        self.assertEqual(len(led["spy0dte-500"][1]), 6)                             # only 10:00 moves of 0.5%+
        self.assertEqual([r for _, r in led["spy0dte-near-1000"][1]][:2], [-0.8, -0.8])   # the 09:35 entry
        self.assertEqual(len(led["spydip-500"][1]), 6)                              # after a 1%+ drop
        self.assertEqual(len(led["dipweek-500"][1]), 12)                            # the otm3 leg, the empty row dropped
        self.assertEqual((len(led["pead-500"][1]), len(led["pead-1000"][1])), (6, 12))
        self.assertIsNone(led["dia0dte-plan-500"][1])

    def test_stop_line_is_the_20th_percentile_of_ten_trade_sums(self):
        from hero import research_verdict as v
        xs = [(f"2026-01-{i + 1:02d}", r) for i, r in enumerate([-1.0] * 10 + [2.0] * 10)]
        line = v.stop_line(xs)
        self.assertEqual((line["windows"], line["worst"], line["best"]), (11, -10.0, 20.0))
        self.assertEqual(line["p20"], -4.0)                                          # sums go -10, -7, -4, ... 20
        self.assertIsNone(v.stop_line(xs[:5]))

    def test_live_trades_join_the_take_and_trail_halves(self):
        from hero import research_verdict as v
        book = {"sleeves": {"x-500": {"closed": [
            {"tid": "20261010-aa", "paid": 100.0, "proceeds": 300.0, "closed": "2026-10-10 10:20"},
            {"tid": "20261010-aar", "paid": 100.0, "proceeds": 10.0, "closed": "2026-10-10 15:30"},
            {"tid": "20261011-bb", "paid": 200.0, "proceeds": 0.0, "closed": "2026-10-11 15:30"}]}}}
        live = v.live_stats(book, "x-500")
        self.assertEqual((live["n"], live["pnl"]), (2, -90.0))                        # 310 - 200 and 0 - 200
        self.assertAlmostEqual(live["mean"], (0.55 - 1.0) / 2)                        # (310/200 - 1) and -1
        self.assertIsNone(v.live_stats(book, "none"))

    def test_verdict_wording_and_the_table(self):
        from hero import research_verdict as v
        s = {"trim": -0.2, "recent": (-0.1, 20)}
        self.assertEqual(v.verdict(s, (0.3, 5), {"p20": -4.0}, {"n": 10, "last_sum": -5.0}), "回测就不行；最近一个月 +30%（5 笔）；实盘低于停止线")
        self.assertEqual(v.verdict({"trim": 0.1, "recent": (0.2, 9)}, None, {"p20": -4.0}, {"n": 3, "last_sum": 1.0}), "回测可以；实盘 3/10 笔")
        self.assertEqual(v.verdict({"trim": 0.1, "recent": (-0.2, 9)}, None, None, None), "回测分歧")
        self.assertEqual(v.verdict(None, None, None, None), "没有回测")
        rows, md = v.build(self.docs(), {"sleeves": {}}, "2026-10-23")
        self.assertIn("| spy0dte-near-500 |", md)
        self.assertIn("| dia0dte-plan-500 | 没有回测：DIA 没算过 | 0 |", md)
        self.assertEqual(next(r for r in rows if r["id"] == "iwm0dte-plan-500")["verdict"], "回测就不行；最近一个月 -90%（4 笔）")


class ResearchSharesSignals(unittest.TestCase):
    """Owner, 10-23 (「那你现在做？」): the signal rules in shares, on a fake tape."""

    def tape(self):
        from datetime import date, timedelta
        days, d = [], date(2026, 1, 5)
        while len(days) < 40:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += timedelta(days=1)
        flat = {s: [[100.0, 100.5, 100.0] for _ in days] for s in ("AAA", "BBB", "SPY", "QQQ")}   # open, high, close
        flat["SSO"] = [[50.0, 50.2, 50.0] for _ in days]
        flat["QLD"] = [[40.0, 40.2, 40.0] for _ in days]
        for i in range(25, 30):
            flat["AAA"][i] = [97.0, 97.5, 97.0]                   # day 25: a 3% gap down, closes there and stays
        flat["AAA"][30] = [97.5, 107.5, 107.0]                   # five sessions on: back to 107 within the day, no gap
        flat["AAA"][31] = [107.0, 107.5, 107.0]
        for i in range(0, 28):
            flat["BBB"][i] = [100.0, 100.5, 100.0 + (0.1 if i % 2 else 0.0)]   # a 0.1% daily wiggle: the 20-day swing
        flat["AAA"][32] = [118.0, 119.5, 119.0]                   # +10.3% over the 107 close, closes above its open
        for i in range(33, 40):
            flat["AAA"][i] = [120.0, 122.5, 122.0]                # the next open is 120, that close 122
        flat["BBB"][28] = [105.0, 106.5, 106.0]                   # +5% open over a 20-day high of 100.5, closes 106
        for i in range(29, 40):
            flat["BBB"][i] = [107.0, 108.5, 108.0]
        flat["SPY"][26] = [100.0, 100.5, 98.9]                    # closes 1.1% down
        flat["SPY"][27] = [99.0, 100.5, 100.0]
        for i in range(27, 40):
            flat["SSO"][i] = [50.0, 51.5, 51.0]
        bars = {s: [{"t": f"{days[i]}T05:00:00Z", "o": x[0], "h": x[1], "c": x[2], "v": 1000} for i, x in enumerate(v)] for s, v in flat.items()}
        return days, bars

    def test_signals_and_their_returns(self):
        from hero import research_shares_signals as ss
        days, bars = self.tape()
        dates, o, h, c, dv = ss.align(bars)
        rows = ss.signals(dates, o, h, c, lambda t: ["AAA", "BBB"], 21)
        dip = next(r for r in rows if r["sig"] == "dip2")
        self.assertEqual((dip["sym"], dip["day"], dip["gap"]), ("AAA", days[25], -0.03))
        self.assertAlmostEqual(dip["res"]["hold5"], 107 / 97 - 1 - 0.001)
        self.assertEqual([r["sym"] for r in rows if r["sig"] == "dip5"], [])
        bg = next(r for r in rows if r["sig"] == "breakgap")
        self.assertEqual((bg["sym"], bg["day"]), ("BBB", days[28]))
        self.assertAlmostEqual(bg["res"]["same_close"], 106 / 105 - 1 - 0.001)
        self.assertAlmostEqual(bg["res"]["next_close"], 108 / 105 - 1 - 0.001)
        big = next(r for r in rows if r["sig"] == "bigup")
        self.assertEqual((big["sym"], big["day"]), ("AAA", days[33]))                     # bought the next open
        self.assertAlmostEqual(big["res"]["close1"], 122 / 120 - 1 - 0.001)
        sd = next(r for r in rows if r["sig"] == "spydip_SPY")
        self.assertEqual(sd["day"], days[27])
        self.assertAlmostEqual(sd["res"]["SPY"], 100 / 99 - 1 - 0.001)
        self.assertAlmostEqual(sd["res"]["SSO"], 51 / 50 - 1 - 0.001)
        self.assertEqual([r for r in rows if r["sig"] == "spydip_QQQ"], [])
        base = ss.baseline(dates, c, lambda t: ["AAA", "BBB"], 21)
        self.assertEqual(len(base), len(dates) - 5 - 21)
        self.assertEqual(ss.day_series(rows, "dip2", "hold5"), [(days[25], dip["res"]["hold5"])])
        table = ss.evaluate(rows, base, dates[-1])
        self.assertIn("dip2|hold5", table)
        self.assertEqual(table["dip2|hold5"]["rows"], 1)
        md = ss.markdown(table, {"generated": "2026-10-23", "start": days[21], "end": days[-1], "candidates": 2})
        self.assertIn("突破大跳空", md)
        self.assertIn("| 第 5 天 | 1（1） | +10.2% |", md)

    def test_growth_compounds_in_sequence(self):
        from hero import research_shares_signals as ss
        g = ss.growth([("d1", 0.10), ("d2", -0.05), ("d3", 0.0)])
        self.assertAlmostEqual(g["multiple"], 1.1 * 0.95)
        self.assertAlmostEqual(g["drawdown"], 0.05)
        self.assertAlmostEqual(g["g100"], (math.log(1.1) + math.log(0.95)) / 3)
        self.assertEqual((g["worst"], g["best"]), (-0.05, 0.10))


class ResearchHeroGame(unittest.TestCase):
    """Owner, 10-23 (「出方案」): rounds from $500 to 20x or under 10%, replayed on a per-trade series."""

    def test_rounds_heroes_and_zeros(self):
        from hero import research_hero_game as hg
        g = hg.game([2.0, 2.0, 2.0, -1.0, -0.5, -0.5, -0.5, -0.5, 1.0], 1.0)
        self.assertEqual((g["rounds"], g["heroes"], g["zeros"]), (3, 1, 2))            # 27x, then -100%, then 4 halvings
        self.assertEqual(g["trades_per_round"], 8 / 3)
        self.assertAlmostEqual(g["p_hero"], 1 / 3)
        self.assertEqual((g["peak_max"], g["cost_per_hero"]), (27.0, 1500.0))
        self.assertEqual(g["open_peak"], 2.0)                                           # the last trade is still open
        half = hg.game([2.0] * 5, 0.5)                                                  # x2 a trade: 32x on the fifth
        self.assertEqual((half["heroes"], half["trades_per_round"]), (1, 5))
        self.assertIsNone(hg.game([], 1.0)["p_hero"])

    def test_live_rounds_from_the_ledgers(self):
        from hero import research_hero_game as hg
        book = {"sleeves": {"x-500": {"start": 500.0, "cash": 620.0, "best": 700.0, "round": 3, "closed": [{}] * 7, "round_from": 5,
                                      "rounds": [{"round": 1, "end": "zero", "cash": 40.0, "peak": 1100.0, "trades": 3, "day": "2026-10-20"},
                                                 {"round": 2, "end": "hero", "cash": 10400.0, "peak": 10400.0, "trades": 2, "day": "2026-10-22"}]},
                            "y-500": {"start": 500.0, "cash": 500.0, "best": 500.0, "round": 1, "closed": []}}}
        rows = hg.live_rounds(book, "实盘")
        x = next(r for r in rows if r["id"] == "x-500")
        self.assertEqual((x["rounds"], x["heroes"], x["zeros"], x["x2_rounds"], x["trades_per_round"], x["cost_per_hero"]), (2, 1, 1, 1.0, 2.5, 1000.0))
        self.assertEqual(x["now"], {"round": 3, "cash": 1.24, "peak": 1.4, "trades": 2})
        self.assertEqual((x["trades"], x["x2"], x["x5"]), (0, None, None))                # the fake closed tickets carry no money
        y = next(r for r in rows if r["id"] == "y-500")
        self.assertEqual((y["rounds"], y["cost_per_hero"], y["now"]["trades"]), (0, None, 0))
        self.assertIn("| x-500 | 实盘 | 0 | — | — | 2 | 1 | 1 | 100.0% | 2 | $1,000 | 第 3 轮 · 1.24 / 1.40 · 2 |", hg.live_markdown(rows))
        book["sleeves"]["y-500"]["closed"] = [{"tid": "a", "paid": 100.0, "proceeds": 300.0}, {"tid": "ar", "paid": 100.0, "proceeds": 900.0},
                                              {"tid": "b", "paid": 200.0, "proceeds": 0.0}]
        y = next(r for r in hg.live_rounds(book, "实盘") if r["id"] == "y-500")
        self.assertEqual((y["trades"], y["x2"], y["x5"]), (2, 0.5, 0.5))                 # a: 1200 / 200 = 6x; b: -100%

    def test_series_and_markdown(self):
        from hero import research_hero_game as hg
        docs = {"lotto": {"rows": [{"symbol": "SPY", "best_x": 25.0, "hold": 3.0}, {"symbol": "AAA", "best_x": 1.5, "hold": -0.9}]},
                "dipweek": {"rows": [{"day": "2026-01-02", "leg": "atm", "gap": -0.03, "res": {"d5": 0.5}},
                                     {"day": "2026-01-02", "leg": "atm", "gap": -0.06, "res": {"d5": -0.9}}]}}
        s = hg.series(docs)
        self.assertEqual(s["一分钱末日彩票 全部（单张 20 倍止盈）"], [19.0, -0.9])
        self.assertEqual(s["抄底给一周 平值 d5"], [-0.9])                                 # the biggest gap-down of the day
        md = hg.markdown(hg.table(docs), "2026-10-23")
        self.assertIn("| 一分钱末日彩票 全部（单张 20 倍止盈） | 2 | 押一半 |", md)


class ResearchPeerSignals(unittest.TestCase):
    """Owner, 10-23 (「C 上传了一个东西」): a signal group's sheet replayed on daily bars; aggregates only."""

    def test_parse_replay_and_summary(self):
        import openpyxl
        from hero import research_peer_signals as ps
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["日期", "标的", "入场价", "最高TP/止损", "到达TP数"])
        ws.append([datetime(2026, 4, 10), "aaa", 110.5, 0.08, 2])
        ws.append([datetime(2026, 4, 12), "BBB", 50.0, "/", "/"])            # no sheet return yet
        ws.append(["note", None, None, None, None])
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.xlsx"
            wb.save(f)
            sigs = ps.parse(f)
        self.assertEqual(sigs, [{"day": "2026-04-10", "sym": "AAA", "entry": 110.5, "sheet_tp": 0.08, "ntp": 2},
                                {"day": "2026-04-12", "sym": "BBB", "entry": 50.0, "sheet_tp": None, "ntp": None}])
        bars = [{"t": f"2026-04-{d:02d}T04:00:00Z", "o": 100.0 + d, "h": 102.0 + d, "l": 99.0 + d, "c": 101.0 + d} for d in range(1, 30)]
        bench = {"SMH": [{"t": b["t"], "c": 200.0 + i} for i, b in enumerate(bars)]}
        r = ps.replay(sigs[0], bars, bench)
        self.assertEqual((r["how"], r["first"]), ("当天按表价", "target"))                   # 110.5 inside the 10th's 109-112 range
        self.assertAlmostEqual(r["res"]["c5"], 116 / 110.5 - 1 - 0.001)                      # the 15th closes at 116
        self.assertAlmostEqual(r["mfe10"], 122 / 110.5 - 1)                                  # the 20th's high
        self.assertAlmostEqual(r["bench"]["SMH"]["c5"], 214 / 209 - 1)
        late = ps.replay({**sigs[0], "entry": 50.0}, bars, bench)                            # not traded through: next open
        self.assertEqual((late["how"], late["res"]["c1"]), ("第二天开盘", (113 / 111) - 1 - 0.001))
        self.assertIsNone(ps.replay({**sigs[0], "day": "2026-05-01"}, bars, bench))
        s = ps.summarise([r, late])
        self.assertEqual(s["entry"], {"当天按表价": 1, "第二天开盘": 1})
        self.assertAlmostEqual(s["first"]["target"], 1.0)
        self.assertIn("2026-04", s["months"])
        md = ps.markdown(s, {"generated": "2026-10-23", "title": "t", "signals": 2, "first": "2026-04-10", "last": "2026-04-12",
                             "sheet_mean": 0.08, "sheet_wins": 1.0, "replayed": 2}, [0.1, -0.05])
        self.assertIn("先到 +5% 的 +100.0%", md)
        self.assertAlmostEqual(ps.momentum_at("2026-04-25", bars), 125 / 105 - 1)
        f = ps.setup("2026-04-25", [{**b, "v": 1000 if b["t"] < "2026-04-25" else 3000} for b in bars])
        self.assertAlmostEqual(f["mom20"], 125 / 105 - 1)
        self.assertAlmostEqual(f["from_high"], 126 / 126 - 1)                                 # closes at the 20-day high
        self.assertAlmostEqual(f["vol"], 3.0)
        r["setup"], late["setup"] = {"mom20": 0.2, "from_high": 0.0, "day": 0.01, "gap": 0.0, "vol": 1.0}, {"mom20": -0.1, "from_high": -0.15, "day": -0.03, "gap": 0.0, "vol": 2.5}
        g = ps.buckets([r, late])
        self.assertEqual((g["信号前 20 天涨 10% 以上（追强势）"]["n"], g["信号前 20 天在跌（抄底）"]["n"], g["当天成交量是平时 2 倍以上"]["n"]), (1, 1, 1))
        self.assertIsNone(ps.setup("2026-04-05", bars))
        up = [{"t": f"d{d}", "o": 100.0 + d, "h": 101.0 + d, "l": 99.5 + d, "c": 100.5 + d} for d in range(1, 25)]   # +1 a day
        g = ps.ladder(100.0, up)
        self.assertEqual(g["levels"], 5)                                                        # every level trades on the way up
        self.assertAlmostEqual(g["ret"], sum(max(l, 0) for l in (0.012, 0.025, 0.04, 0.06, 0.10)) / 5 - 0.001, places=2)
        down = [{"t": f"d{d}", "o": 100.0 - 2 * d, "h": 100.5 - 2 * d, "l": 99.0 - 2 * d, "c": 99.5 - 2 * d} for d in range(1, 25)]
        g = ps.ladder(100.0, down)
        self.assertEqual((g["levels"], g["days"]), (0, 3))                                      # the stop on day 3 (low 93 <= 94)
        self.assertAlmostEqual(g["ret"], 94 / 100 - 1 - 0.001)
        flat = [{"t": f"d{d}", "o": 100.0, "h": 100.5, "l": 99.5, "c": 100.0} for d in range(1, 25)]
        self.assertEqual(ps.ladder(100.0, flat), {"ret": -0.001, "days": 20, "levels": 0})      # nothing trades: out at the horizon
        be = [{"t": "d1", "o": 100.0, "h": 101.5, "l": 99.8, "c": 101.0}] + [{"t": f"d{d}", "o": 100.5, "h": 100.8, "l": 97.0, "c": 98.0} for d in range(2, 25)]
        g = ps.ladder(100.0, be)
        self.assertEqual(g["levels"], 1)                                                        # first level, then the breakeven stop
        self.assertAlmostEqual(g["ret"], 0.2 * 0.012 + 0.8 * 0.0 - 0.001)






class HeroPlanConfig(unittest.TestCase):
    """docs/hero-plan.md, approved 10-23: what config/s500.json must say."""

    def test_the_ledgers_follow_the_plan(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "s500.json").read_text())
        by = {s["id"]: s for s in cfg["sleeves"]["list"]}
        self.assertEqual(len(by), len(cfg["sleeves"]["list"]))
        for sid in ("spy0dte-500", "spy0dte-1000", "spy0dte-near-500", "spy0dte-near-1000"):
            self.assertEqual(by[sid].get("fraction"), 1.0, sid)                          # the SPY 0DTE ledgers go all in
        for sid in ("nvda0dte-500", "nvda0dte-1000", "tsla0dte-5x-500", "tsla0dte-5x-1000", "spydip-500", "spydip-1000", "pead-1000",
                    "qqq0dte-500", "qqq0dte-1000", "tsla0dte-500", "tsla0dte-1000"):
            self.assertTrue(by[sid].get("stop_entries"), sid)                             # not hero candidates (or too dear): no new entries
            self.assertNotEqual(by[sid].get("fraction"), 1.0, sid)
        for sid in ("nvda0dte-plan-500", "tsla0dte-5x-plan-500", "spydip-plan-500", "pead-plan-1000", "qqq0dte-plan-500", "tsla0dte-plan-500"):
            self.assertTrue(by[sid].get("plan_only"), sid)                                # their plan twins keep the record
        for sid in ("overnight-500", "overnight-1000"):                                   # momentum calls: back on, with the cap
            self.assertFalse(by[sid].get("stop_entries"), sid)
            self.assertEqual(by[sid].get("max_spread"), 0.10, sid)
        dip = {sid: s for sid, s in by.items() if s["kind"] == "dipweek"}
        self.assertEqual(sorted(dip), ["dipweek-1000", "dipweek-500", "dipweek-hold-1000", "dipweek-hold-500", "dipweek-take-1000", "dipweek-take-500"])
        self.assertTrue(all(s.get("max_spread") == 0.10 and s.get("fraction") == 0.5 and s.get("min_vol") == 0.103 for s in dip.values()))
        self.assertEqual({(s.get("exit", "trail"), s["offset"]) for s in dip.values()},
                         {("trail", 0.03), ("trail", 0.0), ("hold", 0.03), ("hold", 0.0), ("take2", 0.0), ("take2", 0.03)})
        for sid in ("iwm0dte-plan-500", "amd0dte-plan-500", "amzn0dte-plan-500", "meta0dte-plan-500", "msft0dte-plan-500"):
            self.assertNotIn(sid, by)                                                    # answered by the backtests already
        self.assertIn("dia0dte-plan-500", by)
