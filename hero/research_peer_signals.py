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
    out += ["", "## 按月（第 10 天收盘，平均 / 条数 / 赚钱比例）", "", "| 月 | 平均 | 条数 | 赚钱比例 |", "|---|---|---|---|"]
    out += [f"| {m} | {pct(v[0])} | {v[1]} | {v[2]:.0%} |" for m, v in s["months"].items()]
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
            rows.append(r)
            m = momentum_at(sg["day"], bs)
            if m is not None:
                momo.append(m)
    tps = [x["sheet_tp"] for x in sigs if x["sheet_tp"] is not None]
    meta = {"generated": date.today().isoformat(), "title": sheet.stem, "signals": len(sigs), "first": min(x["day"] for x in sigs),
            "last": max(x["day"] for x in sigs), "sheet_mean": statistics.mean(tps) if tps else None,
            "sheet_wins": sum(x > 0 for x in tps) / len(tps) if tps else 0.0, "replayed": len(rows)}
    return meta, summarise(rows), momo


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
