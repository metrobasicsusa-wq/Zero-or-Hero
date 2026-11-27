import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from hero import calendar_week, lottery, session, sleeve_rules, sleeves, weekbook, zero_dte

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


class TestSessionDays(unittest.TestCase):
    """11-27: "the session before / after" skipped only weekends, so the Friday 11-27 expiries meant to be sold the day
    before (Wednesday 11-25) were dated Thanksgiving and sold the Friday morning."""

    def test_holidays_match_the_research_calendar(self):
        for y in range(2015, 2036):
            self.assertEqual(session.holidays(y), weekbook.holidays(y), y)
        d = date(2015, 1, 1)
        while d < date(2036, 1, 1):
            self.assertEqual(session.is_session(d), calendar_week.is_session(d), d)
            d += timedelta(days=1)

    def test_next_and_previous_session_skip_holidays(self):
        self.assertEqual(session.prev_session(date(2026, 11, 27)), date(2026, 11, 25))   # Thanksgiving
        self.assertEqual(session.next_session(date(2026, 11, 25)), date(2026, 11, 27))
        self.assertEqual(session.next_session(date(2026, 12, 24)), date(2026, 12, 28))   # Christmas on a Friday
        self.assertEqual(session.next_session(date(2026, 12, 31)), date(2027, 1, 4))     # New Year's Day on a Friday
        self.assertEqual(session.prev_session(date(2026, 4, 6)), date(2026, 4, 2))       # Good Friday
        self.assertTrue(session.is_session(date(2021, 12, 31)))                         # New Year's on a Saturday: open
        self.assertTrue(session.is_session(date(2021, 6, 18)))                          # before Juneteenth was a holiday
        self.assertTrue(session.is_session(date(2026, 11, 27)))                         # early close is a session
        self.assertEqual(session.next_session(date(2026, 11, 20)), date(2026, 11, 23))  # plain weekend

    def test_the_rules_use_it(self):
        self.assertEqual(sleeve_rules.business_day_before(date(2026, 11, 27)), date(2026, 11, 25))
        self.assertEqual(sleeve_rules.next_business_day(date(2026, 11, 25)), date(2026, 11, 27))
        self.assertEqual(lottery.schedule({"date": "2026-11-25", "time": "post-market"})[1], date(2026, 11, 27))
        self.assertEqual(lottery.schedule({"date": "2026-11-27", "time": "pre-market"})[0], date(2026, 11, 25))


if __name__ == "__main__":
    unittest.main()
