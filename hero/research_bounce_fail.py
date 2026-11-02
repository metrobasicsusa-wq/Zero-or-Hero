"""Twins of 2026-11-02: a bounce that fails by 10:30 after a down week (owner, 11-02: 「今年肯定发生过几次这样的」).

11-02: SMH had fallen for a week; Monday gapped up 1.3% on oil, not on chip news; by 10:30 it was back under its open;
the afternoon sold AI into the election eve and SPY's 756 put went 3x. This study lists every such day since 2019 on
SMH, QQQ and SPY and asks what the afternoon did, then replays our same-day option rules on them from 2024-02 (where
Alpaca's option bars begin). A twin is: the name's own 5-session return into the day at or under WEEK (SMH -3%, QQQ
and SPY -2%); the open at least GAP (0.5%) above the prior close; the 10:30 minute close at or under the open. Three
controls: any gap-up day that is back under its open at 10:30 (no down week), a down-week gap-up that still holds its
open at 10:30, and every other session. Each twin also carries the VIX-proxy divergence at 11:30 (VIXY above its
10:00–11:30 low while the name sits within NEAR_HIGH of its day high: research_vix_divergence).
Stock level since 2019: the 10:30 -> 15:30 move and the close against the open, how often negative, mean, median.
Option level since 2024-02: the 0DTE put OFF (0.3%) under the 10:30 price, 3x take else 15:30 (our live exit) and
held to 15:30, per $1 with hero/robust.py and the sleeve game; also what the live 09:35 and 10:00 rules (a call above
the open, else a put) made on those days. Record only; nothing here changes a rule.
Run: python -m hero.research_bounce_fail
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_ideas import save
from hero.research_zdte_stop import play_all
from hero.research_zdte_timing import OUT, game

NAMES = ("SMH", "QQQ", "SPY")
WEEK = {"SMH": -0.03, "QQQ": -0.02, "SPY": -0.02}
GAP = 0.005
CHECK = "10:30"
OFF = 0.003
STOCK_START = "2019-01-02"
OPT_START = "2024-02-01"
VIX_PROXY = "VIXY"
VIX_CHECK = "11:30"
VIX_DIV = 0.03
NEAR_HIGH = 0.003
GROUPS = ("twin", "fail_any", "held", "other")
ZH = {"twin": "孪生日（上周跌 + 高开 + 10:30 跌回开盘价下）", "fail_any": "只看高开回落（不管上周）",
      "held": "上周跌 + 高开、10:30 还在开盘价上", "other": "其余交易日"}


# ---------------------------------------------------------------- features of one session

def day_features(m: dict[str, dict]) -> dict | None:
    """Open, the 10:30 / 11:30 / 15:30 closes, the close, and the high up to 11:30, from one day's minute bars."""
    if "09:30" not in m:
        return None
    b1030, b1130, b1530 = at_or_before(m, CHECK), at_or_before(m, VIX_CHECK), at_or_before(m, OUT)
    last = max(k for k in m if k <= "15:59")
    if not (b1030 and b1130 and b1530):
        return None
    hi = max(float(v["h"]) for k, v in m.items() if k <= VIX_CHECK)
    return {"open": float(m["09:30"]["o"]), "c1030": float(b1030["c"]), "c1130": float(b1130["c"]),
            "c1530": float(b1530["c"]), "close": float(m[last]["c"]), "hi1130": hi}


def vix_div(mv: dict[str, dict] | None, at: str = VIX_CHECK, lo_from: str = "10:00") -> float | None:
    """The VIX proxy's close at `at` over its lowest close between lo_from and `at`, minus one."""
    if not mv:
        return None
    b = at_or_before(mv, at)
    lows = [float(v["c"]) for k, v in mv.items() if lo_from <= k <= at]
    if not b or not lows:
        return None
    return float(b["c"]) / min(lows) - 1


def features(days: dict[str, dict], vix: dict[str, dict], und: str) -> list[dict]:
    """One row per session with the week into it, the gap, the 10:30 test, the afternoon and the VIX divergence."""
    dates = sorted(days)
    feats = {d: day_features(days[d]) for d in dates}
    closes = {d: f["close"] for d, f in feats.items() if f}
    rows = []
    for i, d in enumerate(dates):
        f = feats[d]
        if not f or i < 6:
            continue
        prev = [x for x in dates[:i] if x in closes]
        if len(prev) < 6:
            continue
        p1, p6 = closes[prev[-1]], closes[prev[-6]]
        gap = f["open"] / p1 - 1
        week = p1 / p6 - 1
        fail = f["c1030"] <= f["open"]
        div = vix_div(vix.get(d))
        rows.append({"und": und, "day": d, "week": round(week, 4), "gap": round(gap, 4), "fail": fail,
                     "c1030_vs_open": round(f["c1030"] / f["open"] - 1, 4),
                     "pm": round(f["c1530"] / f["c1030"] - 1, 4), "pm_close": round(f["close"] / f["c1030"] - 1, 4),
                     "close_vs_open": round(f["close"] / f["open"] - 1, 4),
                     "vix_div": None if div is None else round(div, 4),
                     "near_high": f["c1130"] >= f["hi1130"] * (1 - NEAR_HIGH),
                     "group": classify(week, gap, fail, und)})
    return rows


def classify(week: float, gap: float, fail: bool, und: str) -> str:
    down_week, gap_up = week <= WEEK[und], gap >= GAP
    if down_week and gap_up and fail:
        return "twin"
    if gap_up and fail:
        return "fail_any"
    if down_week and gap_up:
        return "held"
    return "other"


# ---------------------------------------------------------------- the option replay on signal days

def option_day(client, und: str, day: str, m: dict[str, dict], pace=None) -> dict | None:
    """Our same-day option rules on one session: the 10:30 put OFF under the price (take3 / hold), and the live
    09:35 and 10:00 rules (a call above the open else a put, OFF out, take3)."""
    o = float(m["09:30"]["o"])
    plan = {}
    for entry, forced in (("09:35", None), ("10:00", None), (CHECK, "P")):
        b = at_or_before(m, entry)
        if b:
            spot = float(b["c"])
            plan[entry] = (forced or ("C" if spot >= o else "P"), spot)
    if not plan:
        return None
    lo = min(s for _, s in plan.values()) * 0.97
    hi = max(s for _, s in plan.values()) * 1.03
    cs = []
    for status in ("inactive", "active"):
        if pace:
            pace.wait()
        try:
            cs = client.option_contracts(und, status=status, expiration_date_gte=day,
                                         expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                         strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
        except Exception:
            cs = []
        if cs:
            break
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    near = [c for c in cs if c["expiration_date"] == exp]
    syms = {e: pick(near, k, spot, OFF) for e, (k, spot) in plan.items()}
    want = sorted({s for s in syms.values() if s})
    if not want:
        return None
    if pace:
        pace.wait()
    try:
        bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
    except Exception:
        return None
    time.sleep(0.05)
    mins = {s: et_minutes(b) for s, b in bars.items()}
    out = {"dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days}
    for e, s in syms.items():
        if s and s in mins:
            got = play_all(mins[s], m, plan[e][0], o, entry=e)
            if got:
                out[e] = {"kind": plan[e][0], "take3": got["res"]["take3"], "hold": got["res"]["hold"]}
    return out if len(out) > 1 else None


def gather(client, pace=None, names: tuple[str, ...] = NAMES, start: str = STOCK_START, end: str | None = None,
           log=lambda *a: None) -> list[dict]:
    end = end or (date.today() - timedelta(days=1)).isoformat()
    vix = minutes_by_day(client, VIX_PROXY, start, end)
    log(VIX_PROXY, len(vix), "days")
    rows = []
    for und in names:
        days = minutes_by_day(client, und, start, end)
        log(und, len(days), "days")
        fs = features(days, vix, und)
        for r in fs:
            if r["day"] >= OPT_START and r["group"] in ("twin", "fail_any", "held"):
                r["options"] = option_day(client, und, r["day"], days[r["day"]], pace)
        rows.extend(fs)
        log(und, "twins", sum(r["group"] == "twin" for r in fs))
    return rows


# ---------------------------------------------------------------- the study

def stock_cell(rs: list[dict]) -> dict | None:
    if not rs:
        return None
    pm = [r["pm"] for r in rs]
    return {"n": len(rs), "mean": round(statistics.mean(pm), 4), "median": round(statistics.median(pm), 4),
            "neg": round(sum(x < 0 for x in pm) / len(pm), 3),
            "close_under_open": round(sum(r["close_vs_open"] < 0 for r in rs) / len(rs), 3),
            "div_share": round(sum(1 for r in rs if r["vix_div"] is not None and r["vix_div"] >= VIX_DIV) / len(rs), 3)}


def option_cell(rs: list[dict], entry: str, key: str) -> dict | None:
    xs = [(r["day"], r["options"][entry][key]) for r in rs if r.get("options") and entry in r["options"]]
    if not xs:
        return None
    v = [x for _, x in xs]
    g2 = game(xs, 2) if len(xs) >= 2 else {"heroes": 0, "zeros": 0}
    return {"n": len(v), "mean": round(statistics.mean(v), 3), "median": round(statistics.median(v), 3),
            "win": round(sum(x > 0 for x in v) / len(v), 3), "x2": round(sum(x >= 1 for x in v) / len(v), 3),
            "h2": g2["heroes"], "z2": g2["zeros"]}


def run(rows: list[dict], today: date | None = None) -> dict:
    by = {}
    for und in NAMES + ("全部",):
        rs = [r for r in rows if und == "全部" or r["und"] == und]
        by[und] = {g: stock_cell([r for r in rs if r["group"] == g]) for g in GROUPS}
        by[und]["twin_div"] = stock_cell([r for r in rs if r["group"] == "twin" and r["vix_div"] is not None and r["vix_div"] >= VIX_DIV])
        by[und]["twin_nodiv"] = stock_cell([r for r in rs if r["group"] == "twin" and r["vix_div"] is not None and r["vix_div"] < VIX_DIV])
    opt = {}
    for g in ("twin", "fail_any", "held"):
        rs = [r for r in rows if r["group"] == g and r.get("options")]
        opt[g] = {"put1030_take3": option_cell(rs, CHECK, "take3"), "put1030_hold": option_cell(rs, CHECK, "hold"),
                  "rule0935": option_cell(rs, "09:35", "take3"), "rule1000": option_cell(rs, "10:00", "take3")}
    twins = sorted([r for r in rows if r["group"] == "twin"], key=lambda r: r["day"], reverse=True)
    years = {}
    for r in twins:
        y = years.setdefault(r["day"][:4], [0, 0])
        y[0] += 1
        y[1] += r["pm"] < 0
    rob = {}
    for g in ("twin", "fail_any"):
        rs = [r for r in rows if r["group"] == g and r.get("options") and CHECK in r["options"]]
        rob[f"{ZH[g]} · 10:30 看跌 3 倍止盈"] = robust.summary([(r["day"], r["options"][CHECK]["take3"]) for r in rs])
        rob[f"{ZH[g]} · 10:30 看跌拿到 15:30"] = robust.summary([(r["day"], r["options"][CHECK]["hold"]) for r in rs])
    return {"generated": (today or date.today()).isoformat(), "n_rows": len(rows), "stock": by, "options": opt,
            "twins": twins, "twin_years": years, "robust": rob,
            "days": [min(r["day"] for r in rows), max(r["day"] for r in rows)] if rows else None}


def _pct(x, nd=1):
    return "—" if x is None else f"{x:+.{nd}%}"


def markdown(rep: dict) -> str:
    md = [f"# 11-02 的孪生日：上周跌、周一高开、10:30 守不住，下午怎么走 {rep['generated']}", "",
          f"{'、'.join(NAMES)} 每个交易日（{rep['days'][0] if rep.get('days') else '—'} 到 {rep['days'][1] if rep.get('days') else '—'}，共 {rep['n_rows']} 个标的日）。"
          f"孪生日 = 自己前 5 个交易日跌 ≥{-WEEK['SMH']:.0%}（SMH）/ {-WEEK['SPY']:.0%}（SPY、QQQ），当天开盘比昨收高 ≥{GAP:.1%}，10:30 的分钟收盘价不高于开盘价。"
          f"VIX 背离 = 11:30 时 {VIX_PROXY} 比 10:00–11:30 的最低点高 ≥{VIX_DIV:.0%}。下午 = 10:30 到 15:30。只研究，不改规则。", "",
          "## 孪生日名单（新的在前）", "",
          "| 日期 | 标的 | 上周 | 高开 | 10:30 对开盘 | VIX 背离 11:30 | 下午 10:30→15:30 | 收盘对开盘 | 09:35 规则 | 10:00 规则 | 10:30 看跌（3 倍 / 拿到 15:30） |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["twins"]:
        o = r.get("options") or {}
        f = lambda e, k="take3": _pct(o[e][k], 0) + ("看涨" if o[e]["kind"] == "C" else "看跌") if e in o else "—"
        put = f"{_pct(o[CHECK]['take3'], 0)} / {_pct(o[CHECK]['hold'], 0)}" if CHECK in o else "—"
        md.append(f"| {r['day']} | {r['und']} | {_pct(r['week'])} | {_pct(r['gap'])} | {_pct(r['c1030_vs_open'])} | "
                  f"{_pct(r['vix_div'])} | {_pct(r['pm'])} | {_pct(r['close_vs_open'])} | {f('09:35')} | {f('10:00')} | {put} |")
    md += ["", "各年份孪生日：" + "、".join(f"{y} {n} 天（下午跌 {k}）" for y, (n, k) in sorted(rep["twin_years"].items())), "",
           "## 股票层面：下午怎么走", "",
           "每格：天数 / 下午平均 / 中位数 / 下午收跌的比例 / 收盘低于开盘的比例 / 有 VIX 背离的比例。", "",
           "| 标的 | " + " | ".join(ZH[g] for g in GROUPS) + " | 孪生 + VIX 背离 | 孪生、无背离 |", "|---|" + "---|" * (len(GROUPS) + 2)]
    for und in NAMES + ("全部",):
        cells = []
        for g in GROUPS + ("twin_div", "twin_nodiv"):
            c = rep["stock"][und].get(g)
            cells.append("—" if not c else f"{c['n']} / {_pct(c['mean'], 2)} / {_pct(c['median'], 2)} / {c['neg']:.0%} / {c['close_under_open']:.0%} / {c['div_share']:.0%}")
        md.append(f"| {und} | " + " | ".join(cells) + " |")
    md += ["", f"## 期权层面（{OPT_START} 起）：那天按我们的规则会怎样", "",
           "每格：张数 / 平均每 $1 / 中位数 / 赚钱比例 / 到 2 倍的比例 / 账本玩法到 2 倍 成功·归零。", "",
           "| 情况 | 10:30 看跌、3 倍止盈 | 10:30 看跌、拿到 15:30 | 09:35 规则（现行） | 10:00 规则（现行） |", "|---|---|---|---|---|"]
    for g in ("twin", "fail_any", "held"):
        cells = []
        for k in ("put1030_take3", "put1030_hold", "rule0935", "rule1000"):
            c = rep["options"][g].get(k)
            cells.append("—" if not c else f"{c['n']} / {_pct(c['mean'], 0)} / {_pct(c['median'], 0)} / {c['win']:.0%} / {c['x2']:.0%} / {c['h2']}·{c['z2']}")
        md.append(f"| {ZH[g]} | " + " | ".join(cells) + " |")
    md += ["", robust.table(rep["robust"], "稳健性检查（每张票一笔，每 $1 的收益）"),
           "## 读法", "",
           "- 先看股票层面「孪生日」和「只看高开回落」两列的下午收跌比例差多少：差得多，上周的方向才是必要条件；差不多，那只是「高开回落日下午继续跌」的普通规律。",
           "- 再看「孪生 + VIX 背离」对「孪生、无背离」：背离是不是把下午的跌筛出来了。",
           "- 期权层面的张数少、2024-02 起才有；每 $1 的平均要和「稳健性检查」一起看，去掉最好 3 笔还赚才算数。",
           "- 11-02 自己在不在名单里、它那行的数字和平均差多少，是这期最该看的一行。", ""]
    return "\n".join(md)


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    rows = gather(client, pace=Pace(client), log=lambda *a: print(*a, file=sys.stderr))
    rep = run(rows)
    rep["rows"] = [{k: v for k, v in r.items()} for r in rows if r["group"] != "other"]
    save("bounce-fail", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
