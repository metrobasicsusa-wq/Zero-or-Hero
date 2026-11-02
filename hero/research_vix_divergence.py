"""VIX divergence at 11:30: the index near its high while the VIX proxy climbs (owner, 11-02: 「怎么样才能发现这个机会」).

11-02 at 11:00–12:00 VIXY was back above 19 from an 18 low while SPY still sat near its morning high: consumer and
financials were holding the index up, chips were being sold, somebody was buying protection. The afternoon then
sold off. This study asks, every session since 2019: when VIXY at 11:30 is at least DIV above its 10:00–11:30 low and
SPY at 11:30 is within NEAR_HIGH of its high so far, what does SPY do from 11:30 to 15:30? Controls: divergence
without the index near its high, near its high without the divergence, neither. Thresholds 2% / 3% / 5%. From
2024-02 the signal days also replay the SPY 0DTE put OFF (0.3%) under the 11:30 price, 3x take else 15:30, and held
to 15:30, per $1 with hero/robust.py and the sleeve game. Record only; nothing here changes a rule.
Run: python -m hero.research_vix_divergence
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, timedelta

from hero import robust
from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_bounce_fail import NEAR_HIGH, OFF, OPT_START, STOCK_START, VIX_PROXY, day_features, vix_div
from hero.research_ideas import save
from hero.research_zdte_stop import play_all
from hero.research_zdte_timing import OUT, game

UND = "SPY"
CHECK = "11:30"
THRESHOLDS = (0.02, 0.03, 0.05)
GROUPS = ("signal", "div_only", "high_only", "neither")
ZH = {"signal": "背离 + 指数在高点附近", "div_only": "只有背离", "high_only": "只在高点附近", "neither": "都没有"}


def features(days: dict[str, dict], vix: dict[str, dict]) -> list[dict]:
    rows = []
    for d in sorted(days):
        f = day_features(days[d])
        if not f:
            continue
        b = at_or_before(days[d], CHECK)
        if not b:
            continue
        c1130 = float(b["c"])
        rows.append({"day": d, "vix_div": vix_div(vix.get(d), CHECK), "near_high": c1130 >= f["hi1130"] * (1 - NEAR_HIGH),
                     "c1130": c1130, "pm": round(f["c1530"] / c1130 - 1, 4), "pm_close": round(f["close"] / c1130 - 1, 4),
                     "open_to_1130": round(c1130 / f["open"] - 1, 4)})
    return rows


def classify(r: dict, thr: float) -> str:
    div = r["vix_div"] is not None and r["vix_div"] >= thr
    if div and r["near_high"]:
        return "signal"
    if div:
        return "div_only"
    if r["near_high"]:
        return "high_only"
    return "neither"


def option_day(client, day: str, m: dict[str, dict], pace=None) -> dict | None:
    """The SPY 0DTE put OFF under the 11:30 price: 3x take else 15:30, and held to 15:30."""
    b = at_or_before(m, CHECK)
    if not b:
        return None
    spot = float(b["c"])
    cs = []
    for status in ("inactive", "active"):
        if pace:
            pace.wait()
        try:
            cs = client.option_contracts(UND, status=status, expiration_date_gte=day,
                                         expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                         strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
        except Exception:
            cs = []
        if cs:
            break
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    sym = pick([c for c in cs if c["expiration_date"] == exp], "P", spot, OFF)
    if not sym:
        return None
    if pace:
        pace.wait()
    try:
        bars = client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
    except Exception:
        return None
    time.sleep(0.05)
    if sym not in bars:
        return None
    got = play_all(et_minutes(bars[sym]), m, "P", float(m["09:30"]["o"]), entry=CHECK)
    return None if not got else {"take3": got["res"]["take3"], "hold": got["res"]["hold"],
                                 "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days}


def gather(client, pace=None, start: str = STOCK_START, end: str | None = None, log=lambda *a: None) -> list[dict]:
    end = end or (date.today() - timedelta(days=1)).isoformat()
    vix = minutes_by_day(client, VIX_PROXY, start, end)
    days = minutes_by_day(client, UND, start, end)
    log(UND, len(days), VIX_PROXY, len(vix))
    rows = features(days, vix)
    for r in rows:
        if r["day"] >= OPT_START and classify(r, min(THRESHOLDS)) == "signal":
            r["options"] = option_day(client, r["day"], days[r["day"]], pace)
    log("signal days with options", sum(1 for r in rows if r.get("options")))
    return rows


def cell(rs: list[dict]) -> dict | None:
    if not rs:
        return None
    pm = [r["pm"] for r in rs]
    return {"n": len(rs), "mean": round(statistics.mean(pm), 4), "median": round(statistics.median(pm), 4),
            "neg": round(sum(x < 0 for x in pm) / len(pm), 3), "big_neg": round(sum(x <= -0.005 for x in pm) / len(pm), 3)}


def option_cell(rs: list[dict], key: str) -> dict | None:
    xs = [(r["day"], r["options"][key]) for r in rs if r.get("options")]
    if not xs:
        return None
    v = [x for _, x in xs]
    g2 = game(xs, 2) if len(xs) >= 2 else {"heroes": 0, "zeros": 0}
    return {"n": len(v), "mean": round(statistics.mean(v), 3), "median": round(statistics.median(v), 3),
            "win": round(sum(x > 0 for x in v) / len(v), 3), "x2": round(sum(x >= 1 for x in v) / len(v), 3),
            "h2": g2["heroes"], "z2": g2["zeros"]}


def run(rows: list[dict], today: date | None = None) -> dict:
    stock = {thr: {g: cell([r for r in rows if classify(r, thr) == g]) for g in GROUPS} for thr in THRESHOLDS}
    opt = {thr: {"take3": option_cell([r for r in rows if classify(r, thr) == "signal"], "take3"),
                 "hold": option_cell([r for r in rows if classify(r, thr) == "signal"], "hold")} for thr in THRESHOLDS}
    years = {}
    for r in rows:
        if classify(r, 0.03) == "signal":
            y = years.setdefault(r["day"][:4], [0, 0])
            y[0] += 1
            y[1] += r["pm"] < 0
    recent = sorted([r for r in rows if classify(r, 0.03) == "signal"], key=lambda r: r["day"], reverse=True)[:25]
    rob = {}
    for thr in THRESHOLDS:
        rs = [r for r in rows if classify(r, thr) == "signal" and r.get("options")]
        rob[f"背离 ≥{thr:.0%} · 11:30 看跌 3 倍止盈"] = robust.summary([(r["day"], r["options"]["take3"]) for r in rs])
    return {"generated": (today or date.today()).isoformat(), "n_rows": len(rows), "stock": stock, "options": opt,
            "signal_years": years, "recent": recent, "robust": rob,
            "days": [min(r["day"] for r in rows), max(r["day"] for r in rows)] if rows else None}


def _pct(x, nd=2):
    return "—" if x is None else f"{x:+.{nd}%}"


def markdown(rep: dict) -> str:
    md = [f"# VIX 背离：11:30 指数还在高点、VIX 已经抬头，下午怎么走 {rep['generated']}", "",
          f"{UND} 每个交易日（{rep['days'][0] if rep.get('days') else '—'} 到 {rep['days'][1] if rep.get('days') else '—'}，{rep['n_rows']} 天），"
          f"{VIX_PROXY} 当 VIX 的代理。背离 = 11:30 时 {VIX_PROXY} 比 10:00–11:30 的最低点高出门槛（2% / 3% / 5%）；在高点附近 = 11:30 的 SPY 离当天到那时的最高价不到 {NEAR_HIGH:.1%}。"
          "下午 = 11:30 到 15:30。只研究，不改规则。", "",
          "## 股票层面", "", "每格：天数 / 下午平均 / 中位数 / 下午收跌的比例 / 下午跌 0.5% 以上的比例。", "",
          "| 门槛 | " + " | ".join(ZH[g] for g in GROUPS) + " |", "|---|" + "---|" * len(GROUPS)]
    for thr in THRESHOLDS:
        cells = []
        for g in GROUPS:
            c = rep["stock"][thr].get(g)
            cells.append("—" if not c else f"{c['n']} / {_pct(c['mean'])} / {_pct(c['median'])} / {c['neg']:.0%} / {c['big_neg']:.0%}")
        md.append(f"| ≥{thr:.0%} | " + " | ".join(cells) + " |")
    md += ["", "各年份（门槛 3%）：" + "、".join(f"{y} {n} 天（下午跌 {k}）" for y, (n, k) in sorted(rep["signal_years"].items())), "",
           f"## 期权层面（{OPT_START} 起）：SPY 当天到期、离 11:30 价 {OFF:.1%} 的看跌", "",
           "每格：张数 / 平均每 $1 / 中位数 / 赚钱比例 / 到 2 倍的比例 / 账本玩法到 2 倍 成功·归零。", "",
           "| 门槛 | 3 倍止盈、15:30 平 | 拿到 15:30 |", "|---|---|---|"]
    for thr in THRESHOLDS:
        cells = []
        for k in ("take3", "hold"):
            c = rep["options"][thr].get(k)
            cells.append("—" if not c else f"{c['n']} / {_pct(c['mean'], 0)} / {_pct(c['median'], 0)} / {c['win']:.0%} / {c['x2']:.0%} / {c['h2']}·{c['z2']}")
        md.append(f"| ≥{thr:.0%} | " + " | ".join(cells) + " |")
    md += ["", "## 最近 25 个信号日（门槛 3%）", "", "| 日期 | 背离 | 开盘到 11:30 | 下午 11:30→15:30 | 到收盘 | 看跌 3 倍 / 拿到 15:30 |", "|---|---|---|---|---|---|"]
    for r in rep["recent"]:
        o = r.get("options") or {}
        md.append(f"| {r['day']} | {_pct(r['vix_div'])} | {_pct(r['open_to_1130'])} | {_pct(r['pm'])} | {_pct(r['pm_close'])} | "
                  f"{(_pct(o['take3'], 0) + ' / ' + _pct(o['hold'], 0)) if o else '—'} |")
    md += ["", robust.table(rep["robust"], "稳健性检查（每张票一笔，每 $1 的收益）"),
           "## 读法", "",
           "- 「背离 + 高点」那列的下午收跌比例要明显高于「都没有」那列才算信号；「只有背离」那列告诉你指数位置是不是必要条件。",
           f"- {VIX_PROXY} 是期货 ETF，比 VIX 本身钝、还带滚动损耗；门槛 3% 在它身上大约等于 VIX 5%。",
           "- 期权层面只有 2024-02 起的样本，看稳健性表的「去掉最好 3 笔」和「多付成本后」。", ""]
    return "\n".join(md)


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    rows = gather(client, pace=Pace(client), log=lambda *a: print(*a, file=sys.stderr))
    rep = run(rows)
    rep["rows"] = [r for r in rows if classify(r, min(THRESHOLDS)) != "neither"]
    save("vix-divergence", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
