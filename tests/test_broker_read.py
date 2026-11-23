import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from hero import broker_read as br


class FakeBroker:
    """Answers the GETs broker_read makes; records every call so a test can see nothing else was asked."""

    def __init__(self, pages=None, fail=()):
        self.calls, self.pages, self.fail = [], pages or {}, set(fail)

    def _answer(self, method, path, params):
        self.calls.append((method, path, dict(params or {})))
        if path in self.fail or (path, (params or {}).get("feed")) in self.fail:
            err = RuntimeError(f"{path} refused")
            err.status = 403
            raise err
        v = self.pages.get(path)
        if callable(v):
            return v(params or {})
        return v

    def _t(self, method, path, **kw):
        return self._answer(method, path, kw.get("params"))

    def _d(self, path, params):
        return self._answer("GET", path, params)

    def account(self):
        return self._answer("GET", "/v2/account", {})

    def clock(self):
        return self._answer("GET", "/v2/clock", {})

    def positions(self):
        return self._answer("GET", "/v2/positions", {})

    def asset(self, s):
        return self._answer("GET", f"/v2/assets/{s}", {})


class TestAuctions(unittest.TestCase):
    def test_official_prefers_the_listing_exchange_and_the_official_code(self):
        prints = [{"x": "Q", "c": "O", "p": 724.40}, {"x": "P", "c": "O", "p": 724.31}, {"x": "P", "c": "Q", "p": 724.29}]
        self.assertEqual(br.official(prints, "P", br.OPEN_CODES)["p"], 724.29)
        self.assertEqual(br.official(prints, None, br.OPEN_CODES)["p"], 724.29)      # any exchange's official open
        self.assertEqual(br.official([{"x": "Q", "c": "O", "p": 1.0}], "P", br.OPEN_CODES)["p"], 1.0)
        self.assertIsNone(br.official([{"x": "P", "c": "X", "p": 1.0}], "P", br.OPEN_CODES))

    def test_rows_pages_and_merge(self):
        pages = [{"auctions": {"SPY": [{"d": "2026-11-20", "o": [{"x": "P", "c": "Q", "p": 713.56}], "c": [{"x": "P", "c": "M", "p": 689.81}]}]},
                  "next_page_token": "n"},
                 {"auctions": {"SPY": [{"d": "2026-11-23", "o": [{"x": "P", "c": "Q", "p": 724.29}], "c": []}]}, "next_page_token": None}]
        a = FakeBroker({"/v2/stocks/auctions": lambda p: pages[1] if p.get("page_token") else pages[0]})
        rows = br.auction_rows(br.auctions(a, ["SPY"], "2026-11-20", "2026-11-23"), {"SPY": "ARCA"})
        self.assertEqual(rows["SPY"]["2026-11-20"]["close"], 689.81)
        self.assertEqual(rows["SPY"]["2026-11-23"]["open"], 724.29)
        self.assertIsNone(rows["SPY"]["2026-11-23"]["close"])
        merged = br.merge_auctions({"SPY": {"2026-11-19": {"open": 1}}}, rows)
        self.assertEqual(list(merged["SPY"]), ["2026-11-19", "2026-11-20", "2026-11-23"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "auctions.json"
            p.write_text(json.dumps(merged))
            self.assertEqual(br.official_open("SPY", "2026-11-23", p), 724.29)
            self.assertIsNone(br.official_open("SPY", "2026-11-24", p))
            self.assertIsNone(br.official_open("SPY", "2026-11-23", Path(d) / "missing.json"))


class TestActivities(unittest.TestCase):
    def test_pages_slim_and_no_ids(self):
        uid = "20261120000000000::0f8e5c3a-1111-2222-3333-444455556666"
        page1 = [{"id": f"x{i}", "activity_type": "FILL", "order_id": "0f8e5c3a-1111-2222-3333-444455556666"} for i in range(br.PAGE)]
        page2 = [{"id": uid, "activity_type": "OPEXP", "date": "2026-11-20", "symbol": "SPY261120C00721000", "qty": "-44",
                  "net_amount": "0", "description": "Option Expiry 0f8e5c3a-1111-2222-3333-444455556666"}]
        a = FakeBroker({"/v2/account/activities": lambda p: page2 if p.get("page_token") == f"x{br.PAGE - 1}" else page1})
        rows = br.activities(a, "2026-11-01")
        keep, counts = br.slim(rows)
        self.assertEqual(counts, {"FILL": br.PAGE, "OPEXP": 1})
        self.assertEqual(len(keep), 1)
        text = json.dumps(keep)
        self.assertNotIn("0f8e5c3a", text)
        self.assertNotIn("order_id", text)
        self.assertNotIn(uid, text)
        self.assertEqual(keep[0]["activity_type"], "OPEXP")
        self.assertEqual(br.merge_activities(keep, keep), keep)


class TestHistoryLevelsQuotes(unittest.TestCase):
    def test_history_days_in_new_york(self):
        ts = int(datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc).timestamp())       # midnight New York
        a = FakeBroker({"/v2/account/portfolio/history": {"timestamp": [ts, ts + 86400 * 3], "equity": [70956.37, None],
                                                           "profit_loss": [-6515.31, 0], "profit_loss_pct": [-0.0868, 0]}})
        h = br.history(a)
        self.assertEqual(h, [{"day": "2026-11-20", "equity": 70956.37, "pl": -6515.31, "pl_pct": -0.0868}])

    def test_levels_keep_no_account_number(self):
        a = FakeBroker({"/v2/account": {"account_number": "PA3ABCDEFGH1", "options_trading_level": 3, "options_approved_level": 3},
                        "/v2/account/configurations": {"max_options_trading_level": 3, "no_shorting": False}})
        lv = br.levels(a)
        self.assertEqual(lv["options_trading_level"], 3)
        self.assertNotIn("PA3ABCDEFGH1", json.dumps(lv))

    def test_age_parses_nanoseconds(self):
        now = datetime(2026, 11, 23, 17, 40, tzinfo=timezone.utc)
        self.assertEqual(br.age("2026-11-23T17:25:00.123456789Z", now), 15.0)
        self.assertEqual(br.age("2026-11-23T17:39:00Z", now), 1.0)
        self.assertIsNone(br.age(None, now))

    def test_quotes_side_by_side_and_opra_refused(self):
        s = "SPY261123P00713000"
        q = {"t": "2026-11-23T17:39:00Z", "bp": 0.35, "ap": 0.37}
        a = FakeBroker({"/v1beta1/options/snapshots": {"snapshots": {s: {"latestQuote": {"t": "2026-11-23T17:25:00Z", "bp": 0.5, "ap": 0.52},
                                                                          "latestTrade": {"t": "2026-11-23T17:20:00Z", "p": 0.5}}}},
                        "/v1beta1/options/quotes/latest": {"quotes": {s: q}},
                        "/v1beta1/options/trades/latest": {"trades": {s: {"t": "2026-11-23T17:38:00Z", "p": 0.36}}}},
                       fail={("/v1beta1/options/quotes/latest", "opra")})
        out = br.quotes(a, [s], now=datetime(2026, 11, 23, 17, 40, tzinfo=timezone.utc))
        r = out["rows"][0]
        self.assertEqual((r["snap_quote_age"], r["quote_age"], r["trade_age"]), (15.0, 1.0, 2.0))
        self.assertFalse(r["same_quote"])
        self.assertTrue(out["opra"].startswith("refused"))
        self.assertEqual(br.quotes(a, [])["rows"], [])


class TestCorporateAndRun(unittest.TestCase):
    def test_corporate_by_year_and_splits(self):
        nvda = {"symbol": "NVDA", "new_rate": 10, "old_rate": 1, "ex_date": "2024-06-10"}
        a = FakeBroker({"/v1/corporate-actions": lambda p: {"corporate_actions": {"forward_splits": [nvda] if p["start"] <= "2024-06-10" <= p["end"] else [],
                                                                                   "cash_dividends": [{"symbol": "AAPL", "ex_date": p["start"], "rate": 0.26}]}}})
        acts = br.corporate(a, ["NVDA", "AAPL"], "2023-03-01", "2025-02-01")
        self.assertEqual([c[2]["start"] for c in a.calls], ["2023-03-01", "2024-01-01", "2025-01-01"])
        self.assertEqual(br.splits(acts), [{"symbol": "NVDA", "day": "2024-06-10", "ratio": 10.0}])
        self.assertEqual(len(acts["cash_dividends"]), 3)

    def test_run_account_only_gets_and_survives_a_failure(self):
        a = FakeBroker({"/v2/account": {"options_trading_level": 3}, "/v2/account/configurations": {},
                        "/v2/account/activities": [], "/v2/clock": {"is_open": False}, "/v2/positions": []},
                       fail={"/v2/account/portfolio/history"})
        with tempfile.TemporaryDirectory() as d:
            rec = br.run_account(a, "main", date(2026, 11, 23), data=Path(d))
            self.assertIn("history", rec["errors"])
            self.assertEqual(rec["levels"]["options_trading_level"], 3)
            self.assertTrue((Path(d) / "main.json").exists())
        self.assertEqual({m for m, *_ in a.calls}, {"GET"})

    def test_run_market_and_report(self):
        a = FakeBroker({"/v2/stocks/auctions": {"auctions": {"SPY": [{"d": "2026-11-23", "o": [{"x": "P", "c": "Q", "p": 724.29}],
                                                                       "c": [{"x": "P", "c": "M", "p": 725.0}]}]}},
                        "/v2/assets/SPY": {"exchange": "ARCA"},
                        "/v1/corporate-actions": {"corporate_actions": {}}})
        with tempfile.TemporaryDirectory() as d:
            errors = br.run_market(a, date(2026, 11, 23), data=Path(d), symbols=["SPY"])
            self.assertEqual(errors, {})
            auc = json.loads((Path(d) / "auctions.json").read_text())
            self.assertEqual(auc["SPY"]["2026-11-23"]["open"], 724.29)
            opens = br.compare_opens(auc, {"SPY": {"2026-11-23": 724.29}}, {"SPY": {"2026-11-23": 724.33}})
            self.assertAlmostEqual(opens[0]["minute_gap"], 724.33 / 724.29 - 1)
            text = br.report(date(2026, 11, 23), {"main": {"levels": {"options_trading_level": 3}, "history": [], "activities": [],
                                                           "quotes": {"rows": [], "note": "收盘后不查报价新旧"}}},
                             errors, auc, {"splits": [], "dividends": []}, opens)
            self.assertIn("官方开盘价", text)
            self.assertIn("724.29", text)
            self.assertNotIn("PA3", text)


if __name__ == "__main__":
    unittest.main()
