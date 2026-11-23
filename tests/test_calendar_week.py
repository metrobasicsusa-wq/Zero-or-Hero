"""hero/calendar_week.py: one week's known events from fixed tables (no network)."""

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from hero import calendar_week as cw


def fixture(root: Path) -> None:
    """A small repo: the pool, both snapshots, the extra watch list and an earnings calendar."""
    (root / "data").mkdir(parents=True)
    (root / "journal").mkdir()
    (root / "journal-s500").mkdir()
    (root / "research" / "earnings-forecast" / "deep").mkdir(parents=True)
    (root / "data" / "universe.json").write_text(json.dumps({"stocks": ["HPE", "ADI", "NVDA"], "etfs": ["SPY"]}))
    (root / "journal" / "snapshot.json").write_text(json.dumps({"positions": [{"symbol": "AAPL", "qty": "1"}]}))
    (root / "journal-s500" / "snapshot.json").write_text(json.dumps({"positions": [
        {"symbol": "WMT261127C00130000", "qty": "4"}, {"symbol": "SPY261123P00713000", "qty": "1"}]}))
    (root / "research" / "earnings-forecast" / "extra_watch.json").write_text(json.dumps({"symbols": ["DIS"]}))
    (root / "research" / "earnings-forecast" / "deep" / "HPE-2026-11-23.md").write_text("# HPE\n")
    (root / "data" / "earnings.json").write_text(json.dumps({
        "fetched_at": "2026-11-23T09:00:00-05:00", "fetched_on": "2026-11-23", "horizon_days": 90,
        "symbols": ["HPE", "ADI", "NVDA", "AAPL", "SPY", "ZZZ"],
        "reports": {
            "HPE": [{"date": "2026-11-23", "time": "post-market", "confirmed": True}],
            "ADI": [{"date": "2026-11-24", "time": "pre-market", "confirmed": False}],
            "NVDA": [{"date": "2026-12-01", "time": "post-market", "confirmed": False, "yahoo_date": "2026-11-25"}],
            "AAPL": [{"date": "2026-11-27", "time": "unknown"}],
            "ZZZ": [{"date": "2026-11-24", "time": "pre-market", "confirmed": True}],
        }}))


class TestCalendarWeek(unittest.TestCase):
    def test_early_closes(self):
        self.assertEqual(cw.early_close(date(2026, 11, 27)), "13:00")      # the day after Thanksgiving
        self.assertEqual(cw.early_close(date(2026, 12, 24)), "13:00")      # Christmas Eve on a Thursday
        self.assertEqual(cw.early_close(date(2025, 7, 3)), "13:00")        # July 3 on a Thursday
        self.assertEqual(cw.early_close(date(2028, 7, 3)), "13:00")        # NYSE's footnote for 2028
        self.assertIsNone(cw.early_close(date(2026, 7, 2)))                # July 4 on a Saturday: July 3 closed, July 2 full
        self.assertIsNone(cw.early_close(date(2026, 7, 3)))
        self.assertIsNone(cw.early_close(date(2027, 12, 24)))              # observed Christmas: closed all day
        self.assertIsNone(cw.early_close(date(2026, 11, 26)))
        self.assertIsNone(cw.early_close(date(2026, 11, 25)))

    def test_expiries(self):
        self.assertEqual(cw.monthly_expiry(2025, 4), date(2025, 4, 17))   # the third Friday was Good Friday
        self.assertEqual(cw.monthly_expiry(2026, 12), date(2026, 12, 18))
        self.assertTrue(any("季度" in s for s in cw.expiries(date(2026, 12, 18))))
        self.assertTrue(any("季末" in s for s in cw.expiries(date(2026, 12, 31))))
        self.assertTrue(any("周期权提前" in s for s in cw.expiries(date(2026, 12, 24))))
        self.assertEqual(cw.expiries(date(2026, 11, 24)), [])

    def test_fomc_table(self):
        ev = cw.fomc_events()
        self.assertEqual(len([e for e in ev if e["kind"] == "fomc" and e["time"] == "14:00"]), len(cw.FOMC))
        for e in ev:
            d = date.fromisoformat(e["date"])
            self.assertLess(d.weekday(), 5, e)
            self.assertTrue(cw.is_session(d), e)
        for first, decide, _sep, minutes, _note in cw.FOMC:
            self.assertEqual(date.fromisoformat(decide) - date.fromisoformat(first), timedelta(days=1))
            if minutes:                                                    # the Fed's published dates keep the three-week rule
                self.assertEqual(date.fromisoformat(minutes) - date.fromisoformat(decide), timedelta(days=cw.MINUTES_LAG))
        self.assertEqual([x[1] for x in cw.FOMC], sorted(x[1] for x in cw.FOMC))

    def test_data_table(self):
        for key, d in cw.DATA.items():
            days = [r[0] for r in d["rows"]]
            self.assertEqual(len(days), 8, key)                            # one a month, 2026-11 to 2027-06
            self.assertEqual(days, sorted(days), key)
            self.assertTrue(d["source"].startswith("https://"), key)
            for x in days:
                self.assertTrue(cw.DATA_FROM <= x <= cw.DATA_TO, (key, x))
                self.assertLess(date.fromisoformat(x).weekday(), 5, (key, x))
                self.assertTrue(cw.is_session(date.fromisoformat(x)), (key, x))

    def test_underlying(self):
        self.assertEqual(cw.underlying("SPY261127C00700000"), "SPY")
        self.assertEqual(cw.underlying("BRK.B"), "BRK.B")
        self.assertEqual(cw.underlying("AAPL"), "AAPL")

    def test_week_of_thanksgiving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            r = cw.run(date(2026, 11, 23), root=root, out_dir=root / "out", today=date(2026, 11, 23))
            days = {x["date"]: x for x in r["days"]}
            self.assertEqual(r["sessions"], 4)
            self.assertFalse(days["2026-11-26"]["open"])
            self.assertEqual(days["2026-11-26"]["holiday"], "感恩节")
            self.assertEqual(days["2026-11-27"]["close"], "13:00")
            self.assertEqual(days["2026-11-24"]["close"], "16:00")
            self.assertEqual(r["early_closes"], ["2026-11-27"])
            lines = [x for x in r["page"].splitlines() if x]
            self.assertTrue(lines[1].startswith("> **注意：11-27 周五 13:00 提前收盘"), lines[1])
            self.assertIn("15:30", lines[1])
            self.assertIn("15:50", lines[1])
            got = {(h["file"], h["time"]) for h in r["hardcoded"]}
            self.assertIn(("hero/sleeves.py", "15:30"), got)
            self.assertIn(("hero/sleeves.py", "15:50"), got)
            self.assertIn(("hero/sleeve_rules.py", "15:30"), got)
            self.assertTrue(all(h["time"] > "13:00" for h in r["hardcoded"]))
            self.assertTrue(any("PCE" in s for s in days["2026-11-25"]["data"]))
            self.assertTrue(any("GDP" in s for s in days["2026-11-25"]["data"]))
            self.assertEqual(days["2026-11-27"]["held_expiring"], {"多账本": ["WMT 130C"]})
            self.assertEqual(days["2026-11-23"]["held_expiring"], {"多账本": ["SPY 713P"]})
            # earnings from the fixture files: our names only, with how sure the calendar is
            rows = {x["symbol"]: x for x in r["earnings"]["rows"]}
            self.assertEqual(set(rows), {"HPE", "ADI", "NVDA", "AAPL"})        # ZZZ is not one of ours
            self.assertEqual((rows["HPE"]["date"], rows["HPE"]["slot"]), ("2026-11-23", "盘后"))
            self.assertEqual(rows["HPE"]["deep"], "research/earnings-forecast/deep/HPE-2026-11-23.md")
            self.assertIn("未确认", rows["ADI"]["trust"])
            self.assertIn("没核对", rows["AAPL"]["trust"])
            self.assertEqual(rows["AAPL"]["why"], ["主账户持有"])
            self.assertEqual(rows["AAPL"]["slot"], "时间未定")
            self.assertTrue(rows["NVDA"]["other_source"])                       # Yahoo's date falls in the week
            self.assertEqual(rows["NVDA"]["calendar_date"], "2026-12-01")
            self.assertEqual(r["earnings"]["not_in_calendar"], ["DIS", "WMT"])  # SPY is a fund
            self.assertIsNone(r["earnings"]["gap"])
            saved = json.loads((root / "out" / "2026-11-23.json").read_text())
            self.assertEqual(saved["week"], "2026-11-23")
            self.assertTrue((root / "out" / "2026-11-23.md").read_text().startswith("# 一周日历：2026-11-23"))

    def test_plain_week_and_weekend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            r = cw.run(date(2026, 11, 28), root=root, write=False, today=date(2026, 11, 28))   # a Saturday: next week
            self.assertEqual(r["week"], "2026-11-30")
            self.assertEqual(r["early_closes"], [])
            self.assertEqual(r["hardcoded"], [])
            self.assertNotIn("注意", r["page"].splitlines()[2])
            self.assertTrue(any("非农" in s for s in r["days"][4]["data"]))
            self.assertEqual(r["next"]["下一次 FOMC 利率决定"], "2026-12-09")
            r = cw.run(date(2027, 3, 15), root=root, write=False, today=date(2026, 11, 23))
            self.assertIsNotNone(r["earnings"]["gap"])                          # past the calendar's 90 days
            r = cw.run(date(2027, 8, 2), root=root, write=False, today=date(2026, 11, 23))
            self.assertTrue(r["gaps"])                                          # past the data tables


if __name__ == "__main__":
    unittest.main()
