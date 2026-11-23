"""hero/scoreboard.py: every scorecard recomputed from its raw records (no network: small fixtures in a temp dir)."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from hero import scoreboard as sb

TODAY = date(2026, 11, 23)


def card(day, pc, o, c):
    return {"day": day, "o": round(o, 2), "h": round(max(o, c) + 0.5, 2), "l": round(min(o, c) - 0.5, 2), "c": round(c, 2),
            "pc": round(pc, 2), "gap": round(o / pc - 1, 5), "streak": 1, "above20": True, "rv20": 0.15}


def csv_row(day, kind, pred, result, source=""):
    return {"date": day, "kind": kind, "prediction": pred, "result": result, "source": source}


class AgainstGap(unittest.TestCase):
    def setUp(self):
        self.cards = {
            # earlier days for the base rate: two reversals out of four judged days, one flat day not judged
            "2026-10-01": card("2026-10-01", 100, 100.5, 100.2),   # gap up, closed below the open: against
            "2026-10-02": card("2026-10-02", 100.2, 100.0, 99.5),  # gap down, kept falling: not against
            "2026-10-05": card("2026-10-05", 99.5, 99.0, 99.6),    # gap down, closed above the open: against
            "2026-10-06": card("2026-10-06", 99.6, 100.0, 100.4),  # gap up, kept rising: not against
            "2026-10-07": card("2026-10-07", 100.4, 100.45, 99.0),  # flat open (+0.05%): not judged
            # the scored days
            "2026-11-16": card("2026-11-16", 100, 99.5, 99.0),     # gap -0.5%, kept falling: not against
            "2026-11-17": card("2026-11-17", 99.0, 99.4, 98.9),    # gap +0.4%, closed below the open: against
            "2026-11-18": card("2026-11-18", 98.9, 98.95, 99.5),   # flat open: not judged
            "2026-11-19": card("2026-11-19", 99.5, 101.5, 102.0),  # gap up, kept rising: not against
        }

    def test_reverify_and_report_mismatches(self):
        rows = [csv_row("2026-11-16", "against_gap", "会（低开后收在开盘价上方）", "miss", "日线：开 99.50 收 99.00"),
                csv_row("2026-11-17", "against_gap", "不会（高开后收在开盘价上方）", "hit"),          # wrong: it reversed
                csv_row("2026-11-18", "against_gap", "会", "hit"),                                   # flat: not to be judged
                csv_row("2026-11-19", "against_gap", "不会", "hit", "日线：开 101.00 收 102.00"),   # open recorded wrong
                csv_row("2026-11-19", "prediction", "10 年期美债收益率收盘高于 5%", "hit")]
        r = sb.against_gap(rows, self.cards)
        self.assertEqual(r["n"], 3)
        self.assertEqual([x["hit"] for x in r["rows"]], [False, False, True])
        self.assertEqual(r["ours"], {"hits": 1, "n": 3})
        always_no = r["baselines"][0]
        self.assertEqual((always_no["hits"], always_no["n"], always_no["beat"]), (2, 3, False))
        self.assertAlmostEqual(r["baselines"][1]["rate"], 0.5)     # 2 reversals in the 4 judged earlier days
        text = "\n".join(r["discrepancies"])
        self.assertIn("2026-11-17", text)
        self.assertIn("重算是错，记的是 hit", text)
        self.assertIn("2026-11-18", text)
        self.assertIn("本该不判", text)
        self.assertIn("记的开 101.00 收 102.00", text)
        self.assertNotIn("2026-11-16", text)
        self.assertEqual(len(r["discrepancies"]), 3)

    def test_spy_direction_against_the_gap(self):
        rows = [csv_row("2026-11-16", "prediction", "SPY 今天收盘下跌", "hit"),
                csv_row("2026-11-17", "prediction", "SPY 收盘高于昨收 99.0", "hit"),                  # closed lower: a miss
                csv_row("2026-11-19", "prediction", "SPY 今天收盘涨跌幅在 ±1% 以内", "hit"),          # +2.5%: a miss
                csv_row("2026-11-19", "prediction", "WTI 近月期货结算价低于 $85", "hit")]
        r = sb.spy_direction(rows, self.cards)
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["ours"]["hits"], 1)
        self.assertEqual(r["baselines"][0]["hits"], 1)              # follow the gap: right on 11-16, wrong on 11-17
        text = "\n".join(r["discrepancies"])
        self.assertIn("2026-11-17", text)
        self.assertIn("±1%", text)
        self.assertEqual(len(r["discrepancies"]), 2)


TOMORROW = """# 「明天会怎样」的打分

| 预测日 | 晚上版：大波动 / p_up | 早上版：大波动 / p_up | 实际涨跌幅 | 实际方向 | 大波动（>1%）发生了吗 |
|---|---|---|---|---|---|
| 11-02（预测 11-03） | 40% / 60% | 50% / 70% | +1.50% | 涨 | 发生了 |
| 11-03（预测 11-04） | 20% / 55% | 30% / 40% | −0.30% | 跌 | 没有 |
| 11-04（预测 11-05） | 30% / 55% | 周四早上补 | 周四晚补 | 周四晚补 | 周四晚补 |
| 11-05 | 30% / 55% | 30% / 55% | — | — | — |

## Brier 分数（到目前为止，3 天）

| 版本 | 大波动 | 方向（收盘对昨收） | 开到收 |
|---|---|---|---|
| 晚上版 | 0.297 | 0.300 | — |
| 早上版 | 0.167 | 0.124 | 0.218 |
| 一律 25% 基线 | 0.396 | — | — |
| 缺口死规则（高开 68%、低开 30%） | — | 0.094 | — |

第三天（11-05，测试）：实际 −2.00%、收跌、开到收 −1.50%。大波动发生了：晚上版 30%、早上版 60%（随便）。方向 晚上版 55%、早上版 35%（随便）。开到收：我填 45%（随便）。
"""


class Tomorrow(unittest.TestCase):
    def setUp(self):
        c3 = 101.5 * 0.998
        c5 = c3 * 0.98
        self.cards = {"2026-11-03": card("2026-11-03", 100.0, 100.5, 101.5),     # +1.5%, big, up, open-to-close up
                      "2026-11-04": card("2026-11-04", 101.5, 101.0, c3),         # -0.2%, gap down, open-to-close up
                      "2026-11-05": card("2026-11-05", c3, c5 / 0.985, c5)}       # -2.0%, gap down, open-to-close down

    def test_brier_against_hand_numbers(self):
        r = sb.tomorrow(TOMORROW, self.cards, TODAY)
        got = {s["label"]: s for s in r["ours"]}
        self.assertAlmostEqual(got["大波动（晚上版）"]["brier"], round((0.36 + 0.04 + 0.49) / 3, 4))
        self.assertAlmostEqual(got["大波动（早上版）"]["brier"], round((0.25 + 0.09 + 0.16) / 3, 4))
        self.assertAlmostEqual(got["方向（晚上版）"]["brier"], round((0.16 + 0.3025 + 0.3025) / 3, 4))
        self.assertAlmostEqual(got["方向（早上版）"]["brier"], round((0.09 + 0.16 + 0.1225) / 3, 4))
        self.assertAlmostEqual(got["开到收（早上）"]["brier"], round((0.09 + 0.36 + 0.2025) / 3, 4))
        base = {b["name"]: b["brier"] for b in got["大波动（晚上版）"]["baselines"]}
        self.assertAlmostEqual(base["一律 25%"], round((0.5625 + 0.0625 + 0.5625) / 3, 4))
        dir_base = {("gap" if b["name"].startswith("缺口") else "55" if b["name"].startswith("一律 55%") else "hindsight"): b["brier"]
                    for b in got["方向（早上版）"]["baselines"]}
        self.assertAlmostEqual(dir_base["gap"], round((0.1024 + 0.09 + 0.09) / 3, 4))
        self.assertAlmostEqual(dir_base["55"], round((0.2025 + 0.3025 + 0.3025) / 3, 4))
        self.assertAlmostEqual(dir_base["hindsight"], round(1 / 3 * 2 / 3, 4))      # one up day in three: r(1 - r)

    def test_mismatches_and_unparsed_rows(self):
        r = sb.tomorrow(TOMORROW, self.cards, TODAY)
        text = "\n".join(r["discrepancies"])
        self.assertIn("「晚上版」的方向：记的 0.300，重算 0.255", text)
        self.assertIn("2026-11-04：记的实际涨跌 -0.30%，日线 -0.20%", text)
        for ok in ("「晚上版」的大波动", "「早上版」", "一律 25%", "缺口死规则"):
            self.assertNotIn(ok, text)
        self.assertEqual(len(r["discrepancies"]), 2)
        self.assertTrue(any("11-05" in u for u in r["unparsed"]))   # the row without 「预测」
        self.assertFalse(any("2026-11-05 的早上版" in u for u in r["unparsed"]))   # filled from the paragraph


FOLLOW = """# 跟单卡记分

| 日期 | 结论 | 10:00 SPY 离开盘 | 「一定要跟」的单子 | 每 $1 | 例外触发了吗 |
|---|---|---|---|---|---|
| 11-02 | 没发（例程晚到），不计 | +0.11% | 没发（事后看 763 看涨约 −0.56） | — | 没有 |
| 11-03 | 不跟，$0 | +0.05% | 756 看涨 $0.89 → $0.15 | −0.83 | 没有 |
| 11-04 | 有条件地跟：触发，+2.0 | +0.70% | 771 看涨 $0.71 → $2.13 | **+2.0** | **触发**（第一次） |
| 11-05 | 有条件地跟：触发，+1.5 | −0.60% | 743 认沽 $1.68（卡片写最多 $1.50、超过 $1.90 不做）→ $4.20 | **+1.5** | **触发**（第 2 次） |
| 11-06 | 有条件地跟：擦线，按卡片的话不做，$0（照线做是 −0.99） | +0.51% | 721 看涨 $2.30 → $0.01 | −1.00 | 擦线（+0.51%） |
| 11-09 | 有条件地跟：没触发，$0 | +0.80% | 750 看涨 $1.04 → $0.20 | −0.81 | 没有 |

## 合计（5 天）

- 「结论」：触发 2 次：11-04 +2.0、11-05 +1.5，合计 +3.50；两处都从严（11-05 不算、11-06 算亏）是 +1.01；只按线机械地记是 +2.51。
- 「一定要跟」：2 赚 3 亏，每 $1 合计 **+1.00**。
"""


class Follow(unittest.TestCase):
    def test_three_readings_and_recorded_totals(self):
        r = sb.follow(FOLLOW, TODAY)
        t = r["ours"]
        # strict: 11-05 bought above the card's own $1.50 limit is not counted, the 11-06 graze is booked as the loss
        self.assertEqual(t["concl"], {"booked": 3.5, "strict": round(2.0 - 0.99, 2), "mech": round(2.0 + 1.5 - 0.99, 2)})
        self.assertEqual(t["per"]["booked"], round(-0.83 + 2.0 + 1.5 - 1.0 - 0.81, 2))
        self.assertEqual(t["per"]["strict"], round(-0.83 + 2.0 + 0.0 - 1.0 - 0.81, 2))   # the broken limit is not bought
        self.assertEqual(t["per"]["with_missed"], round(t["per"]["booked"] - 0.56, 2))
        text = "\n".join(r["discrepancies"])
        self.assertIn("2026-11-09", text)                       # +0.80% at 10:00 but booked as not triggered
        self.assertIn("「一定要跟」合计", text)                 # +1.00 recorded, +0.86 recomputed
        self.assertNotIn("结论从严", text)
        self.assertNotIn("结论按线机械地记", text)
        self.assertEqual(len(r["discrepancies"]), 2)


PMDIR = """# 午盘的下午方向（pm_dir）记分

| 日期 | 12:00 的 SPY | 填的 | 收盘 | 12:00 到收盘 | 对错 | 写的时候的 SPY | 写的时候到收盘 | 按写的时候 |
|---|---|---|---|---|---|---|---|---|
| 11-16 | 100.00 | 跌 | 99.00 | −1.00% | 对 | 约 99.5 | −0.50% | 对 |
| 11-17 | 99.00 | 涨 | 98.90 | −0.10% | 对 | 98.80 | +0.10% | 对 |

累计：2 次，按 12:00 的价 2 对 0 错；一律填「涨」是 0 对 2 错。按写的那一刻的价是 2 对 0 错。
"""


class PmDir(unittest.TestCase):
    def test_recomputed_from_the_tables_own_prices(self):
        cards = {"2026-11-16": card("2026-11-16", 101, 100.5, 99.0), "2026-11-17": card("2026-11-17", 99.0, 99.2, 98.9)}
        r = sb.pm_dir(PMDIR, cards, TODAY)
        self.assertEqual(r["ours"], {"at_write": [2, 2], "at_noon": [1, 2]})
        text = "\n".join(r["discrepancies"])
        self.assertIn("2026-11-17：12:00 99.0 → 收 98.9", text)
        self.assertIn("按 12:00 记的是 2 对 0 错，重算是 1 对 1 错", text)
        self.assertEqual(len(r["discrepancies"]), 2)


def write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


def fake_bars(table):
    """A stand-in for Yahoo: symbol -> {day: (open, close)}; a symbol not in the table fails like a dead network."""
    def get(sym, start, end):
        if sym not in table:
            raise OSError("no network in tests")
        return {d: v for d, v in table[sym].items() if start.isoformat() <= d <= end.isoformat()}
    return get


BARS = {"DIS": {"2026-11-14": (118.4, 118.97), "2026-11-16": (119.6, 118.70), "2026-11-17": (118.26, 116.94)},
        "WIDG": {"2026-11-13": (20.0, 20.0), "2026-11-16": (21.0, 21.5)},
        "MRVL": {"2026-11-17": (150.20, 148.74)}}


def fixture_root(root: Path) -> None:
    write(root, "data/daybook/spy.json", {"built": "2026-11-20", "cards": [
        card("2026-11-13", 100.0, 100.2, 100.5), card("2026-11-16", 100.5, 100.0, 99.0), card("2026-11-17", 99.0, 98.8, 98.0)]})
    write(root, "macro/briefings/scores.csv", "date,kind,prediction,result,source\n"
          "2026-11-16,against_gap,会（低开后收在开盘价上方）,miss,日线：开 100.00 收 99.00\n"
          "2026-11-17,prediction,SPY 收盘高于昨收 99.0,hit,日线\n")
    write(root, "exchange/claude/pm-dir-scores.md", PMDIR)
    write(root, "exchange/claude/tomorrow-scores.md", "# 没有表\n")
    write(root, "posts/follow/scorecard.md", FOLLOW)
    base = "research/earnings-forecast/"
    write(root, base + "2026-11-16.judgment.json", {"date": "2026-11-16", "calls": [
        {"symbol": "DIS", "direction": "down", "evidence": "profile"}, {"symbol": "WIDG", "direction": "up", "evidence": "profile"},
        {"symbol": "GONE", "direction": "up", "evidence": "profile"}]})
    write(root, base + "2026-11-12.judgment.json", {"date": "2026-11-12", "calls": [{"symbol": "OLD", "direction": "up"}]})
    write(root, base + "2026-11-16.json", {"reports": [
        {"symbol": "DIS", "reaction_day": "2026-11-17", "result": {"gap": 0.001}},     # the first minute bar, not the daily open
        {"symbol": "WIDG", "reaction_day": "2026-11-16", "result": {"gap": 0.05}},
        {"symbol": "GONE", "reaction_day": "2026-11-17", "result": None}]})
    write(root, base + "reasons.jsonl", json.dumps({"date": "2026-11-16", "symbol": "DIS", "reason": "开盘 −0.4%、收盘 −1.5%"},
                                                   ensure_ascii=False) + "\n")
    write(root, "journal-s500/sniper/2026-11-17.json", {"date": "2026-11-17", "sniper": {
        "symbol": "MRVL", "direction": "call", "order": "second", "wrong_if": "10:00 时在开盘价下方；当天收盘收回开盘价"}})
    write(root, "journal-s500/sniper/2026-10-30.json", {"date": "2026-10-30", "sniper": {"symbol": "OLD", "direction": "call"}})
    write(root, "exchange/claude/2026-11-17.close.json", {"sniper": {
        "pick": "MRVL 看涨", "all_day_line": "「当天收盘收回开盘价」没发生：收 $148.74（开到收 +0.97%）"}})
    write(root, "research/weekbook/ledger.json", {"reads": [{"week": "2026-11-23", "written": "2026-11-23T10:52-05:00",
                                                             "labels": {}, "p": {"big": 0.58}}]})


class Network(unittest.TestCase):
    def test_earnings_daily_open_and_failed_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture_root(root)
            cards = sb.load_cards(root)
            r = sb.earnings(root, cards, sb.Fetcher(fake_bars(BARS)), TODAY)
            got = {x["symbol"]: x for x in r["rows"]}
            self.assertNotIn("OLD", got)                                # before 11-13: not a name we care about
            self.assertAlmostEqual(got["DIS"]["gap"], 118.26 / 118.70 - 1, places=5)
            self.assertTrue(got["DIS"]["hit"])
            self.assertAlmostEqual(got["DIS"]["spy_gap"], 98.8 / 99.0 - 1, places=4)
            self.assertAlmostEqual(got["WIDG"]["gap"], 0.05)
            self.assertFalse(got["GONE"]["fetched"])
            self.assertEqual(r["ours"], {"hits": 2, "n": 2})
            self.assertTrue(any("GONE" in u and "没取到" in u for u in r["unparsed"]))
            text = "\n".join(r["discrepancies"])
            self.assertIn("DIS：预测文件 result.gap +0.1%", text)       # the minute-bar open is reported
            self.assertNotIn("reasons.jsonl", text)                     # the corrected -0.4% agrees
            self.assertEqual(len(r["discrepancies"]), 1)

    def test_sniper_open_to_close_and_recorded_lines(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture_root(root)
            r = sb.sniper(root, sb.Fetcher(fake_bars(BARS)), TODAY)
            self.assertEqual(r["n"], 1)
            x = r["rows"][0]
            self.assertFalse(x["match"])
            self.assertTrue(x["all_day_line"])
            self.assertEqual(r["ours"]["by_order"], {"second": [0, 1]})
            text = "\n".join(r["discrepancies"])
            self.assertIn("开到收 +0.97%，日线 -0.97%", text)
            self.assertIn("记的是没发生", text)

    def test_dead_network_degrades(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture_root(root)
            fetch = sb.Fetcher(fake_bars({}))
            r = sb.sniper(root, fetch, TODAY)
            self.assertEqual(r["n"], 0)
            self.assertIn("没取到", r["lines"][-1])
            e = sb.earnings(root, sb.load_cards(root), fetch, TODAY)
            self.assertEqual(e["n"], 0)
            self.assertGreaterEqual(fetch.fails, 3)                       # stopped trying


class Build(unittest.TestCase):
    def test_run_writes_page_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture_root(root)
            rep = sb.run(root, TODAY, sb.Fetcher(fake_bars(BARS)))
            md = (root / "research" / "scoreboard" / "2026-11-23.md").read_text()
            js = json.loads((root / "research" / "scoreboard" / "2026-11-23.json").read_text())
            self.assertTrue(md.startswith("# 记分卡总账：2026-11-23"))
            self.assertLess(md.index("## 先看"), md.index("## 一张表"))
            self.assertIn("不够（", md)
            self.assertIn("没有能打分的", md)                             # the week of 11-23 is not over
            self.assertEqual([c["key"] for c in js["cards"]],
                             ["against_gap", "spy_direction", "pm_dir", "tomorrow", "follow", "earnings", "sniper", "weekbook"])
            self.assertNotIn("lines", js["cards"][0])
            n_disc = sum(len(c["discrepancies"]) for c in rep["cards"])
            self.assertIn(f"（{n_disc} 处）", md)
            self.assertEqual(js["cards"][3]["n"], 0)                    # a tomorrow page without its tables scores nothing


if __name__ == "__main__":
    unittest.main()
