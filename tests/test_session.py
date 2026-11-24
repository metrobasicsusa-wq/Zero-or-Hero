import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from hero import calendar_week, lottery, session, sleeves, zero_dte

ET = ZoneInfo("America/New_York")


def at(d, hh, mm):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


class TestSessionClock(unittest.TestCase):
    def test_early_closes_match_the_calendar_module(self):
        d = date(2026, 1, 1)
        while d < date(2033, 1, 1):
            if calendar_week.is_session(d):
                self.assertEqual(session.early_close(d), calendar_week.early_close(d), d)
            d += timedelta(days=1)
        self.assertEqual(session.early_close(date(2026, 11, 27)), "13:00")
        self.assertEqual(session.early_close(date(2026, 12, 24)), "13:00")
        self.assertIsNone(session.early_close(date(2026, 11, 24)))

    def test_clock_shifts_only_the_afternoon_of_an_early_close(self):
        half, full = date(2026, 11, 27), date(2026, 11, 25)
        self.assertEqual(session.clock(at(half, 9, 35)), "09:35")
        self.assertEqual(session.clock(at(half, 11, 59)), "11:59")
        self.assertEqual(session.clock(at(half, 12, 0)), "15:00")
        self.assertEqual(session.clock(at(half, 12, 30)), "15:30")
        self.assertEqual(session.clock(at(half, 12, 50)), "15:50")
        self.assertEqual(session.clock(at(half, 13, 0)), "16:00")
        self.assertEqual(session.clock(at(full, 12, 30)), "12:30")
        self.assertEqual(session.clock(at(full, 15, 30)), "15:30")

    def test_the_three_readers_use_it(self):
        half, full = date(2026, 11, 27), date(2026, 11, 25)
        self.assertEqual(zero_dte.hhmm(at(half, 12, 30)), "15:30")
        self.assertTrue(lottery.entry_time(at(half, 12, 31)))
        self.assertFalse(lottery.entry_time(at(half, 11, 30)))
        self.assertFalse(lottery.entry_time(at(full, 15, 29)))
        self.assertTrue(lottery.entry_time(at(full, 15, 30)))
        run = sleeves.Runner(None, None, {}, [], None, {}, at(half, 12, 50), half)
        self.assertEqual(run.hhmm, "15:50")
        self.assertGreaterEqual(run.hhmm, sleeves.LAST_CYCLE)
        self.assertEqual(sleeves.Runner(None, None, {}, [], None, {}, at(full, 12, 50), full).hhmm, "12:50")


if __name__ == "__main__":
    unittest.main()
