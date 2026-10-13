"""Research: on days a stock has no same-day expiry, is buying its nearest expiry (and selling at 15:30) any good?

Since 2026-10-13 the 0DTE sleeves buy the nearest listed expiry within a week when the name has none that day
(owner's instruction). Here that rule on TSLA, NVDA and META (META has no sleeve; asked about on 10-13), every
weekday since 2024-02:
  at 10:00 a call if the stock is above its open, else a put; the nearest expiry from that day on (within a week);
  the first strike at least 0.6% beyond the 10:00 price; a resting 3x / 5x take, else sold at 15:30 that day;
  pay 10% over to get in, get 10% under to get out (SIP stock minutes, option minute bars);
grouped by how far the bought expiry is (same day; 1-2 days; 3+ days), against the same-day days, so the new
days are judged against the old ones and against sitting out (0). Record only. Run: python -m hero.research_zdte_nearest
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_before, et_minutes, trade
from hero.research_0dte_names import minutes_by_day, pick

START = "2024-02-01"
NAMES = ("TSLA", "NVDA", "META")
ENTRY = "10:00"
OFF = 0.006
TAKES = (3, 5)


def gap_group(days: int) -> str:
    return "当天到期" if days == 0 else ("1–2 天后到期" if days <= 2 else "3 天以上到期")


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or not at_or_before(m, "15:30"):
                continue
            spot_bar = at_or_before(m, ENTRY)
            if not spot_bar:
                continue
            spot, day_open = float(spot_bar["c"]), float(m["09:30"]["o"])
            kind = "C" if spot >= day_open else "P"
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                                 expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, OFF)
            if not sym:
                continue
            try:
                bars = client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.05)
            mins = et_minutes(bars.get(sym, []))
            res = {str(t): trade(mins, ENTRY, t) for t in TAKES}
            if any(v is None for v in res.values()):
                continue
            rows.append({"und": und, "day": day, "kind": kind, "exp": exp,
                         "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days,
                         "res": {k: round(v, 4) for k, v in res.items()}})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def cell(xs: list[float], take: int) -> dict | None:
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
            "hit": round(sum(x >= take - 1 for x in xs) / len(xs), 3), "sum": round(sum(xs), 2)}


def table(rep: dict) -> dict:
    out = {}
    for und in NAMES + ("合计",):
        rs = [r for r in rep["rows"] if und == "合计" or r["und"] == und]
        for g in ("当天到期", "1–2 天后到期", "3 天以上到期", "新规则补上的日子（非当天）", "2026 年：新规则补上的日子"):
            if g == "新规则补上的日子（非当天）":
                sub = [r for r in rs if r["dte"] > 0]
            elif g == "2026 年：新规则补上的日子":
                sub = [r for r in rs if r["dte"] > 0 and r["day"] >= "2026-01-01"]
            else:
                sub = [r for r in rs if gap_group(r["dte"]) == g]
            for t in TAKES:
                out[f"{und}|{g}|{t}"] = cell([r["res"][str(t)] for r in sub], t)
    return out


def markdown(rep: dict, t: dict) -> str:
    groups = ("当天到期", "1–2 天后到期", "3 天以上到期", "新规则补上的日子（非当天）", "2026 年：新规则补上的日子")
    out = [f"# 没有当天到期时买最近到期，值不值 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{'、'.join(NAMES)} 每个交易日：10:00 比开盘高买看涨、低买看跌，最近到期（一周内），"
           "离现价至少 0.6%；3 倍 / 5 倍止盈，否则当天 15:30 卖；买入多付 10%、卖出少拿 10%。"
           "「新规则补上的日子」= 当天没有到期、以前会不做的日子；不做就是 0，所以平均为负 = 补上反而亏。"
           "每格「平均每 $1 / 中位数 / 碰到止盈比例（笔数）」。只研究，不改交易。", ""]
    for take in TAKES:
        out += [f"## {take} 倍止盈", "", "| 情况 | " + " | ".join(NAMES + ("合计",)) + " |", "|---|" + "---|" * (len(NAMES) + 1)]
        for g in groups:
            cs = [t.get(f"{u}|{g}|{take}") for u in NAMES + ("合计",)]
            out.append(f"| {g} | " + " | ".join(f"{c['mean']:+.0%} / {c['median']:+.0%} / {c['hit']:.0%}（{c['n']}）" if c else "—"
                                                 for c in cs) + " |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-zdte-nearest.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-zdte-nearest.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
