"""Replay a signal group's published record on real daily bars (owner, 10-23: 「C 上传了一个东西」).

claude-c put the group's own results workbook in exchange/claude-c/ of the public peer repo (the user chose to publish
it). Its headline numbers count each signal by the highest take-profit zone the price ever reached (the sheet's own
disclaimer calls this 虚高), in a half year when the names it picks (MU, NBIS, DELL, chips, AI) ran hard. This replays
every signal on split-adjusted SIP daily bars: entry at the sheet's price if the signal day traded through it (else the
next open), then the close 1 / 5 / 10 / 20 sessions on, the best and worst points inside 10 sessions, whether a -6.3%
stop (the sheet's average loss) hit before +5%, and the same windows on SMH and SPY to take the market out. Reports only
aggregates: the group's list is paid content and stays in their repo. hero.robust on the trade series. Research only."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
PEER_URL = "https://github.com/Cao-YMGG/zero-or-hero"
PEER_DIR = Path(os.environ.get("PEER_REPO_DIR", "/home/user/cao-ymgg/zero-or-hero"))
SHEET = "exchange/claude-c/阿德里安2026美股交易汇总.xlsx"
STOP, TARGET, COST = -0.063, 0.05, 0.001
HOLDS = (1, 5, 10, 20)


def parse(path: Path) -> list[dict]:
    """(day, symbol, entry price, the sheet's own max-TP return, TPs reached) from the workbook's main sheet."""
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Sheet1"]
    out = []
    for r in ws.iter_rows(values_only=True):
        d, sym, entry, tp, ntp = (tuple(r) + (None,) * 5)[:5]
        if not isinstance(d, datetime) or not sym or not isinstance(entry, (int, float)):
            continue
        try:
            tp = float(tp)
        except (TypeError, ValueError):
            tp = None
        out.append({"day": d.date().isoformat(), "sym": str(sym).strip().upper(), "entry": float(entry), "sheet_tp": tp,
                    "ntp": int(ntp) if isinstance(ntp, (int, float)) else None})
    return out


def peer_sheet() -> Path:
    if (PEER_DIR / SHEET).exists():
        return PEER_DIR / SHEET
    tmp = Path(tempfile.mkdtemp()) / "peer"
    subprocess.run(["git", "clone", "--depth", "1", PEER_URL, str(tmp)], check=True, env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    return tmp / SHEET


def replay(sig: dict, bars: list[dict], bench: dict[str, list[dict]]) -> dict | None:
    """One signal on its bars (dicts with t/o/h/l/c, oldest first)."""
    days = [b["t"][:10] for b in bars]
    if sig["day"] not in days:
        return None
    i = days.index(sig["day"])
    b0 = bars[i]
    if float(b0["l"]) <= sig["entry"] <= float(b0["h"]):
        entry, how, start = sig["entry"], "当天按表价", i
    elif i + 1 < len(bars):
        entry, how, start = float(bars[i + 1]["o"]), "第二天开盘", i + 1
    else:
        return None
    out = {"day": sig["day"], "sym": sig["sym"], "how": how, "sheet_tp": sig["sheet_tp"], "res": {}, "bench": {}}
    for h in HOLDS:
        j = start + h
        out["res"][f"c{h}"] = float(bars[j]["c"]) / entry - 1 - COST if j < len(bars) else None
    path = bars[start + 1: start + 11]
    out["mfe10"] = max((float(b["h"]) / entry - 1 for b in path), default=None)
    out["mae10"] = min((float(b["l"]) / entry - 1 for b in path), default=None)
    first = None   # which came first inside 10 sessions: the stop (on the low) or the target (on the high)
    for b in path:
        if float(b["l"]) / entry - 1 <= STOP:
            first = "stop"
            break
        if float(b["h"]) / entry - 1 >= TARGET:
            first = "target"
            break
    out["first"] = first or "neither"
    out["ladder"] = ladder(entry, bars[start + 1: start + 21])
    for name, bb in bench.items():
        bd = {b["t"][:10]: float(b["c"]) for b in bb}
        d0 = days[start]
        if d0 in bd:
            out["bench"][name] = {f"c{h}": (bd[days[start + h]] / bd[d0] - 1) if start + h < len(days) and days[start + h] in bd else None for h in HOLDS}
    return out


def momentum_at(sig_day: str, bars: list[dict]) -> float | None:
    """The stock's 20-session move into the signal day: the style question (momentum or dip)."""
    days = [b["t"][:10] for b in bars]
    if sig_day not in days:
        return None
    i = days.index(sig_day)
    return float(bars[i - 1]["c"]) / float(bars[i - 21]["c"]) - 1 if i >= 21 else None


def setup(sig_day: str, bars: list[dict]) -> dict | None:
    """What the chart looked like when the idea was posted: the 20-session move in, how far under the 20-session high,
    the signal day's own move and gap, its volume against the 20-session average."""
    days = [b["t"][:10] for b in bars]
    if sig_day not in days:
        return None
    i = days.index(sig_day)
    if i < 21:
        return None
    prev = [bars[k] for k in range(i - 20, i)]
    c0, cp = float(bars[i]["c"]), float(bars[i - 1]["c"])
    hi20 = max(float(b["h"]) for b in prev)
    vol20 = statistics.mean(float(b.get("v") or 0) for b in prev)
    return {"mom20": cp / float(bars[i - 21]["c"]) - 1, "from_high": c0 / hi20 - 1, "day": c0 / cp - 1,
            "gap": float(bars[i]["o"]) / cp - 1, "vol": (float(bars[i].get("v") or 0) / vol20) if vol20 else None}


LADDER = (0.012, 0.025, 0.04, 0.06, 0.10)   # the cards' typical levels on a $424 / $344 / $526 / $233 entry: +1% to +10%
LADDER_STOP = -0.06                           # about one ATR, the sheet's average loss is -6.3%


def ladder(entry: float, path: list[dict], levels=LADDER, stop=LADDER_STOP, horizon: int = 20) -> dict:
    """The group's own exit logic on daily bars: sell a fifth at each level, move the stop to the entry once the first
    level trades, the rest out at the stop or the horizon's close. Inside one day the touch nearer the open is taken
    first. Returns the weighted return per $1, the sessions held, the levels taken."""
    left, got, stop_px, taken = 1.0, 0.0, entry * (1 + stop), 0
    days = 0
    for b in path[:horizon]:
        days += 1
        o, h, lo = float(b["o"]), float(b["h"]), float(b["l"])
        while left > 1e-9:
            nxt = entry * (1 + levels[taken]) if taken < len(levels) else None
            hit_stop = lo <= stop_px
            hit_lvl = nxt is not None and h >= nxt
            if not hit_stop and not hit_lvl:
                break
            stop_first = hit_stop and (not hit_lvl or abs(o - stop_px) <= abs(o - nxt))
            if stop_first:
                px = min(o, stop_px) if o <= stop_px else stop_px
                got += left * (px / entry - 1)
                left = 0.0
                break
            px = max(o, nxt)
            slice_ = min(left, 1.0 / len(levels))
            got += slice_ * (px / entry - 1)
            left -= slice_
            taken += 1
            stop_px = max(stop_px, entry)          # breakeven after the first level
            if taken == len(levels):
                left = 0.0
        if left <= 1e-9:
            break
    if left > 1e-9 and path:
        got += left * (float(path[min(len(path), horizon) - 1]["c"]) / entry - 1)
    return {"ret": got - COST, "days": days, "levels": taken}


def buckets(rows: list[dict]) -> dict[str, dict]:
    """Real outcomes by the shape of the setup."""
    groups = {
        "信号前 20 天涨 10% 以上（追强势）": lambda f: f["mom20"] > 0.10,
        "信号前 20 天 0 到 10%": lambda f: 0 <= f["mom20"] <= 0.10,
        "信号前 20 天在跌（抄底）": lambda f: f["mom20"] < 0,
        "收在 20 日高点 3% 以内（突破 / 新高）": lambda f: f["from_high"] >= -0.03,
        "离 20 日高点 3–10%（回调中）": lambda f: -0.10 <= f["from_high"] < -0.03,
        "离 20 日高点 10% 以上（深回调）": lambda f: f["from_high"] < -0.10,
        "信号当天涨 3% 以上": lambda f: f["day"] >= 0.03,
        "信号当天跌 2% 以上": lambda f: f["day"] <= -0.02,
        "当天成交量是平时 2 倍以上": lambda f: (f["vol"] or 0) >= 2,
    }
    out = {}
    for name, keep in groups.items():
        rs = [r for r in rows if r.get("setup") and keep(r["setup"])]
        c10 = [r["res"]["c10"] for r in rs if r["res"].get("c10") is not None]
        ex = [r["res"]["c10"] - r["bench"]["SMH"]["c10"] for r in rs if r["res"].get("c10") is not None and r["bench"].get("SMH", {}).get("c10") is not None]
        if c10:
            out[name] = {"n": len(c10), "c10": statistics.mean(c10), "win": sum(x > 0 for x in c10) / len(c10),
                         "ex_smh": statistics.mean(ex) if ex else None,
                         "touch5": sum(r["first"] == "target" for r in rs) / len(rs), "stop": sum(r["first"] == "stop" for r in rs) / len(rs)}
    return out


def summarise(rows: list[dict]) -> dict:
    out = {"n": len(rows), "entry": {}, "first": {}, "holds": {}, "excess": {}, "mfe": {}, "sheet_vs_real": {}, "months": {}}
    for k in ("当天按表价", "第二天开盘"):
        out["entry"][k] = sum(r["how"] == k for r in rows)
    for k in ("target", "stop", "neither"):
        out["first"][k] = sum(r["first"] == k for r in rows) / len(rows) if rows else None
    for h in HOLDS:
        xs = [(r["day"], r["res"][f"c{h}"]) for r in rows if r["res"].get(f"c{h}") is not None]
        out["holds"][f"c{h}"] = robust.summary(xs, extra_cost=COST) if xs else None
        for name in ("SMH", "SPY"):
            ex = [(r["day"], r["res"][f"c{h}"] - r["bench"][name][f"c{h}"]) for r in rows
                  if r["res"].get(f"c{h}") is not None and r["bench"].get(name, {}).get(f"c{h}") is not None]
            out["excess"][f"{name}|c{h}"] = robust.summary(ex, extra_cost=COST) if ex else None
    lad = [(r["day"], r["ladder"]["ret"]) for r in rows if r.get("ladder")]
    out["ladder"] = robust.summary(lad, extra_cost=COST) if lad else None
    if lad:
        out["ladder_detail"] = {"days": statistics.mean(r["ladder"]["days"] for r in rows if r.get("ladder")),
                                "levels": statistics.mean(r["ladder"]["levels"] for r in rows if r.get("ladder")),
                                "stopped_at_loss": sum(r["ladder"]["levels"] == 0 and r["ladder"]["ret"] < 0 for r in rows if r.get("ladder")) / len(lad)}
    mfe = [r["mfe10"] for r in rows if r["mfe10"] is not None]
    if mfe:
        out["mfe"] = {"mean": statistics.mean(mfe), "median": statistics.median(mfe), "ge5": sum(x >= 0.05 for x in mfe) / len(mfe),
                      "ge10": sum(x >= 0.10 for x in mfe) / len(mfe), "mae_mean": statistics.mean(r["mae10"] for r in rows if r["mae10"] is not None)}
    pairs = [(r["sheet_tp"], r["mfe10"]) for r in rows if r["sheet_tp"] is not None and r["mfe10"] is not None]
    if pairs:
        out["sheet_vs_real"] = {"sheet_mean": statistics.mean(p[0] for p in pairs), "real_mfe_mean": statistics.mean(p[1] for p in pairs),
                                "sheet_wins": sum(p[0] > 0 for p in pairs) / len(pairs), "real_touch5": sum(p[1] >= 0.05 for p in pairs) / len(pairs)}
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["res"].get("c10") is not None:
            by.setdefault(r["day"][:7], []).append(r["res"]["c10"])
    out["months"] = {m: (statistics.mean(v), len(v), sum(x > 0 for x in v) / len(v)) for m, v in sorted(by.items())}
    return out


def pct(x) -> str:
    return "—" if x is None else f"{x:+.1%}"


def markdown(s: dict, meta: dict, momo: list[float]) -> str:
    out = [f"# 信号群记录的真实回放 {meta['generated']}", "",
           f"claude-c 放进 exchange 的「{meta['title']}」：{meta['signals']} 条信号（{meta['first']} 到 {meta['last']}），表自己的口径是"
           f"「到达的最高止盈区间」，平均 {pct(meta['sheet_mean'])}、{meta['sheet_wins']:.0%} 到过第一档；表也写明这是虚高。这里用拆股调整的 SIP 日线回放：当天在表价"
           f"成交得到就按表价进，否则第二天开盘；买卖合计 0.1% 成本；同一窗口减去 SMH / SPY 看超额。能对上日线的 {s['n']} 条。只报汇总，信号本身不转载。只研究，不改交易。", "",
           "## 进场和 10 天内先碰到哪边", "",
           f"- 进场：当天按表价 {s['entry'].get('当天按表价', 0)} 条，第二天开盘 {s['entry'].get('第二天开盘', 0)} 条。",
           f"- 10 个交易日内先到 +5% 的 {pct(s['first'].get('target'))}，先到 -6.3%（表的平均止损）的 {pct(s['first'].get('stop'))}，都没到的 {pct(s['first'].get('neither'))}。",
           f"- 10 天内最高点平均 {pct(s['mfe'].get('mean'))}、中位 {pct(s['mfe'].get('median'))}，到过 +5% 的 {pct(s['mfe'].get('ge5'))}、+10% 的 {pct(s['mfe'].get('ge10'))}；最低点平均 {pct(s['mfe'].get('mae_mean'))}。",
           f"- 表的「最高 TP」平均 {pct(s['sheet_vs_real'].get('sheet_mean'))} 对真实 10 天最高点 {pct(s['sheet_vs_real'].get('real_mfe_mean'))}；表的赢 {pct(s['sheet_vs_real'].get('sheet_wins'))} 对真实到过 +5% {pct(s['sheet_vs_real'].get('real_touch5'))}。", "",
           "## 拿到收盘（这才是账户会拿到的）", ""]
    out.append(robust.table({f"第 {h} 天收盘卖": s["holds"].get(f"c{h}") for h in HOLDS}, "按天数拿"))
    out.append("")
    out.append(robust.table({f"减 {name} 同期，第 {h} 天": s["excess"].get(f"{name}|c{h}") for name in ("SMH", "SPY") for h in (5, 10, 20)}, "超额（减去 SMH / SPY 同一窗口）"))
    if s.get("ladder"):
        d = s.get("ladder_detail", {})
        out += ["", "## 按群自己的玩法出场（阶梯分批 + 保本止损）", "",
                f"卡片的典型阶梯：入场上方 +1.2% / +2.5% / +4% / +6% / +10% 各出五分之一，止损 -6%（约 1 个 ATR），碰到第一档后止损移到成本价，20 个交易日到期按收盘出；"
                f"同一天里离开盘价近的那边先成交。平均持有 {d.get('days', 0):.1f} 天，平均到 {d.get('levels', 0):.1f} 档，{d.get('stopped_at_loss', 0):.0%} 的信号一档没到就止损。", ""]
        out.append(robust.table({"阶梯分批 + 保本止损": s["ladder"]}, "群的玩法，真实日线"))
    out += ["", "## 按月（第 10 天收盘，平均 / 条数 / 赚钱比例）", "", "| 月 | 平均 | 条数 | 赚钱比例 |", "|---|---|---|---|"]
    out += [f"| {m} | {pct(v[0])} | {v[1]} | {v[2]:.0%} |" for m, v in s["months"].items()]
    if s.get("setups"):
        out += ["", "## 形态：发出信号时图上是什么样，之后 10 天真实拿到多少", "",
                "| 形态 | 条数 | 第 10 天收盘 | 赚钱比例 | 减 SMH 同期 | 10 天内先到 +5% | 先到 -6.3% |", "|---|---|---|---|---|---|---|"]
        for name, g in s["setups"].items():
            out.append(f"| {name} | {g['n']} | {pct(g['c10'])} | {g['win']:.0%} | {pct(g['ex_smh'])} | {g['touch5']:.0%} | {g['stop']:.0%} |")
    if momo:
        out += ["", "## 风格：信号前 20 天这只股票走了多少", "",
                f"中位 {statistics.median(momo):+.1%}，平均 {statistics.mean(momo):+.1%}；涨了的占 {sum(x > 0 for x in momo) / len(momo):.0%}，涨 10% 以上的占 {sum(x > 0.10 for x in momo) / len(momo):.0%}——"
                "正数多就是追强势股（动能），负数多就是抄底。"]
    return "\n".join(out) + "\n"


def run(client, sheet: Path) -> tuple[dict, dict, list[float]]:
    sigs = parse(sheet)
    syms = sorted({x["sym"] for x in sigs})
    start = (date.fromisoformat(min(x["day"] for x in sigs)) - timedelta(days=45)).isoformat()
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 100):
        bars.update(client.daily_bars(syms[i:i + 100], start, adjustment="split", feed="sip"))
    bench = client.daily_bars(["SMH", "SPY"], start, adjustment="split", feed="sip")
    rows, momo = [], []
    for sg in sigs:
        bs = bars.get(sg["sym"]) or []
        r = replay(sg, bs, bench)
        if r:
            r["setup"] = setup(sg["day"], bs)
            rows.append(r)
            m = momentum_at(sg["day"], bs)
            if m is not None:
                momo.append(m)
    tps = [x["sheet_tp"] for x in sigs if x["sheet_tp"] is not None]
    meta = {"generated": date.today().isoformat(), "title": sheet.stem, "signals": len(sigs), "first": min(x["day"] for x in sigs),
            "last": max(x["day"] for x in sigs), "sheet_mean": statistics.mean(tps) if tps else None,
            "sheet_wins": sum(x > 0 for x in tps) / len(tps) if tps else 0.0, "replayed": len(rows)}
    s = summarise(rows)
    s["setups"] = buckets(rows)
    return meta, s, momo


def main() -> int:
    from hero.alpaca import Alpaca
    meta, s, momo = run(Alpaca(), peer_sheet())
    md = markdown(s, meta, momo)
    out = ROOT / "research" / f"{meta['generated']}-peer-signals"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps({"meta": meta, "summary": s, "momentum_before": momo}, ensure_ascii=False, indent=1, default=str))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
