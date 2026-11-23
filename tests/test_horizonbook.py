"""hero/horizonbook.py: the weekbook for eleven symbols, and a book for the next 63 sessions. No network: the bars are
made up, calm and wild stretches with the direction a coin toss."""

import json
import random
import statistics
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import horizonbook as hb
from hero import weekbook as wb


def bars(years: int = 16, seed: int = 7, wild_every: int = 16) -> list[tuple]:
    """(day, open, high, low, close) on real New York trading days from 2010: four wild months in every `wild_every`
    (daily steps of 2.5%), calm otherwise (0.2%), each step up or down by a coin toss."""
    rnd, out, px, mon = random.Random(seed), [], 100.0, date(2010, 1, 4)
    while mon < date(2010 + years, 1, 1):
        for d in wb.sessions(mon):
            wild = ((d.year - 2010) * 12 + d.month - 1) % wild_every >= wild_every - 4
            step = rnd.choice((-1, 1)) * (0.025 if wild else 0.002)
            o = px * (1 + rnd.choice((-1, 1)) * 0.001)
            c = px * (1 + step)
            out.append((d.isoformat(), round(o, 4), round(max(o, c) * 1.001, 4), round(min(o, c) * 0.999, 4), round(c, 4)))
            px = c
        mon += timedelta(days=7)
    return out


def flat_vix(bs: list[tuple], level: float = 20.0) -> dict[str, float]:
    return {b[0]: level for b in bs}


def first_session(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() >= 5 or d in wb.holidays(y):
        d += timedelta(days=1)
    return d


class Inputs(unittest.TestCase):
    def test_volatility_is_measured_against_the_symbols_own_normal(self):
        rnd = random.Random(3)
        steady, px = [], 100.0
        for _ in range(600):                                          # always wild: wild is this symbol's normal
            px *= 1 + rnd.choice((-1, 1)) * 0.025
            steady.append(px)
        rel = hb.rel_vol(steady)
        self.assertIsNone(rel[hb.FIRST - 1])
        self.assertEqual(hb.bucket(rel[-1], hb.RVOLS), hb.RVOLS[1][1])  # 40% a year, and calm for it
        jump = steady[:500] + [steady[499] * (1 + 0.08 * (-1) ** k) for k in range(1, 21)]
        self.assertEqual(hb.bucket(hb.rel_vol(jump)[-1], hb.RVOLS), hb.RVOLS[3][1])   # three times its normal
        self.assertEqual(hb.bucket(0.8, hb.RVOLS), hb.RVOLS[1][1])
        self.assertEqual(hb.bucket(-0.15, hb.Q_LAST), "前三个月跌 5–15%")
        self.assertEqual(hb.bucket(-0.31, hb.Q_HIGHS), "离一年高点 30% 以上")

    def test_unfinished_bars_and_the_calendar(self):
        bs = [("2026-11-20", 1, 1, 1, 1), ("2026-11-23", 1, 1, 1, 1)]
        morning = datetime(2026, 11, 23, 11, 0, tzinfo=hb.db.ET)
        evening = datetime(2026, 11, 23, 16, 30, tzinfo=hb.db.ET)
        self.assertEqual([b[0] for b in hb.finished(bs, date(2026, 11, 23), morning)], ["2026-11-20"])
        self.assertEqual(len(hb.finished(bs, date(2026, 11, 23), evening)), 2)
        self.assertEqual(len(hb.finished(bs, date(2026, 11, 20), evening)), 1)
        self.assertEqual(hb.add_sessions("2026-11-20", 1), "2026-11-23")
        self.assertEqual(hb.add_sessions("2026-11-25", 1), "2026-11-27")          # Thanksgiving

    def test_a_symbol_yahoo_gives_no_quotes_for_is_skipped_with_a_note(self):
        good = bars(2)

        def fetch(sym, interval, start, end):
            if sym == "BAD":
                raise KeyError("open")
            et = hb.db.ET
            return [(datetime.fromisoformat(d).replace(hour=9, minute=30, tzinfo=et), o, h, l, c) for d, o, h, l, c in good]

        data, notes = hb.load(("GOOD", "BAD"), datetime(2012, 1, 1, tzinfo=hb.db.ET), fetch)
        self.assertEqual(list(data), ["GOOD"])
        self.assertEqual(data["GOOD"][0], good[0])
        self.assertEqual(len(notes), 1)
        self.assertIn("BAD", notes[0])


class QuarterBook(unittest.TestCase):
    def setUp(self):
        self.bars = bars()
        self.days = [b[0] for b in self.bars]
        self.qs = hb.quarter_samples(self.bars, flat_vix(self.bars))

    def test_samples_enter_on_the_first_session_of_each_month(self):
        entries = [s["key"] for s in self.qs]
        self.assertGreater(len(entries), 150)
        for e in entries:
            d = date.fromisoformat(e)
            self.assertEqual(d, first_session(d.year, d.month))
        months = [(date.fromisoformat(e).year, date.fromisoformat(e).month) for e in entries]
        for (y0, m0), (y1, m1) in zip(months, months[1:]):
            self.assertEqual(y1 * 12 + m1, y0 * 12 + m0 + 1)              # one sample a month, none missing
        self.assertGreaterEqual(self.days.index(entries[0]), hb.FIRST)   # a year of volatility behind the first

    def test_the_outcome_is_the_close_63_sessions_later(self):
        closes = [b[4] for b in self.bars]
        for s in self.qs[::17]:
            e = self.days.index(s["key"])
            if e + hb.H >= len(self.bars):
                self.assertIsNone(s["done"])
                continue
            self.assertEqual((s["pc"], s["end"], s["done"]), (closes[e], closes[e + hb.H], self.days[e + hb.H]))
            self.assertEqual(s["low"], min(closes[e + 1:e + hb.H + 1]))
            self.assertAlmostEqual(s["last"], closes[e] / closes[e - hb.H] - 1, places=4)
            self.assertAlmostEqual(s["dd"], closes[e] / max(closes[e - 251:e + 1]) - 1, places=4)
            self.assertEqual(s["above200"], closes[e] >= statistics.mean(closes[e - 199:e + 1]))
        unfinished = [s for s in self.qs if not s["done"]]
        self.assertIn(len(unfinished), (2, 3, 4))                        # the last three months or so are still running
        q = {k: f for k, (_, f) in hb.QUARTER.items()}
        dip = {"pc": 100.0, "end": 103.0, "low": 88.0}                   # fell 12% on the way, ended up 3%
        self.assertEqual([q[k](dip) for k in ("up", "big", "up10", "down10", "touch10", "touch20", "flat5")],
                         [True, False, False, False, True, False, True])
        crash = {"pc": 100.0, "end": 79.0, "low": 75.0}
        self.assertEqual([q[k](crash) for k in ("up", "big", "down10", "touch20", "flat5")], [False, True, True, True, False])

    def test_only_samples_finished_by_the_read_close_are_used(self):
        labs = [hb.quarter_labels(s) for s in self.qs]
        book = hb.Book(self.qs, labs, hb.KEYS["quarter"], hb.QUARTER)
        k = 80
        at = self.qs[k]["day"]
        r = book.read(labs[k], at)
        self.assertTrue(r["idx"])
        self.assertTrue(all(self.qs[i]["done"] <= at for i in r["idx"]))
        self.assertNotIn(k - 1, r["idx"])                                 # entered a month ago: not finished
        self.assertNotIn(k - 2, r["idx"])
        idx, cnt = book.usable((0,), at)
        self.assertEqual(idx, [i for i, s in enumerate(self.qs) if s["done"] and s["done"] <= at])
        self.assertEqual(cnt["up"][1], len(idx))
        j = 50                                                            # a sample finishing on the read close counts
        idx, _ = book.usable((0,), self.qs[j]["done"])
        self.assertIn(j, idx)
        self.assertNotIn(j + 1, idx)
        late = book.read(labs[-1], "9999-12-31")                          # unfinished samples are never used
        self.assertTrue(all(self.qs[i]["done"] for i in late["idx"]))
        sc = hb.score(book, hb.WARM["quarter"])
        scored = [i for i, s in enumerate(self.qs) if i >= hb.WARM["quarter"] and s["done"]]
        self.assertEqual(sc["up"]["n"], len(scored))
        self.assertEqual(sc["up"]["from"], self.qs[hb.WARM["quarter"]]["key"])

    def test_a_read_matches_counting_by_hand(self):
        labs = [hb.quarter_labels(s) for s in self.qs]
        book = hb.Book(self.qs, labs, hb.KEYS["quarter"], hb.QUARTER)
        keys = hb.KEYS["quarter"]
        for i in range(40, len(self.qs), 7):
            at = self.qs[i]["day"]
            ok = [j for j, s in enumerate(self.qs) if s["done"] and s["done"] <= at]
            for n in range(len(keys), 0, -1):
                idx = [j for j in ok if all(labs[j].get(k) == labs[i][k] for k in keys[:n])]
                if len(idx) >= hb.MIN_N or n == 1:
                    break
            r = book.read(labs[i], at)
            self.assertEqual(r["idx"], idx)
            for q, (_, f) in hb.QUARTER.items():
                b = sum(f(self.qs[j]) for j in ok) / len(ok)
                self.assertAlmostEqual(r["q"][q]["p"], (sum(f(self.qs[j]) for j in idx) + hb.PRIOR * b) / (len(idx) + hb.PRIOR), places=4)

    def test_size_can_be_learnt_and_direction_cannot(self):
        st = hb.study(self.bars, flat_vix(self.bars), date(2026, 1, 5))
        sc = st["quarter"]["score"]
        self.assertTrue(sc["big"]["pass"])                               # wild months follow wild months
        self.assertTrue(sc["flat5"]["pass"])
        self.assertFalse(sc["up"]["pass"])                               # a coin toss stays a coin toss
        self.assertEqual(st["quarter"]["read"]["kept"][0], "vol")
        for seed in (1, 2, 3):
            other = hb.study(bars(seed=seed), flat_vix(self.bars), date(2026, 1, 5))
            self.assertFalse(other["quarter"]["score"]["up"]["pass"])
            self.assertFalse(other["week"]["score"]["up"]["pass"])


class WeekBook(unittest.TestCase):
    def test_a_week_knows_only_the_closes_before_it(self):
        bs = bars(4)
        days = [b[0] for b in bs]
        rel = hb.rel_vol([b[4] for b in bs])
        ws = hb.week_samples(bs, flat_vix(bs), "2013-06-03", rel)
        self.assertTrue(all(w["week"] < "2013-06-03" for w in ws))
        w = ws[10]
        pre = days.index(w["day"])
        self.assertEqual(days[pre + 1], [d.isoformat() for d in wb.sessions(date.fromisoformat(w["week"]))][0])
        self.assertEqual((w["pc"], w["rvrel"]), (bs[pre][4], rel[pre]))
        self.assertEqual(w["done"], wb.sessions(date.fromisoformat(w["week"]))[-1].isoformat())
        lab = hb.week_labels(w)
        self.assertEqual(set(lab), set(wb.KEYS))
        self.assertIn(lab["vol"], [name for _, name in hb.RVOLS])

    def test_size_can_be_learnt_and_direction_cannot(self):
        bs = bars()
        st = hb.study(bs, flat_vix(bs), date(2026, 1, 5))
        sc = st["week"]["score"]
        self.assertTrue(sc["big"]["pass"])
        self.assertTrue(sc["range"]["pass"])
        self.assertFalse(sc["up"]["pass"])
        self.assertFalse(sc["vix_down"]["pass"])                         # a flat VIX never falls: nothing to learn
        self.assertEqual(sc["big"]["from"], st["week"]["book"].samples[hb.WARM["week"]]["week"])
        r = st["week"]["read"]
        self.assertGreaterEqual(r["n"], hb.MIN_N)
        self.assertEqual(st["week"]["probe"]["day"], bs[-1][0])          # the read close: the last before the week

    def test_with_the_weekbooks_labels_it_scores_exactly_like_the_weekbook(self):
        bs = bars(10)
        vix = {b[0]: 15.0 + 10 * (k // 40 % 2) for k, b in enumerate(bs)}
        ws = hb.week_samples(bs, vix, "2030-01-01", [0.0] * len(bs))
        old = wb.score(wb.Book([{k: v for k, v in w.items() if k not in ("key", "day", "done", "rvrel")} for w in ws]))
        new = hb.score(hb.Book(ws, [wb.labels(w) for w in ws], wb.KEYS, wb.OUTCOMES), wb.WARM)
        self.assertEqual(old, new)


class Run(unittest.TestCase):
    def test_a_run_writes_the_page_and_each_read_down_once(self):
        data = {"AAA": bars(), "BBB": bars(seed=8)}
        vix = flat_vix(data["AAA"], 18.0)
        last = date.fromisoformat(data["AAA"][-1][0])
        sat = last + timedelta(days=(5 - last.weekday()) % 7)
        now = datetime(sat.year, sat.month, sat.day, 10, 0, tzinfo=hb.db.ET)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            got = hb.run(sat, data, vix, ["CCC：Yahoo 没有给报价，这次跳过。"], out_dir=out, now=now)
            self.assertTrue((out / f"{sat.isoformat()}.md").exists())
            rep = json.loads((out / f"{sat.isoformat()}.json").read_text())
            self.assertEqual(set(rep["symbols"]), {"AAA", "BBB"})
            self.assertEqual(rep["week"], (sat + timedelta(days=2)).isoformat())
            for word in ("这一周", "这三个月", "过线", "描述", "CCC", "三分之二"):
                self.assertIn(word, got["page"])
            hb.run(sat, data, vix, out_dir=out, now=now)
            reads = json.loads((out / "ledger.json").read_text())["reads"]
            self.assertEqual(sorted((x["symbol"], x["horizon"]) for x in reads),
                             [("AAA", "quarter"), ("AAA", "week"), ("BBB", "quarter"), ("BBB", "week")])
            q = next(x for x in reads if x["horizon"] == "quarter")
            self.assertEqual((q["period"], q["from"]), (data["AAA"][-1][0][:7], data["AAA"][-1][0]))
            self.assertEqual(set(q["p"]), set(hb.QUARTER))


if __name__ == "__main__":
    unittest.main()
