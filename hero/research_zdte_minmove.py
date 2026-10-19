"""The 0DTE sleeves: skip the day unless the price has already moved from the open? (owner, 10-16: test it first.)

10-16 the NVDA sleeves bought a put at 10:00 with NVDA 0.12% below its open and lost $1,216; claude-b's numbers
say SPY does better when it has already moved 0.5% by 10:00. research/2026-10-08-zdte-filter.md looked at this for
SPY / QQQ with everything sold at 3x; the sleeves now sell half at 3x and trail the rest, which is the exit the
fat tails need, and NVDA / TSLA were never tested. Here, every weekday since 2024-02 on SPY, QQQ, NVDA, TSLA, with
the live pick (the expiry of the day, else the nearest within a week; a call above the open, else a put; the first
strike 0.6% -- and 0.3% -- beyond the price), entries at 09:35 and 10:00, played through hero.research_zdte_timing
.play (minute closes, 10% over to get in, 10% under to get out) plus take5_trail (half at 5x, the rest on the
trail) for the TSLA 5x sleeve; grouped by how far the price had moved from the open at the entry. Record only.
Run: python -m hero.research_zdte_minmove
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta

from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero import robust
from hero.research_event_turn import Pace
from hero.research_ideas import save
from hero.research_zdte_timing import OUT, game, play

START = "2024-02-01"
NAMES = ("SPY", "QQQ", "NVDA", "TSLA")
ENTRIES = ("09:35", "10:00")
OFFS = (0.006, 0.003)
# The live sleeves (config/s500.json): name, entry, strike offset, exit.
SLEEVES = [("SPY", "10:00", 0.006, "take3_trail", "SPY 末日单（spy0dte）"),
           ("SPY", "10:00", 0.003, "take3_trail", "SPY 末日单 0.3%（spy0dte-near）"),
           ("QQQ", "09:35", 0.006, "take3_trail", "QQQ 末日单 09:35（qqq0dte）"),
           ("NVDA", "10:00", 0.006, "take3_trail", "NVDA 末日单（nvda0dte）"),
           ("TSLA", "10:00", 0.006, "take3_trail", "TSLA 末日单（tsla0dte）"),
           ("TSLA", "10:00", 0.006, "take5_trail", "TSLA 末日单 5 倍（tsla0dte-5x）")]
BANDS = [(0.0, 0.001), (0.001, 0.002), (0.002, 0.003), (0.003, 0.005), (0.005, 0.01), (0.01, 1.0)]
FLOORS = (0.001, 0.002, 0.003, 0.005)
EXITS = {"take3_trail": "一半 3 倍、其余回撤（现行）", "take3": "3 倍全卖", "hold": "拿到 15:30"}


def with_take5(res: dict[str, float]) -> dict[str, float]:
    """Half at 5x and the rest on the trail, like take3_trail (both halves on the trail when 5x never comes)."""
    return {**res, "take5_trail": round((res["take5"] + res["trail2"]) / 2 if res["take5"] == 4 else res["trail2"], 4)}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, rows = Pace(client), []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or not at_or_before(m, OUT):
                continue
            o = float(m["09:30"]["o"])
            plan = {}
            for e in ENTRIES:
                b = at_or_before(m, e)
                if b:
                    spot = float(b["c"])
                    plan[e] = ("C" if spot >= o else "P", spot, spot / o - 1)
            if not plan:
                continue
            lo, hi = min(s for _, s, _ in plan.values()) * 0.97, max(s for _, s, _ in plan.values()) * 1.03
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            near = [c for c in cs if c["expiration_date"] == exp]
            syms = {(e, off): pick(near, k, spot, off) for e, (k, spot, _) in plan.items() for off in OFFS}
            want = sorted({s for s in syms.values() if s})
            if not want:
                continue
            pace.wait()
            try:
                bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            mins = {s: et_minutes(b) for s, b in bars.items()}
            res = {}
            for (e, off), s in syms.items():
                got = play(mins[s], e) if s and s in mins else None
                if got:
                    res[f"{e}|{off}"] = with_take5(got)
            if res:
                rows.append({"und": und, "day": day, "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days,
                             "move": {e: round(mv, 5) for e, (_, _, mv) in plan.items()}, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def trades(rows: list[dict], und: str, entry: str, off: float, ex: str, keep=lambda mv: True) -> list[tuple[str, float]]:
    key = f"{entry}|{off}"
    return [(r["day"], r["res"][key][ex]) for r in rows
            if r["und"] == und and key in r["res"] and entry in r["move"] and keep(abs(r["move"][entry]))]


def groups(rows: list[dict], und: str, entry: str, off: float, ex: str) -> dict[str, list[tuple[str, float]]]:
    g = {"现行：每天都做": trades(rows, und, entry, off, ex)}
    for lo, hi in BANDS:
        name = f"　开盘到 {entry} 走了 {lo:.1%}–{hi:.1%}" if hi < 1 else f"　开盘到 {entry} 走了 {lo:.1%} 以上"
        g[name] = trades(rows, und, entry, off, ex, lambda mv, lo=lo, hi=hi: lo <= mv < hi)
    for f in FLOORS:
        g[f"不到 {f:.1%} 就不做"] = trades(rows, und, entry, off, ex, lambda mv, f=f: mv >= f)
    return g


def stats(xs: list[tuple[str, float]], end: str) -> dict | None:
    if not xs:
        return None
    s = robust.summary(xs, end)
    part = lambda f: (lambda p: (statistics.mean(p), len(p)) if p else None)([x for d, x in xs if f(d)])
    g2, g20 = game(xs, 2), game(xs, 20)
    return {**s, "y24": part(lambda d: d < "2025-01-01"), "y2526": part(lambda d: d >= "2025-01-01"),
            "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}


def fmt(s: dict | None, key: str) -> str:
    if not s or s.get(key) is None:
        return "—"
    v = s[key]
    return f"{v[0]:+.0%}（{v[1]}）" if isinstance(v, tuple) else f"{v:+.0%}"


def report(rep: dict) -> tuple[dict, str]:
    end, table, out = rep["end"], {}, []
    for und, entry, off, live, title in SLEEVES:
        out += [f"## {title}：{und} {entry} 进场，离现价 {off:.1%}", "",
                f"现行出场（{EXITS.get(live, '一半 5 倍、其余回撤（现行）')}）：", "",
                "| 情况 | 笔数 | 平均每 $1 | 去掉最好 3 笔 | 中位数 | 收在 2 倍以上 | 2024 | 2025–26 | 最近 3 个月 | 账本到 2 倍：成功 / 归零 / 到 20 倍 | 提醒 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        g = groups(rep["rows"], und, entry, off, live)
        for name, xs in g.items():
            s = stats(xs, end)
            table[f"{und}|{entry}|{off}|{live}|{name}"] = s
            if not s:
                continue
            out.append(f"| {name} | {s['n']} | {fmt(s, 'mean')} | {fmt(s, 'trim')} | {fmt(s, 'median')} | {s['x2']:.0%} | "
                       f"{fmt(s, 'y24')} | {fmt(s, 'y2526')} | {fmt(s, 'recent')} | {s['h2']} / {s['z2']} / {s['h20']} | "
                       f"{'；'.join(s['flags']) or '—'} |")
        others = [x for x in EXITS if x != live]
        out += ["", "其他出场（平均每 $1 / 中位数）：", "", "| 情况 | " + " | ".join(EXITS[x] for x in others) + " |",
                "|---|" + "---|" * len(others)]
        gx = {x: groups(rep["rows"], und, entry, off, x) for x in others}
        for name in g:
            cells = []
            for x in others:
                s = stats(gx[x][name], end)
                table[f"{und}|{entry}|{off}|{x}|{name}"] = s
                cells.append(f"{fmt(s, 'mean')} / {fmt(s, 'median')}" if s else "—")
            out.append(f"| {name} | " + " | ".join(cells) + " |")
        out.append("")
    head = [f"# 末日单：开盘后走得不够就不做？ {rep['generated']}", "",
            f"{rep['start']} 至 {rep['end']}，SPY / QQQ / NVDA / TSLA 每个交易日，按现行挑法（当天到期，没有就一周内最近到期；"
            "比开盘高买看涨、低买看跌；行权价离现价至少 0.6% 或 0.3%），按分钟收盘价卖，买入多付 10%、卖出少拿 10%。"
            "按进场时离开盘走了多远分组，再看「不到某个幅度就不做」。账本玩法：每笔押一半，低于 10% 算归零。"
            "「去掉最好 3 笔」和「最近 3 个月」用来看平均是不是靠少数几笔撑起来的。只研究，不改交易。", ""]
    return table, "\n".join(head + out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(rep)
    save("zdte-minmove", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
