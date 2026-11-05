"""The follow card over ten years, with a modelled same-day option. (owner, 11-05: 「那可以直接回测啊」)

Real option bars start in 2024-02, which gave the card 52 trigger days (research/2026-11-05-zdte-logic-review.md).
To see ten years, the card's contract is priced off SPY's minute path: Black-Scholes every minute from 10:00 to
15:30, implied vol = k x VIX with VIX read from Yahoo's daily open and close and interpolated through the day (so a
crash day's vol expansion is in), time to expiry in trading minutes to 16:00, $1 strikes, the same 10% in and out
and the same exits as the live replay (hero.research_zdte_timing.play). k is fitted on the 2024-02+ days, calls
and puts apart, so the modelled 10:00 premium matches the real one (research/2026-11-02-zdte-stop.json); then the
modelled outcomes are checked against the real ones day by day before 2016-2023 is read. A model, not a tape: the
check says how far to trust it. Record only.
Run: python -m hero.research_zdte_synth
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

from hero import robust
from hero.research_0dte import at_or_before, strike_for
from hero.research_0dte_names import minutes_by_day
from hero.research_ideas import ROOT, save
from hero.research_zdte_timing import OUT, play

START = "2016-01-04"
REAL_START = "2024-02-01"
FLOOR = 0.005
OFF = 0.003
STOP_JSON = ROOT / "research" / "2026-11-02-zdte-stop.json"
EXITS = {"take3_trail": "一半 3 倍、其余回撤（现行）", "take3": "3 倍全卖", "hold": "拿到 15:30"}


def vix_daily(start: str) -> dict[str, dict]:
    """day -> {o, c} for the VIX index from Yahoo; empty when the fetch fails."""
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?period1={p1}"
           f"&period2={int(datetime.now().timestamp())}&interval=1d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    except Exception:
        return {}
    q = res["indicators"]["quote"][0]
    day = lambda t: (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)).date().isoformat()
    return {day(t): {"o": float(o), "c": float(c)} for t, o, c in zip(res["timestamp"], q["open"], q["close"]) if o and c}


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot: float, strike: float, kind: str, t_years: float, iv: float) -> float:
    """Black-Scholes, zero rate; intrinsic at expiry; never under a cent."""
    if t_years <= 0 or iv <= 0:
        return max(0.01, (spot - strike) if kind == "C" else (strike - spot))
    s = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * s * s) / s
    d2 = d1 - s
    px = spot * ncdf(d1) - strike * ncdf(d2) if kind == "C" else strike * ncdf(-d2) - spot * ncdf(-d1)
    return max(px, 0.01)


def minute_index(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h - 9) * 60 + m - 30


def synth(m: dict[str, dict], kind: str, strike: float, vo: float, vc: float, k: float) -> dict[str, dict]:
    """The modelled option's minute closes from 09:30 to 15:30 off SPY's minute closes."""
    out = {}
    for hhmm, bar in m.items():
        if not ("09:30" <= hhmm <= OUT):
            continue
        i = minute_index(hhmm)
        iv = k * (vo + (vc - vo) * i / 390) / 100
        out[hhmm] = {"c": round(bs(float(bar["c"]), strike, kind, (390 - i) / 390 / 252, iv), 4)}
    return out


def real_rows() -> dict[str, dict]:
    """The real replay (SPY, 0.3%): day -> kind, spot, raw 10:00 premium, exits."""
    if not STOP_JSON.exists():
        return {}
    rows = json.load(open(STOP_JSON))["rows"]
    return {r["day"]: {"kind": r["kind"], "spot": r["spot"], "prem": r["cost"] / 1.1, "res": r["res"]}
            for r in rows if r["und"] == "SPY" and r["off"] == 0.003}


def run(client, vix: dict[str, dict] | None = None, real: dict[str, dict] | None = None) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    vix = vix_daily(START) if vix is None else vix
    real = real_rows() if real is None else real
    days = minutes_by_day(client, "SPY", START, end)
    try:
        vixy = minutes_by_day(client, "VIXY", START, end)
    except Exception:
        vixy = {}
    base, prev_close, vol30s = [], None, []
    for day in sorted(days):
        m = days[day]
        b10 = at_or_before(m, "10:00")
        if "09:30" not in m or not b10 or not at_or_before(m, OUT):
            continue
        o, spot = float(m["09:30"]["o"]), float(b10["c"])
        mv = spot / o - 1
        kind = "C" if mv >= 0 else "P"
        v = vix.get(day) or {"o": 20.0, "c": 20.0}
        # early-warning features, all known at 10:00 (owner, 11-05: 「我们要预警，能分辨出」)
        first = [b for k, b in m.items() if "09:30" <= k <= "10:00"]
        vol30 = sum(float(b.get("v") or 0) for b in first)
        hi30, lo30 = max(float(b["h"]) for b in first), min(float(b["l"]) for b in first)
        pos10 = (spot - lo30) / (hi30 - lo30) if hi30 > lo30 else 0.5
        vm = vixy.get(day) or {}
        vb10 = at_or_before(vm, "10:00") if vm else None
        vprev = base[-1]["vixy_close"] if base else None
        feats = {"gap": round(o / prev_close - 1, 5) if prev_close else None, "vol30": vol30,
                 "vol30_avg20": statistics.mean(vol30s[-20:]) if len(vol30s) >= 10 else None,
                 "pos10": round(pos10, 3), "range30": round(hi30 / lo30 - 1, 5),
                 "vixy10": round(float(vb10["c"]) / vprev - 1, 4) if vb10 and vprev else None}
        vlast = at_or_before(vm, "15:59") if vm else None
        base.append({"day": day, "move": round(mv, 5), "kind": kind, "spot": spot, "strike": strike_for(spot, kind, OFF),
                     "vo": v["o"], "vc": v["c"], "m": m, "feats": feats,
                     "vixy_close": float(vlast["c"]) if vlast else vprev,
                     "close": float(m[max(m)]["c"]), "p1530": float(at_or_before(m, OUT)["c"])})
        prev_close = float(m[max(m)]["c"])
        vol30s.append(vol30)
    # fit k per side: real 10:00 premium / modelled premium at k = 1
    ks = {}
    for kind in ("C", "P"):
        ratios = []
        for r in base:
            rr = real.get(r["day"])
            if r["kind"] != kind or not rr or rr["kind"] != kind:
                continue
            p1 = synth(r["m"], kind, r["strike"], r["vo"], r["vc"], 1.0).get(max(k for k in r["m"] if k <= "10:00"))
            if p1 and p1["c"] > 0.011:
                ratios.append(rr["prem"] / p1["c"])
        ratios.sort()
        ks[kind] = {"k": statistics.median(ratios) if ratios else 0.5, "n": len(ratios),
                    "q1": ratios[len(ratios) // 4] if ratios else None, "q3": ratios[3 * len(ratios) // 4] if ratios else None}
    rows = []
    for r in base:
        opt = synth(r["m"], r["kind"], r["strike"], r["vo"], r["vc"], ks[r["kind"]]["k"])
        got = play(opt, "10:00")
        if not got:
            continue
        rr = real.get(r["day"])
        rows.append({k: v for k, v in r.items() if k not in ("m", "vixy_close")} | {
            "prem": opt[max(k for k in opt if k <= "10:00")]["c"], "res": {x: got[x] for x in EXITS},
            "real_prem": rr["prem"] if rr else None, "real": {x: rr["res"].get(x) for x in EXITS} if rr else None})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "k": ks, "rows": rows}


def check(rows: list[dict]) -> dict:
    """Model against tape on the days that have both, triggers and all days: means, medians, correlation."""
    out = {}
    for name, keep in (("触发日（0.5% 以上）", lambda r: abs(r["move"]) >= FLOOR), ("每天都做", lambda r: True)):
        pairs = [(r["res"]["take3_trail"], r["real"]["take3_trail"]) for r in rows if r["real"] and keep(r)]
        if len(pairs) < 3:
            continue
        a, b = [p[0] for p in pairs], [p[1] for p in pairs]
        ma, mb = statistics.mean(a), statistics.mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in pairs)
        corr = cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) if cov else 0.0
        prem = [(r["prem"], r["real_prem"]) for r in rows if r["real"] and keep(r)]
        out[name] = {"n": len(pairs), "model_mean": ma, "real_mean": mb, "model_median": statistics.median(a),
                     "real_median": statistics.median(b), "corr": corr,
                     "same_sign": sum(1 for x, y in pairs if (x > 0) == (y > 0)) / len(pairs),
                     "prem_ratio_median": statistics.median(p / q for p, q in prem if q)}
    return out


def groups(rows: list[dict], ex: str = "take3_trail") -> dict[str, list[tuple[str, float]]]:
    trig = [r for r in rows if abs(r["move"]) >= FLOOR]
    g = {"触发日，全部（模型）": [(r["day"], r["res"][ex]) for r in trig]}
    for name, keep in (("　2016–2019", lambda d: d < "2020"), ("　2020–2023", lambda d: "2020" <= d < "2024"), ("　2024 起", lambda d: d >= "2024")):
        g[name] = [(r["day"], r["res"][ex]) for r in trig if keep(r["day"])]
    g["　2024 起，真实期权（对照）"] = [(r["day"], r["real"][ex]) for r in trig if r["real"] and r["real"].get(ex) is not None]
    g["　高开走高（买看涨）"] = [(r["day"], r["res"][ex]) for r in trig if r["kind"] == "C"]
    g["　低开走低（买看跌）"] = [(r["day"], r["res"][ex]) for r in trig if r["kind"] == "P"]
    g["　前一天 VIX 收盘 ≥ 24"] = [(r["day"], r["res"][ex]) for r in trig if r["vo"] >= 24]
    g["　前一天 VIX 收盘 < 24"] = [(r["day"], r["res"][ex]) for r in trig if r["vo"] < 24]
    g["每天都做（模型）"] = [(r["day"], r["res"][ex]) for r in rows]
    return g


def markdown(rep: dict) -> str:
    rows, ks, end = rep["rows"], rep["k"], rep["end"]
    chk = check(rows)
    q = lambda side, key: "—" if ks[side].get(key) is None else f"{ks[side][key]:.2f}"
    out = [f"# 跟单卡十年回测（模型期权） {rep['generated']}", "",
           f"{rep['start']} 至 {end}，SPY 分钟线 {len(rows)} 个交易日。真实期权只有 2024-02 起；这里用 Black-Scholes 按 SPY 每分钟的价格"
           "给当天到期、离 10:00 价 0.3% 外的那张定价，隐含波动 = k × VIX（VIX 用当天开盘和收盘插值，崩盘日的波动放大算进去），"
           "k 在 2024-02 起的日子上按真实的 10:00 买价拟合（看涨、看跌各一个），买卖各多付 10%，出场同现行。"
           "先看模型对不对得上真实结果，再读 2016–2023。模型不是行情，差多少见「校准」。只研究，不改交易。", "",
           "## 校准", "",
           f"- k（真实 10:00 买价 ÷ 按 VIX 定价）：看涨 {q('C', 'k')}（{ks['C']['n']} 天，四分位 {q('C', 'q1')}–{q('C', 'q3')}）、"
           f"看跌 {q('P', 'k')}（{ks['P']['n']} 天，四分位 {q('P', 'q1')}–{q('P', 'q3')}）。",
           "", "| 2024-02 起 | 天数 | 模型平均每 $1 | 真实平均 | 模型中位 | 真实中位 | 逐日相关 | 盈亏方向一致 | 买价比（模型 / 真实）中位 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for name, c in chk.items():
        out.append(f"| {name} | {c['n']} | {c['model_mean']:+.0%} | {c['real_mean']:+.0%} | {c['model_median']:+.0%} | {c['real_median']:+.0%} | "
                   f"{c['corr']:.2f} | {c['same_sign']:.0%} | {c['prem_ratio_median']:.2f} |")
    for ex, exn in EXITS.items():
        g = groups(rows, ex)
        t = {name: robust.summary(xs, end) for name, xs in g.items()}
        out += ["", f"## {exn}", "", robust.table(t, f"按 {exn} 出场").split("\n", 2)[2]]
    out += ["", "## 每年（现行出场，触发日，模型；2024 起另列真实）", "",
            "| 年 | 交易日 | 触发 | 模型平均每 $1 | 模型中位 | 赚钱比例 | 真实平均（有的话） |", "|---|---|---|---|---|---|---|"]
    years = sorted({r["day"][:4] for r in rows})
    for y in years:
        yr = [r for r in rows if r["day"][:4] == y]
        tr = [r for r in yr if abs(r["move"]) >= FLOOR]
        if not tr:
            out.append(f"| {y} | {len(yr)} | 0 | — | — | — | — |")
            continue
        xs = [r["res"]["take3_trail"] for r in tr]
        rl = [r["real"]["take3_trail"] for r in tr if r["real"]]
        out.append(f"| {y} | {len(yr)} | {len(tr)} | {statistics.mean(xs):+.0%} | {statistics.median(xs):+.0%} | "
                   f"{sum(1 for x in xs if x > 0) / len(xs):.0%} | {('%+.0f%%' % (100 * statistics.mean(rl))) if rl else '—'} |")
    out += ["", "## 每一天（触发日，模型；2024 起附真实）", "",
            "| 日期 | 方向 | 10:00 离开盘 | VIX 开 → 收 | 模型买价 | 真实买价 | 模型（现行 / 15:30） | 真实（现行 / 15:30） |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if abs(r["move"]) < FLOOR:
            continue
        rl = r["real"]
        out.append(f"| {r['day']} | {'看涨' if r['kind'] == 'C' else '看跌'} | {r['move']:+.2%} | {r['vo']:.0f} → {r['vc']:.0f} | ${r['prem']:.2f} | "
                   f"{('$%.2f' % r['real_prem']) if r['real_prem'] else '—'} | {r['res']['take3_trail']:+.0%} / {r['res']['hold']:+.0%} | "
                   f"{('%+.0f%% / %+.0f%%' % (100 * rl['take3_trail'], 100 * rl['hold'])) if rl and rl.get('hold') is not None else '—'} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    save("zdte-synth", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
