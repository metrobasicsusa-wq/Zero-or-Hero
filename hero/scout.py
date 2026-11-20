"""Our own scout (owner, 11-20: 「我们自研究个 agent 吧」; asked for both jobs, a plan ledger, several structures side by side).

After each close it does by rule what was done by hand on 11-20:

1. Where things stand: the indexes, the sector funds and the nine names we always look at -- above or below the 50-day
   average, how far from the one-year high, falling alone or with the market, which sector is the centre of the fall --
   and beside each fund what followed the same state since 1999 (shares, dividends included): three months and one year on.
2. Is today a big drop day?  Fixed before any result: SPY down 1.5% or more, or QQQ down 2% or more, or any sector fund
   down 3% or more, close to close.
3. Only on a big drop day, a list.  The lines, fixed too: 25% or more under the one-year high; the last earnings record
   says guidance above consensus (hero.earnings_reasons); not 10 points or more worse than its own group over ten sessions
   (that reads as its own trouble, not the panic); $300M a day traded; no report within five sessions.  Each name is put
   with the fund it has moved with lately (its returns over SPY's against each fund's); a group keeps the two names most
   like it (their fall is the group's, not their own): three names from one group are one bet.

Steps 4 and 5 (real option quotes for three structures side by side, and a plan ledger scored against the same
structures on SPY) run in the workflow, where the broker's key is; they are in hero.scout_ledger.

Only a record: it sends no orders and changes no rule.  The lines above are not to be tuned after looking at results.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "scout"
INDEXES = {"SPY": "标普 500", "QQQ": "纳指 100", "IWM": "小盘股"}
SECTORS = {"XLK": "科技", "SMH": "芯片", "IGV": "软件", "XLC": "通信和互联网", "XLY": "可选消费", "XLP": "必需消费", "XLF": "金融", "KRE": "地区银行",
           "XLV": "医疗", "XBI": "生物科技", "XLI": "工业", "XLE": "能源", "XLU": "公用事业", "XLB": "原材料"}
FUNDS = {**INDEXES, **SECTORS}
FAMILY = {"SMH": "XLK", "KRE": "XLF"}          # funds that are one bet: chips with technology, regional banks with financials
FAMILY_NAMES = {"XLK": "科技和芯片", "XLF": "金融"}
WATCH = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")
DROP = {"SPY": -0.015, "QQQ": -0.02}           # an index this far down at the close makes a big drop day
DROP_SECTOR = -0.03                            # or any sector fund this far down
DEEP, WORSE, TRADED, SOON, PER_GROUP = -0.25, -0.10, 300e6, 5, 2
MOVES_DAYS, MOVES_MIN = 120, 0.30
BEARS = (("2000-09", "2002-10"), ("2007-11", "2009-02"), ("2022-01", "2022-12"))
ENVS = {"up": "上升", "rebound": "反弹中", "pullback": "回调", "down": "下跌"}


# ---------- prices ----------
def fetch(sym: str, since: str = "1999-01-01") -> list[tuple[str, float, float, float]]:
    """(day, close, close with dividends put back, shares traded) from Yahoo; days without a price are left out."""
    start = int(datetime.fromisoformat(since).replace(tzinfo=ET).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('.', '-')}?interval=1d"
           f"&period1={start}&period2={int(time.time()) + 86400}")
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=40))["chart"]["result"][0]
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    q, adj = r["indicators"]["quote"][0], (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c:
            a = adj[i] if adj and adj[i] else c
            out.append((datetime.fromtimestamp(t, ET).strftime("%Y-%m-%d"), float(c), float(a), float(q["volume"][i] or 0)))
    return out


def state(closes: list[float], i: int) -> dict | None:
    """Where a series stands on day i: against its 50-day average and its one-year high (252 sessions)."""
    if i < 251:
        return None
    above = closes[i] >= statistics.mean(closes[i - 49:i + 1])
    dd = closes[i] / max(closes[i - 251:i + 1]) - 1
    far = dd <= -0.10
    return {"above50": above, "dd": dd, "env": ("rebound" if far else "up") if above else ("down" if far else "pullback")}


def in_bear(day: str) -> bool:
    return any(a <= day[:7] <= b for a, b in BEARS)


# ---------- what followed the same state before ----------
def stretches(days: list[str], gap: int = 45) -> int:
    """How many separate stretches a set of days makes (a new one after `gap` calendar days without any)."""
    n, prev = 0, None
    for d in sorted(days):
        if prev is None or (date.fromisoformat(d) - date.fromisoformat(prev)).days > gap:
            n += 1
        prev = d
    return n


def summary(xs: list[float]) -> dict | None:
    if len(xs) < 20:
        return None
    v = sorted(xs)
    return {"n": len(v), "mean": statistics.mean(v), "up": sum(x > 0 for x in v) / len(v), "bad": v[len(v) // 20]}


def past(series: list[tuple[str, float]], market: list[tuple[str, float]]) -> list[dict]:
    """Every day of a fund's history with its state, whether the market itself was in a decline that day, and what the
    fund did over the next 63 and 252 sessions (None where the future is not in yet)."""
    closes = [c for _, c in series]
    mkt = {d: i for i, (d, _) in enumerate(market)}
    mcl = [c for _, c in market]
    out = []
    for i, (d, _) in enumerate(series):
        s, j = state(closes, i), mkt.get(d)
        m = state(mcl, j) if j is not None else None
        if not s or not m:
            continue
        out.append({"day": d, **s, "market_down": m["env"] == "down",
                    "m3": closes[i + 63] / closes[i] - 1 if i + 63 < len(closes) else None,
                    "y1": closes[i + 252] / closes[i] - 1 if i + 252 < len(closes) else None})
    return out


def like_now(rows: list[dict], now: dict, market_down: bool) -> dict:
    """What followed the days that looked like today: the same environment and the same answer to "is the market in a
    decline too"; for a fund 25% or more under its high, also the deep days split into the three bear markets and the rest."""
    same = [r for r in rows if r["env"] == now["env"] and r["market_down"] == market_down]
    out = {"days": len(same), "stretches": stretches([r["day"] for r in same]),
           "m3": summary([r["m3"] for r in same if r["m3"] is not None]),
           "y1": summary([r["y1"] for r in same if r["y1"] is not None]),
           "usual_m3": summary([r["m3"] for r in rows if r["m3"] is not None])}
    if now["dd"] <= DEEP:
        deep = [r for r in rows if r["dd"] <= DEEP and r["y1"] is not None]
        out["deep_bear"] = summary([r["y1"] for r in deep if in_bear(r["day"])])
        out["deep_other"] = summary([r["y1"] for r in deep if not in_bear(r["day"])])
    return out


# ---------- the day ----------
def big_drop(moves: dict[str, float]) -> list[str]:
    """Why today is a big drop day (empty when it is not): `moves` are close-to-close returns by fund."""
    why = [f"{FUNDS[s]} {moves[s]:+.1%}" for s, line in DROP.items() if s in moves and moves[s] <= line]
    why += [f"{SECTORS[s]} {moves[s]:+.1%}" for s in SECTORS if s in moves and moves[s] <= DROP_SECTOR]
    return why


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.mean(a), statistics.mean(b)
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / den if den else 0.0


def moves_with(stock: list[float], funds: dict[str, list[float]], spy: list[float]) -> tuple[str | None, float]:
    """The sector fund a stock has moved with: its daily returns over SPY's against each fund's over SPY's. None when
    no fund reaches MOVES_MIN -- it goes its own way."""
    own = [x - m for x, m in zip(stock, spy)]
    best, top = None, MOVES_MIN
    for f, rets in funds.items():
        c = corr(own, [x - m for x, m in zip(rets, spy)])
        if c >= top:
            best, top = f, c
    return best, (top if best else 0.0)


def screen(names: dict[str, dict], records: dict[str, dict], reports: dict[str, str], today: str) -> tuple[list[dict], list[dict]]:
    """(kept, left out with the reason).  `names`: symbol -> {"dd", "traded", "ten", "group", "group_ten", ...};
    `records`: the latest earnings line per symbol; `reports`: symbol -> next report day."""
    kept, out = [], []
    soon = (date.fromisoformat(today) + timedelta(days=SOON + 2)).isoformat()      # five sessions is about a week
    for s, n in names.items():
        rec = records.get(s)
        why = None
        if n["dd"] > DEEP:
            continue                                                               # not deep: not a candidate, not a note
        if not rec:
            why = "没有上次财报的记录"
        elif rec.get("guide") == "cut" or rec.get("guide_vs_est") == "below":
            why = "上次指引下调或低于预期"
        elif rec.get("guide_vs_est") != "above":
            why = "上次指引没有高于预期"
        elif n["traded"] < TRADED:
            why = f"成交太小（一天约 ${n['traded'] / 1e6:,.0f} 百万）"
        elif n.get("group") and n["ten"] - n["group_ten"] <= WORSE:
            why = f"十天里比同组多跌 10% 以上，像是有它自己的事（多跌 {n['group_ten'] - n['ten']:.0%}）"
        elif today <= reports.get(s, "") <= soon:
            why = f"{reports[s][5:]} 发财报"
        (out if why else kept).append({"symbol": s, **n, "why": why, "record": rec})
    groups: dict[str, list[dict]] = {}
    final = []
    for c in sorted(kept, key=lambda c: -c["with"]):                               # the two most like their group: its fall is the group's
        if c.get("group"):
            groups.setdefault(family(c["group"]), []).append(c)
        else:
            final.append(c)                                                        # names that go their own way are not one bet
    for g, cs in groups.items():
        final += cs[:PER_GROUP]
        out += [{**c, "why": f"同一组（{group_name(g)}）已经有两只更像这一组的"} for c in cs[PER_GROUP:]]
    return sorted(final, key=lambda c: (family(c["group"]) if c.get("group") else "~", -c["with"])), out


def family(fund: str) -> str:
    return FAMILY.get(fund, fund)


def group_name(fund: str | None) -> str:
    return "自己走" if not fund else FAMILY_NAMES.get(family(fund), FUNDS[family(fund)])


# ---------- the page ----------
def pct(x, digits=0) -> str:
    return "—" if x is None else f"{x:+.{digits}%}"


def after(s: dict | None) -> str:
    return "样本不够" if not s else f"{pct(s['mean'], 1)}，涨 {s['up']:.0%}，最差的 5% {pct(s['bad'])}"


def page(day: str, funds: dict[str, dict], watch: dict[str, dict], drop: list[str], kept: list[dict], left: list[dict], centre: str) -> str:
    out = [f"# 侦察：{day}", "",
           "我们自己的 agent 每个交易日 15:40（美东）按写死的线跑一遍（`hero/scout.py`）；「今天」是那一刻的涨跌，不是收盘价。只记录，不下单，不是投资建议。", "",
           "## 一、现在各在哪", "",
           f"震中（离一年高点最远的板块）：**{FUNDS[centre]}**，{pct(funds[centre]['dd'])}。", "",
           "| | 今天 | 环境 | 离高点 | 大盘也在下跌吗 | 1999 年以来同样的时候：3 个月后 | 1 年后 | 几段行情 |", "|---|---|---|---|---|---|---|---|"]
    for f, v in funds.items():
        h = v["like"]
        out.append(f"| {FUNDS[f]}（{f}） | {pct(v['move'], 1)} | {ENVS[v['env']]} | {pct(v['dd'])} | {'是' if v['market_down'] else '否'} | "
                   f"{after(h['m3'])} | {after(h['y1'])} | {h['stretches']} |")
    deep = [(f, v) for f, v in funds.items() if "deep_bear" in v["like"]]
    if deep:
        out += ["", "离高点 25% 以上的板块，再多看一刀——同样跌到这么深的时候进场，一年后（熊市 = 2000-09 到 2002-10、2007-11 到 2009-02、2022 年）：", "",
                "| | 三次熊市里 | 其余时候 |", "|---|---|---|"]
        out += [f"| {FUNDS[f]} | {after(v['like']['deep_bear'])} | {after(v['like']['deep_other'])} |" for f, v in deep]
    out += ["", "常看的几只：", "", "| | 今天 | 环境 | 离高点 | 最近跟着谁走 |", "|---|---|---|---|---|"]
    out += [f"| {s} | {pct(v['move'], 1)} | {ENVS[v['env']]} | {pct(v['dd'])} | {FUNDS.get(v['group'], '自己走')}（{v['with']:.2f}） |" for s, v in watch.items()]
    out += ["", "## 二、今天是不是大跌日", "",
            ("**是**：" + "；".join(drop) + "。" if drop else "**不是**。线：标普收跌 1.5% 以上，或纳指 100 收跌 2% 以上，或任何一个行业基金收跌 3% 以上。今天没有清单。")]
    if drop:
        out += ["", "## 三、清单", "",
                "五条线：离高点 25% 以上；上次指引高于预期；十天里没有比同组多跌 10% 以上；一天成交 $3 亿以上；一周内不发财报。同一组只留最像这一组的两只。", ""]
        if kept:
            out += ["| 标的 | 今天 | 离高点 | 跟着谁走 | 是不是震中 | 上次财报 |", "|---|---|---|---|---|---|"]
            for c in kept:
                rec = c["record"]
                out.append(f"| {c['symbol']} | {pct(c['move'], 1)} | {pct(c['dd'])} | {group_name(c.get('group'))} | {'是' if c.get('group') and family(c['group']) == family(centre) else '否'} | "
                           f"{rec['date'][5:]} {'、'.join((rec.get('tags') or [])[:2])} |")
        else:
            out.append("今天没有一只过五条线。")
        reasons: dict[str, list[str]] = {}
        for c in left:
            head = c["why"].split("（")[0]
            reasons.setdefault("一周内发财报" if c["why"].endswith("发财报") else head, []).append(c["symbol"])
        if reasons:
            out += ["", "跌得够深但没放进去的："] + [f"- {why}：{'、'.join(sorted(syms)[:14])}{' 等' if len(syms) > 14 else ''}（{len(syms)} 只）" for why, syms in reasons.items()]
    out += ["", "## 说明", "",
            "- 「同样的时候」= 同一种环境（在不在 50 日均线上方、离一年高点是否 10% 以上），并且大盘那天是不是也在下跌环境。每天一个样本、前后重叠，所以另列了「几段行情」。",
            "- 行业基金是一篮子，个股的尾巴更长；大熊市只有三次。通信和互联网那只基金 2018 年才有，它那一行的历史很短。",
            "- 清单是按线筛出来的，不看增速在不在放缓这类要读财报才知道的事；上次财报的标签列在旁边，自己再看一眼。",
            "- 「跟着谁走」是最近 120 天扣掉标普以后和哪个行业基金最像，不是官方的行业分类。",
            "- 三种做法的真实报价和演算账本在 `research/scout/` 的账本页（工作流里跑）。"]
    return "\n".join(out) + "\n"


def run(today: str | None = None, sleep=time.sleep) -> dict:
    data = {}
    for f in FUNDS:
        data[f] = fetch(f)
        sleep(0.15)
    today = today or data["SPY"][-1][0]
    spy_adj = [(d, a) for d, _, a, _ in data["SPY"] if d <= today]
    spy_now = state([a for _, a in spy_adj], len(spy_adj) - 1)
    funds = {}
    for f in FUNDS:
        bars = [b for b in data[f] if b[0] <= today]
        adj = [(d, a) for d, _, a, _ in bars]
        now = state([a for _, a in adj], len(adj) - 1)
        if not now:
            continue
        funds[f] = {**now, "move": bars[-1][2] / bars[-2][2] - 1, "market_down": spy_now["env"] == "down",
                    "like": like_now(past(adj, spy_adj), now, spy_now["env"] == "down")}
    centre = min(SECTORS, key=lambda f: funds[f]["dd"] if f in funds else 0)
    drop = big_drop({f: v["move"] for f, v in funds.items()})

    def tail(sym, n):
        rows = [b for b in data[sym] if b[0] <= today][-(n + 1):]
        return [rows[i][2] / rows[i - 1][2] - 1 for i in range(1, len(rows))]
    spy_r = tail("SPY", MOVES_DAYS)
    fund_r = {f: tail(f, MOVES_DAYS) for f in SECTORS}
    records = {}
    for line in (ROOT / "research" / "earnings-forecast" / "reasons.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["date"] >= (date.fromisoformat(today) - timedelta(days=100)).isoformat():
            records[r["symbol"]] = r
    cal = json.loads((ROOT / "data" / "earnings.json").read_text()).get("reports") or {}
    reports = {s: min((r["date"] for r in rs if r.get("date", "") >= today), default="") for s, rs in cal.items()}
    pool = json.loads((ROOT / "data" / "universe.json").read_text())["stocks"]
    wanted = sorted(set(WATCH) | ((set(pool) | {s for s, r in records.items() if r.get("guide_vs_est") == "above"}) if drop else set()))
    names = {}
    for s in wanted:
        if s in FUNDS:
            continue
        try:
            bars = [b for b in fetch(s, (date.fromisoformat(today) - timedelta(days=760)).isoformat()) if b[0] <= today]
        except Exception:
            continue
        sleep(0.12)
        adj = [a for _, _, a, _ in bars]
        now = state(adj, len(adj) - 1)
        if not now or len(adj) < MOVES_DAYS + 12:
            continue
        rets = [adj[i] / adj[i - 1] - 1 for i in range(len(adj) - MOVES_DAYS, len(adj))]
        group, c = moves_with(rets, fund_r, spy_r) if len(rets) == len(spy_r) else (None, 0.0)
        gbars = [b for b in data[group] if b[0] <= today] if group else None
        names[s] = {**now, "move": adj[-1] / adj[-2] - 1, "price": bars[-1][1], "ten": adj[-1] / adj[-11] - 1,
                    "traded": statistics.mean(b[1] * b[3] for b in bars[-20:]), "group": group, "with": c,
                    "group_ten": gbars[-1][2] / gbars[-11][2] - 1 if gbars else 0.0}
    kept, left = screen({s: v for s, v in names.items()}, records, reports, today) if drop else ([], [])
    watch = {s: names[s] for s in WATCH if s in names}
    md = page(today, funds, watch, drop, kept, left, centre)
    OUT.mkdir(parents=True, exist_ok=True)
    rep = {"day": today, "centre": centre, "drop": drop, "funds": funds, "watch": watch,
           "kept": [{k: v for k, v in c.items() if k != "record"} | {"record_date": c["record"]["date"]} for c in kept],
           "left": [{"symbol": c["symbol"], "why": c["why"]} for c in left]}
    (OUT / f"{today}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (OUT / f"{today}.md").write_text(md)
    return rep


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
    print((OUT / sorted(p.name for p in OUT.glob("*.md"))[-1]).read_text())
