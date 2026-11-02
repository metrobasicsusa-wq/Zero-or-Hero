"""Which 11:30 signals say anything about SPY's afternoon? (owner, 11-02: 「深度研究下信号这个方向」)

The twins study (research_bounce_fail) found the 11-02 shape alone has no afternoon edge and the VIX-divergence study
found the opposite sign; so instead of guessing one shape, this lays every signal computable by 11:30 on the table
and lets the data rank them, with the ranking done on 2019–2024 and the score read on 2025–2026 only.
Target: SPY 11:30 -> 15:30. Signals (SPY minute bars plus VIXY, RSP, XLK, SMH, XLY, XLF, TLT, GLD, QQQ):
  position: the 5-session return into the day, close against the 20-day average, yesterday's afternoon;
  open: the gap, first-hour volume against its 10-day average;
  holding: 10:30 and 11:30 against the open, when the morning high came;
  fear: VIXY at 11:30 against its 10:00-11:30 low and against its prior close;
  breadth / rotation: RSP against SPY since the open, (XLY + XLF) against (XLK + SMH) since the open;
  haven: TLT and GLD since the open;
  calendar: weekday, FOMC decision day and its eve, election eve, monthly opex.
Method: each signal is cut into three buckets (flags into two) and, on the training years, the bucket with the
weakest afternoon is called its bearish bucket and the strongest its bullish bucket; a day's vote is the count of
bearish minus bullish buckets it sits in. Everything is then read on the validation years: afternoon mean and the
share of down afternoons by bucket and by vote, the best pairs, and from 2024-02 the SPY 0DTE put (vote <= -3) and
call (vote >= +3) 0.3% out at 11:30, 3x take else 15:30, with hero/robust.py. Twenty signals always yield one that
looks sharp in-sample, so only the validation numbers count. Record only; nothing here changes a rule.
Run: python -m hero.research_signals
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from hero import robust
from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_bounce_fail import OFF, OPT_START, STOCK_START, VIX_PROXY, vix_div
from hero.research_ideas import save
from hero.research_zdte_stop import play_all
from hero.research_zdte_timing import OUT, game

UND = "SPY"
OTHERS = (VIX_PROXY, "RSP", "XLK", "SMH", "XLY", "XLF", "TLT", "GLD", "QQQ")
CHECK = "11:30"
TRAIN_UNTIL = "2024-12-31"
FOMC = ["2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
        "2020-01-29", "2020-03-03", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
        "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
        "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
        "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
ELECTIONS = ["2020-11-03", "2022-11-08", "2024-11-05", "2026-11-03"]
SIGNALS = {   # name -> (Chinese label, kind) ; kind "num" = terciles, "flag" = two buckets
    "week5": ("前 5 天涨跌", "num"), "trend20": ("昨收对 20 日均线", "num"), "prev_pm": ("昨天下午", "num"),
    "gap": ("开盘跳空", "num"), "vol1h": ("第一小时成交量对 10 天", "num"),
    "hold1030": ("10:30 对开盘", "num"), "hold1130": ("11:30 对开盘", "num"), "high_early": ("上午高点在 10:00 前", "flag"),
    "vix_div": ("VIXY 11:30 对上午低点", "num"), "vix_day": ("VIXY 11:30 对昨收", "num"),
    "breadth": ("等权 RSP 减 SPY（开盘起）", "num"), "rotation": ("消费金融 减 科技芯片（开盘起）", "num"),
    "tlt": ("TLT 开盘起", "num"), "gld": ("GLD 开盘起", "num"),
    "monday": ("周一", "flag"), "friday": ("周五", "flag"), "fomc": ("FOMC 决议日", "flag"), "fomc_eve": ("FOMC 前一天", "flag"),
    "election_eve": ("选举前一天", "flag"), "opex": ("月度期权到期日", "flag"),
}


# ---------------------------------------------------------------- features

def _move(m: dict[str, dict], at: str) -> float | None:
    """Close at `at` against the 09:30 open."""
    b = at_or_before(m, at)
    return None if not b or "09:30" not in m else float(b["c"]) / float(m["09:30"]["o"]) - 1


def _closes(days: dict[str, dict]) -> dict[str, float]:
    out = {}
    for d, m in days.items():
        ks = [k for k in m if k <= "15:59"]
        if ks:
            out[d] = float(m[max(ks)]["c"])
    return out


def opex_day(d: str) -> bool:
    x = date.fromisoformat(d)
    return x.weekday() == 4 and 15 <= x.day <= 21


def features(spy: dict[str, dict], others: dict[str, dict[str, dict]]) -> list[dict]:
    dates = sorted(spy)
    closes = _closes(spy)
    oclose = {s: _closes(m) for s, m in others.items()}
    vol1h = {}
    for d, m in spy.items():
        vol1h[d] = sum(float(v.get("v", 0)) for k, v in m.items() if "09:30" <= k < "10:30")
    rows = []
    for i, d in enumerate(dates):
        m = spy[d]
        if "09:30" not in m or i < 21:
            continue
        prev = [x for x in dates[:i] if x in closes]
        if len(prev) < 21:
            continue
        p1, p5, p20 = closes[prev[-1]], closes[prev[-5]], [closes[x] for x in prev[-20:]]
        b1130, b1530 = at_or_before(m, CHECK), at_or_before(m, OUT)
        if not (b1130 and b1530):
            continue
        c1130, c1530 = float(b1130["c"]), float(b1530["c"])
        o = float(m["09:30"]["o"])
        hi = max(((float(v["h"]), k) for k, v in m.items() if k <= CHECK), default=(o, "09:30"))
        pm_prev = None
        pm_m = spy.get(prev[-1])
        if pm_m:
            a, b = at_or_before(pm_m, CHECK), at_or_before(pm_m, OUT)
            if a and b:
                pm_prev = float(b["c"]) / float(a["c"]) - 1
        v10 = [vol1h[x] for x in prev[-10:] if vol1h.get(x)]
        vx = others.get(VIX_PROXY, {}).get(d)
        vxb = at_or_before(vx, CHECK) if vx else None
        vx_prev = oclose.get(VIX_PROXY, {}).get(prev[-1])

        def mv(sym: str) -> float | None:
            mm = others.get(sym, {}).get(d)
            return _move(mm, CHECK) if mm else None

        rot_pos = [x for x in (mv("XLY"), mv("XLF")) if x is not None]
        rot_neg = [x for x in (mv("XLK"), mv("SMH")) if x is not None]
        rsp = mv("RSP")
        rows.append({
            "day": d, "pm": round(c1530 / c1130 - 1, 5), "pm_close": round(closes[d] / c1130 - 1, 5) if d in closes else None,
            "week5": round(p1 / p5 - 1, 4), "trend20": round(p1 / (sum(p20) / 20) - 1, 4), "prev_pm": None if pm_prev is None else round(pm_prev, 4),
            "gap": round(o / p1 - 1, 4), "vol1h": round(vol1h[d] / (sum(v10) / len(v10)), 3) if v10 and vol1h.get(d) else None,
            "hold1030": round((_move(m, "10:30") or 0.0), 4), "hold1130": round(c1130 / o - 1, 4),
            "high_early": hi[1] < "10:00",
            "vix_div": None if not vx else (lambda x: None if x is None else round(x, 4))(vix_div(vx, CHECK)),
            "vix_day": None if not (vxb and vx_prev) else round(float(vxb["c"]) / vx_prev - 1, 4),
            "breadth": None if rsp is None else round(rsp - (c1130 / o - 1), 4),
            "rotation": None if not (rot_pos and rot_neg) else round(sum(rot_pos) / len(rot_pos) - sum(rot_neg) / len(rot_neg), 4),
            "tlt": mv("TLT"), "gld": mv("GLD"),
            "monday": date.fromisoformat(d).weekday() == 0, "friday": date.fromisoformat(d).weekday() == 4,
            "fomc": d in FOMC, "fomc_eve": (date.fromisoformat(d) + timedelta(days=1)).isoformat() in FOMC
            or (date.fromisoformat(d).weekday() == 4 and (date.fromisoformat(d) + timedelta(days=3)).isoformat() in FOMC),
            "election_eve": (date.fromisoformat(d) + timedelta(days=1)).isoformat() in ELECTIONS, "opex": opex_day(d),
        })
    return rows


# ---------------------------------------------------------------- buckets and votes

def cuts(train: list[dict], name: str) -> tuple[float, float] | None:
    xs = sorted(r[name] for r in train if r.get(name) is not None)
    if len(xs) < 30:
        return None
    return xs[len(xs) // 3], xs[2 * len(xs) // 3]


def bucket(r: dict, name: str, kind: str, cut: tuple[float, float] | None) -> str | None:
    v = r.get(name)
    if v is None:
        return None
    if kind == "flag":
        return "yes" if v else "no"
    if cut is None:
        return None
    return "low" if v < cut[0] else "high" if v >= cut[1] else "mid"


def cell(rs: list[dict]) -> dict | None:
    if not rs:
        return None
    pm = [r["pm"] for r in rs]
    return {"n": len(rs), "mean": round(statistics.mean(pm), 5), "median": round(statistics.median(pm), 5),
            "neg": round(sum(x < 0 for x in pm) / len(pm), 3), "big_neg": round(sum(x <= -0.005 for x in pm) / len(pm), 3),
            "big_pos": round(sum(x >= 0.005 for x in pm) / len(pm), 3)}


def rank(train: list[dict], val: list[dict]) -> dict:
    """Per signal: the buckets' afternoon on train and val, which bucket is bearish / bullish (chosen on train),
    and the val lift of that choice over the val base rate."""
    base_val = cell(val)
    out = {}
    for name, (label, kind) in SIGNALS.items():
        cut = cuts(train, name) if kind == "num" else None
        bs = ("yes", "no") if kind == "flag" else ("low", "mid", "high")
        tr = {b: cell([r for r in train if bucket(r, name, kind, cut) == b]) for b in bs}
        va = {b: cell([r for r in val if bucket(r, name, kind, cut) == b]) for b in bs}
        ok = {b: c for b, c in tr.items() if c and c["n"] >= 30}
        bear = min(ok, key=lambda b: ok[b]["mean"]) if ok else None
        bull = max(ok, key=lambda b: ok[b]["mean"]) if ok else None
        if bear == bull:
            bull = None
        out[name] = {"label": label, "kind": kind, "cut": cut, "train": tr, "val": va, "bear": bear, "bull": bull,
                     "val_bear_lift": round(va[bear]["neg"] - base_val["neg"], 3) if bear and va.get(bear) else None,
                     "val_bull_lift": round(-(va[bull]["neg"] - base_val["neg"]), 3) if bull and va.get(bull) else None}
    return out


def vote(r: dict, ranked: dict) -> int:
    v = 0
    for name, info in ranked.items():
        b = bucket(r, name, info["kind"], info["cut"])
        if b is None:
            continue
        if b == info["bear"]:
            v -= 1
        elif b == info["bull"]:
            v += 1
    return v


# ---------------------------------------------------------------- option replay on the vote days

def option_day(client, day: str, m: dict[str, dict], kind: str, pace=None) -> dict | None:
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
    sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, OFF)
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
    got = play_all(et_minutes(bars[sym]), m, kind, float(m["09:30"]["o"]), entry=CHECK)
    return None if not got else {"kind": kind, "take3": got["res"]["take3"], "hold": got["res"]["hold"]}


def gather(client, pace=None, start: str = STOCK_START, end: str | None = None, log=lambda *a: None) -> dict:
    now = datetime.now(ZoneInfo("America/New_York"))
    end = end or (now.date().isoformat() if now.hour >= 17 else (now.date() - timedelta(days=1)).isoformat())
    spy = minutes_by_day(client, UND, start, end)
    others = {}
    for s in OTHERS:
        others[s] = minutes_by_day(client, s, start, end)
        log(s, len(others[s]), "days")
    rows = features(spy, others)
    log(UND, len(rows), "rows")
    train = [r for r in rows if r["day"] <= TRAIN_UNTIL]
    val = [r for r in rows if r["day"] > TRAIN_UNTIL]
    ranked = rank(train, val)
    for r in rows:
        r["vote"] = vote(r, ranked)
        if r["day"] >= OPT_START and abs(r["vote"]) >= 3:
            r["options"] = option_day(client, r["day"], spy[r["day"]], "P" if r["vote"] <= -3 else "C", pace)
    log("option days", sum(1 for r in rows if r.get("options")))
    return {"rows": rows, "ranked": ranked, "spy_days": len(spy)}


# ---------------------------------------------------------------- the study

def run(data: dict, today: date | None = None) -> dict:
    rows, ranked = data["rows"], data["ranked"]
    train = [r for r in rows if r["day"] <= TRAIN_UNTIL]
    val = [r for r in rows if r["day"] > TRAIN_UNTIL]
    by_vote = {}
    for lo, hi, label in ((-99, -3, "≤ −3（偏空）"), (-2, -2, "−2"), (-1, -1, "−1"), (0, 0, "0"), (1, 1, "+1"), (2, 2, "+2"), (3, 99, "≥ +3（偏多）")):
        by_vote[label] = {"train": cell([r for r in train if lo <= r["vote"] <= hi]), "val": cell([r for r in val if lo <= r["vote"] <= hi])}
    # pairs among the five signals with the best validation bear lift
    top = sorted([n for n in ranked if ranked[n]["val_bear_lift"] is not None], key=lambda n: -ranked[n]["val_bear_lift"])[:6]
    pairs = []
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            ia, ib = ranked[a], ranked[b]
            rs_v = [r for r in val if bucket(r, a, ia["kind"], ia["cut"]) == ia["bear"] and bucket(r, b, ib["kind"], ib["cut"]) == ib["bear"]]
            rs_t = [r for r in train if bucket(r, a, ia["kind"], ia["cut"]) == ia["bear"] and bucket(r, b, ib["kind"], ib["cut"]) == ib["bear"]]
            if len(rs_v) >= 15:
                pairs.append({"a": a, "b": b, "train": cell(rs_t), "val": cell(rs_v)})
    pairs.sort(key=lambda p: -p["val"]["neg"])
    opt = {}
    for side, label in (("P", "偏空日 11:30 买看跌"), ("C", "偏多日 11:30 买看涨")):
        rs = [r for r in rows if r.get("options") and r["options"]["kind"] == side]
        for key in ("take3", "hold"):
            xs = [(r["day"], r["options"][key]) for r in rs]
            if xs:
                v = [x for _, x in xs]
                g = game(xs, 2) if len(xs) >= 2 else {"heroes": 0, "zeros": 0}
                opt[f"{label} · {'3 倍止盈' if key == 'take3' else '拿到 15:30'}"] = {
                    "n": len(v), "mean": round(statistics.mean(v), 3), "median": round(statistics.median(v), 3),
                    "win": round(sum(x > 0 for x in v) / len(v), 3), "x2": round(sum(x >= 1 for x in v) / len(v), 3),
                    "h2": g["heroes"], "z2": g["zeros"], "robust": robust.summary(xs)}
    last = rows[-1] if rows else None
    return {"generated": (today or date.today()).isoformat(), "n_train": len(train), "n_val": len(val),
            "days": [rows[0]["day"], rows[-1]["day"]] if rows else None, "base": {"train": cell(train), "val": cell(val)},
            "ranked": ranked, "by_vote": by_vote, "pairs": pairs[:10], "options": opt,
            "last": None if not last else {k: last.get(k) for k in ["day", "pm", "vote"] + list(SIGNALS)},
            "last_buckets": None if not last else {n: bucket(last, n, ranked[n]["kind"], ranked[n]["cut"]) for n in SIGNALS}}


def _p(x, nd=2):
    return "—" if x is None else f"{x:+.{nd}%}"


def _c(c: dict | None) -> str:
    return "—" if not c else f"{c['n']} / {_p(c['mean'])} / {c['neg']:.0%}"


def markdown(rep: dict) -> str:
    bt, bv = rep["base"]["train"], rep["base"]["val"]
    md = [f"# 哪些 11:30 的信号对 SPY 下午说得上话 {rep['generated']}", "",
          f"SPY {rep['days'][0] if rep.get('days') else '—'} 到 {rep['days'][1] if rep.get('days') else '—'}，训练 2019–2024（{rep['n_train']} 天）、验证 2025–2026（{rep['n_val']} 天）。"
          f"下午 = 11:30 到 15:30。每个信号切三档（是 / 否的切两档），在训练期挑出下午最弱的一档叫「偏空档」、最强的叫「偏多档」，验证期只读不挑。"
          f"基线：训练期下午平均 {_p(bt['mean'])}、收跌 {bt['neg']:.0%}；验证期 {_p(bv['mean'])}、收跌 {bv['neg']:.0%}。只研究，不改规则。", "",
          "## 单个信号（每格：天数 / 下午平均 / 收跌比例）", "",
          "| 信号 | 档 | 训练 | 验证 | 训练挑出的偏空档 | 验证期收跌比基线高 | 偏多档 | 验证期收涨比基线高 |", "|---|---|---|---|---|---|---|---|"]
    for name, info in sorted(rep["ranked"].items(), key=lambda kv: -(kv[1]["val_bear_lift"] or -1)):
        bs = ("yes", "no") if info["kind"] == "flag" else ("low", "mid", "high")
        zh = {"yes": "是", "no": "否", "low": "低", "mid": "中", "high": "高"}
        first = True
        for b in bs:
            md.append(f"| {info['label'] if first else ''} | {zh[b]} | {_c(info['train'].get(b))} | {_c(info['val'].get(b))} | "
                      f"{(zh[info['bear']] if info['bear'] else '—') if first else ''} | {(_p(info['val_bear_lift'], 1) if info['val_bear_lift'] is not None else '—') if first else ''} | "
                      f"{(zh[info['bull']] if info['bull'] else '—') if first else ''} | {(_p(info['val_bull_lift'], 1) if info['val_bull_lift'] is not None else '—') if first else ''} |")
            first = False
    md += ["", "## 投票：一天落在几个偏空档减几个偏多档", "", "| 票数 | 训练 | 验证 |", "|---|---|---|"]
    for label, c in rep["by_vote"].items():
        md.append(f"| {label} | {_c(c['train'])} | {_c(c['val'])} |")
    md += ["", "## 验证期偏空档两两组合（验证期至少 15 天）", "", "| 信号 A | 信号 B | 训练 | 验证 |", "|---|---|---|---|"]
    for p in rep["pairs"]:
        md.append(f"| {rep['ranked'][p['a']]['label']} | {rep['ranked'][p['b']]['label']} | {_c(p['train'])} | {_c(p['val'])} |")
    md += ["", f"## 期权层面（{OPT_START} 起）：票数 ≤ −3 买当天到期看跌、≥ +3 买看涨，离 11:30 价 {OFF:.1%}", "",
           "| 情况 | 张数 | 平均每 $1 | 中位数 | 赚钱比例 | 到 2 倍 | 账本到 2 倍 成功·归零 |", "|---|---|---|---|---|---|---|"]
    for label, c in rep["options"].items():
        md.append(f"| {label} | {c['n']} | {_p(c['mean'], 0)} | {_p(c['median'], 0)} | {c['win']:.0%} | {c['x2']:.0%} | {c['h2']}·{c['z2']} |")
    if rep["options"]:
        md += ["", robust.table({k: v["robust"] for k, v in rep["options"].items()}, "稳健性检查（每张票一笔，每 $1 的收益）")]
    if rep.get("last"):
        L, B = rep["last"], rep["last_buckets"]
        zh = {"yes": "是", "no": "否", "low": "低", "mid": "中", "high": "高", None: "—"}
        md += ["", f"## 最后一天（{L['day']}）的信号和票数", "", f"票数 {L['vote']:+d}，下午实际 {_p(L['pm'])}。", "",
               "| 信号 | 值 | 档 | 偏空 / 偏多 |", "|---|---|---|---|"]
        for n, (label, kind) in SIGNALS.items():
            info = rep["ranked"][n]
            tag = "偏空" if B.get(n) == info["bear"] else "偏多" if B.get(n) == info["bull"] else ""
            v = L.get(n)
            md.append(f"| {label} | {v if isinstance(v, bool) or v is None else (f'{v:+.2%}' if abs(v) < 5 else f'{v:.2f}')} | {zh.get(B.get(n), '—')} | {tag} |")
    md += ["", "## 读法", "",
           "- 一个信号算「有话说」，要它的偏空档在验证期的收跌比例明显高于基线（比如 +8 个百分点以上）、而且训练期同向；只在训练期好看的是过拟合。",
           "- 投票表是最该看的：如果 ≤ −3 那行验证期收跌比例没有明显高于 0 那行，二十个信号加起来也没信号。",
           "- 期权层面的张数少，去掉最好 3 笔和多付成本后还赚才算。",
           "- 下午 11:30 到 15:30 的平均本来就接近零，收跌比例 44% 上下是常态；任何 60% 以上的格子先看天数。", ""]
    return "\n".join(md)


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    data = gather(client, pace=Pace(client), log=lambda *a: print(*a, file=sys.stderr))
    rep = run(data)
    rep["rows"] = [{k: v for k, v in r.items()} for r in data["rows"] if r["day"] > TRAIN_UNTIL or r.get("options")]
    save("signals", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
