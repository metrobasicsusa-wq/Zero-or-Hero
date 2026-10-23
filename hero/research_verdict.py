"""Verdict table: every ledger against the backtest that already answers it (owner, 10-23: 「自己研究下 想加快进度 因为环境是实时变化的」).

The live and plan ledgers were being used to wait six weeks for answers the research folder already holds
(research/2026-10-07-0dte-names.json had the six new 0DTE names, 2026-10-13-zdte-timing.json the 09:35 entry).
For each ledger config this maps the exact rule parameters to the matching per-trade series of an existing study,
then reports hero/robust.py's checks plus two things the ledgers need: the last month on its own (the regime moves
faster than a 2024-to-now mean), and the backtest's own stop line -- the 20th percentile of 10-consecutive-trade sums --
so a live ledger can be judged after 10 trades instead of 100: below it, it is doing worse than four fifths of history.
The live ledger's trades so far sit next to it. No API: reads research/*.json and journal-s500/sleeves.json.
Research only: it changes no rule. Verdict wording: 回测就不行 (trimmed mean and the last 3 months both negative),
回测可以 (both positive), 回测分歧 (otherwise)."""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
WINDOW = 10        # trades in a stop-line window
PCT = 0.2          # the stop line: this fraction of history's windows did worse

Series = list[tuple[str, float]]


def load(name: str) -> dict:
    return json.loads((ROOT / "research" / name).read_text())


# ---- the series behind each ledger ----
def minmove(doc: dict, und: str, entry: str, off: float, exit_: str, min_move: float = 0.0) -> Series:
    key = f"{entry}|{off}"
    out = []
    for r in doc["rows"]:
        if r["und"] != und or key not in r["res"]:
            continue
        if min_move and abs(r["move"].get(entry) or 0) < min_move:
            continue
        v = r["res"][key].get(exit_)
        if v is not None:
            out.append((r["day"], float(v)))
    return out


def names(doc: dict, und: str, off: float, take: int) -> Series:
    return [(d, float(r)) for d, r in doc["trades"].get(f"{und}|{off}|{take}", [])]


def streak(doc: dict, und: str, key: str, after: float) -> Series:
    return [(r["day"], float(r["res"][key])) for r in doc["rows"]
            if r["symbol"] == und and r["prev_ret"] <= after and r["res"].get(key) is not None]


def dipweek(doc: dict, leg: str, exit_: str) -> Series:
    return [(r["day"], float(r["res"][exit_])) for r in doc["rows"] if r.get("leg") == leg and r.get("res", {}).get(exit_) is not None]


def pead(doc: dict, gap: float, exit_: str) -> Series:
    return [(r["day"], float(r["res"][exit_])) for r in doc["rows"]
            if r["gap"] >= gap and r.get("held", 0) >= 0 and r["res"].get(exit_) is not None]


def ledgers(docs: dict) -> dict[str, tuple[str, Series | None]]:
    """ledger id -> (where the series comes from, the series). Live and plan ledgers alike."""
    mm, nm, st, dw, pd_ = (docs.get(k) for k in ("minmove", "names", "streak", "dipweek", "pead"))
    out: dict[str, tuple[str, Series | None]] = {}

    def m(sid, und, entry, off, exit_, min_move=0.0, note=""):
        out[sid] = (f"zdte-minmove {und} {entry} {off:.1%} {exit_}{note}", minmove(mm, und, entry, off, exit_, min_move) if mm else None)

    m("spy0dte-500", "SPY", "10:00", 0.003, "take3_trail", 0.005, "，10:00 离开盘 ≥0.5%")
    m("spy0dte-1000", "SPY", "10:00", 0.003, "take5_trail", 0.005, "，10:00 离开盘 ≥0.5%")
    m("spy0dte-near-500", "SPY", "10:00", 0.003, "take3_trail")
    m("spy0dte-near-1000", "SPY", "09:35", 0.003, "take3_trail")
    m("spy0dte-old-plan-500", "SPY", "10:00", 0.006, "take3_trail")
    m("qqq0dte-500", "QQQ", "09:35", 0.006, "take3_trail")
    m("qqq0dte-1000", "QQQ", "09:35", 0.003, "take3_trail")
    m("tsla0dte-500", "TSLA", "10:00", 0.006, "take3_trail")
    m("tsla0dte-1000", "TSLA", "09:35", 0.006, "take3_trail")
    m("tsla0dte-5x-500", "TSLA", "10:00", 0.006, "take5_trail")
    m("tsla0dte-5x-1000", "TSLA", "10:00", 0.003, "take5_trail")
    m("nvda0dte-500", "NVDA", "10:00", 0.006, "take3_trail")
    m("nvda0dte-1000", "NVDA", "10:00", 0.006, "take5_trail")
    for sid, und, off in (("iwm0dte-plan-500", "IWM", 0.003), ("amd0dte-plan-500", "AMD", 0.006), ("amzn0dte-plan-500", "AMZN", 0.006),
                          ("meta0dte-plan-500", "META", 0.006), ("msft0dte-plan-500", "MSFT", 0.006)):
        out[sid] = (f"0dte-names {und} 10:00 {off:.1%} 3 倍全卖（只有个股有到期的日子）", names(nm, und, off, 3) if nm else None)
    out["dia0dte-plan-500"] = ("没有回测：DIA 没算过", None)
    out["spydip-500"] = ("zdte-streak SPY 前一天跌 ≥1%，10:00 买 0.3% 看涨 3 倍", streak(st, "SPY", "C|0.003|3", -0.01) if st else None)
    out["spydip-1000"] = ("zdte-streak SPY 前一天跌 ≥1%，10:00 买 0.6% 看涨 3 倍", streak(st, "SPY", "C|0.006|3", -0.01) if st else None)
    out["dipweek-500"] = ("dip-week 价外 3%，翻倍后回撤 40%，否则第 5 天", dipweek(dw, "otm3", "trail2") if dw else None)
    out["dipweek-1000"] = ("dip-week 平值，翻倍后回撤 40%，否则第 5 天", dipweek(dw, "atm", "trail2") if dw else None)
    out["pead-500"] = ("pead 高开 ≥10% 且守住，翻倍后回撤 40%，否则第 3 天", pead(pd_, 0.10, "trail2") if pd_ else None)
    out["pead-1000"] = ("pead 高开 ≥5% 且守住，翻倍后回撤 40%，否则第 3 天", pead(pd_, 0.05, "trail2") if pd_ else None)
    return out


# ---- the checks ----
def stop_line(xs: Series, window: int = WINDOW, pct: float = PCT) -> dict | None:
    """Sums of every `window` consecutive trades (date order): the `pct` quantile is the stop line, the median the norm."""
    v = [r for _, r in sorted(xs)]
    if len(v) < window:
        return None
    sums = sorted(sum(v[i:i + window]) for i in range(len(v) - window + 1))
    return {"windows": len(sums), "p20": sums[int(pct * (len(sums) - 1))], "p50": sums[len(sums) // 2],
            "worst": sums[0], "best": sums[-1]}


def last_month(xs: Series, end: str, days: int = 30) -> tuple[float, int] | None:
    cut = (date.fromisoformat(end[:10]) - timedelta(days=days)).isoformat()
    v = [r for d, r in xs if d >= cut]
    return (statistics.mean(v), len(v)) if v else None


def monthly(xs: Series, months: int = 6) -> list[tuple[str, float, int]]:
    by: dict[str, list[float]] = {}
    for d, r in xs:
        by.setdefault(d[:7], []).append(r)
    return [(m, statistics.mean(v), len(v)) for m, v in sorted(by.items())[-months:]]


def live_stats(book: dict, sid: str, window: int = WINDOW) -> dict | None:
    """One live trade = one entry: its take half and its trail half (ticket id + "r") are summed back together,
    so the return per $1 is the backtest's take3_trail, not two tickets of +2.0 and -0.9."""
    s = (book.get("sleeves") or {}).get(sid) or {}
    by: dict[str, dict] = {}
    for t in s.get("closed", []):
        if t.get("paid") is None or t.get("proceeds") is None:
            continue
        e = by.setdefault(str(t.get("tid", "")).rstrip("r"), {"paid": 0.0, "got": 0.0, "closed": ""})
        e["paid"] += float(t["paid"])
        e["got"] += float(t["proceeds"])
        e["closed"] = max(e["closed"], str(t.get("closed") or ""))
    trades = sorted((e for e in by.values() if e["paid"] > 0), key=lambda e: e["closed"])
    if not trades:
        return None
    rets = [e["got"] / e["paid"] - 1 for e in trades]
    last = rets[-window:]
    return {"n": len(rets), "mean": statistics.mean(rets), "last_n": len(last), "last_sum": sum(last),
            "pnl": round(sum(e["got"] - e["paid"] for e in trades), 2)}


def verdict(s: dict | None, lm: tuple[float, int] | None, line: dict | None, live: dict | None) -> str:
    if not s:
        return "没有回测"
    trim, recent = s.get("trim"), (s.get("recent") or (None, 0))[0]
    if trim is None or recent is None:
        return "样本太少"
    if trim < 0 and recent < 0:
        out = "回测就不行"
    elif trim > 0 and recent > 0:
        out = "回测可以"
    else:
        out = "回测分歧"
    if lm and lm[1] >= 3:
        out += f"；最近一个月 {lm[0]:+.0%}（{lm[1]} 笔）"
    if live and line and live["n"] >= WINDOW:
        out += "；实盘低于停止线" if live["last_sum"] < line["p20"] else "；实盘在停止线上"
    elif live:
        out += f"；实盘 {live['n']}/{WINDOW} 笔"
    return out


def pct(x) -> str:
    return "—" if x is None else f"{x:+.0%}"


def build(docs: dict, book: dict, today: str) -> tuple[list[dict], str]:
    rows = []
    for sid, (src, xs) in ledgers(docs).items():
        s = robust.summary(xs, end=max(d for d, _ in xs)) if xs else None
        end = max(d for d, _ in xs) if xs else None
        lm = last_month(xs, end) if xs else None
        line = stop_line(xs) if xs else None
        live = live_stats(book, sid)
        rows.append({"id": sid, "source": src, "summary": s, "end": end, "last_month": lm, "stop": line, "live": live,
                     "verdict": verdict(s, lm, line, live), "monthly": monthly(xs) if xs else []})
    md = [f"# 判决表：每个账本对上已有的回测 {today}", "",
          "用户 10-23：「想加快进度，因为环境是实时变化的」。账本在等的答案，大多研究文件夹里已经有了（10-07 算过 IWM / AMD / AMZN / "
          "META / MSFT 的末日单，10-13 算过 09:35 进场）。这张表把每个账本的参数对到同一口径的逐笔回测：hero/robust.py 的检查，"
          "加「最近一个月」（环境变了先在这里看出来）和回测自己的 **停止线**——历史上任意连续 10 笔之和的 20 分位；实盘做满 10 笔"
          "低于它，就是比八成的历史还差，不用等 100 笔。回测的成本假设各不相同（末日单买多付 10%、卖少拿 10%），见各自文件。只研究，不改交易。", "",
          "| 账本 | 回测 | 笔数 | 平均每 $1 | 去掉最好 3 笔 | 中位数 | 最近 3 个月 | 最近 1 个月 | 10 笔停止线 / 中位 | 实盘：笔数 · 平均 · 最近 10 笔合计 | 判决 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s, lm, line, live = r["summary"], r["last_month"], r["stop"], r["live"]
        rec = f"{pct(s['recent'][0])}（{s['recent'][1]}）" if s and s.get("recent") else "—"
        lmx = f"{pct(lm[0])}（{lm[1]}）" if lm else "—"
        st = f"{line['p20']:+.1f} / {line['p50']:+.1f}" if line else "—"
        lv = f"{live['n']} · {pct(live['mean'])} · {live['last_sum']:+.1f}（${live['pnl']:+,.0f}）" if live else "—"
        md.append(f"| {r['id']} | {r['source']}{'（数据到 ' + r['end'][5:] + '）' if r['end'] else ''} | {s['n'] if s else 0} | {pct(s['mean']) if s else '—'} | {pct(s['trim']) if s else '—'} | "
                  f"{pct(s['median']) if s else '—'} | {rec} | {lmx} | {st} | {lv} | {r['verdict']} |")
    md += ["", "## 环境变得多快：月度平均（每 $1）", "",
           "同一条规则，按月看。一个月 20 笔的平均能从 -50% 跳到 +50%，所以「最近 3 个月」也只是三枚硬币。", ""]
    shown = [r for r in rows if r["monthly"] and r["summary"] and r["summary"]["n"] >= 150]
    if shown:
        months = sorted({m for r in shown for m, _, _ in r["monthly"]})[-6:]
        md.append("| 账本 | " + " | ".join(months) + " |")
        md.append("|---|" + "---|" * len(months))
        for r in shown:
            by = {m: (v, n) for m, v, n in r["monthly"]}
            md.append(f"| {r['id']} | " + " | ".join(f"{pct(by[m][0])}（{by[m][1]}）" if m in by else "—" for m in months) + " |")
    md += ["", "## 没在表里的", "",
           "- 狙击、跟 c 方向、财报判断：判断类，没有回测，只能靠实盘和演算攒（狙击停止线 10 次已在规则里）。",
           "- 突破大跳空：research/2026-10-15-sniper-factors3.md 只有汇总（57 次 +53%，去掉最好 3 笔 +23%，中位数 -10%），一年约 20 次，按回测判。",
           "- 周权三条、净值、低开反弹、急跌抄底、财报正股、接力、TQQQ、隔夜、高开回落、全仓赌一把、VIX：各有自己的研究文件（docs/sleeves-plan.md 的「依据」列）。",
           "", "## 怎么用", "",
           "- 「回测就不行」的实盘账本：不等 30 笔，做满 10 笔低于停止线就停（只演算）；演算账本直接撤。",
           "- 「回测可以」的：实盘做满 10 笔看是不是在停止线上，在就继续，不在就查成交差距而不是规则。",
           "- 每周把这张表重算一次（本脚本不用 API），最近一个月那列变号就是环境变了的第一信号。"]
    return rows, "\n".join(md) + "\n"


def main(argv: list[str]) -> int:
    today = argv[1] if len(argv) > 1 else date.today().isoformat()
    docs = {}
    for k, f in (("minmove", "2026-10-16-zdte-minmove.json"), ("names", "2026-10-07-0dte-names.json"),
                 ("streak", "2026-10-08-zdte-streak.json"), ("dipweek", "2026-10-16-dip-week.json"), ("pead", "2026-10-13-pead.json")):
        try:
            docs[k] = load(f)
        except FileNotFoundError:
            pass
    try:
        book = json.loads((ROOT / "journal-s500" / "sleeves.json").read_text())
    except FileNotFoundError:
        book = {}
    rows, md = build(docs, book, today)
    out = ROOT / "research" / f"{today}-verdict"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps({"generated": today, "rows": rows}, ensure_ascii=False, indent=1, default=str))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
