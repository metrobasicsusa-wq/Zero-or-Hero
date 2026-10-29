"""Chip / AI-hardware earnings beats on a wider list (owner, 10-29: 「跑吧 扩大版回测」).

research/2026-10-28-earnings-overshoot.md found one clear after-the-report pattern: a chip or AI-hardware stock that
closed its reaction day up at least 1.5x its options' implied move beat SPY by +9% over the next 20 sessions (30
reports, 70%, every year positive), and the option version (research/2026-10-28-beat-drift.md, 11 trades) a median
+83%. Those 30 came from the 29 names of our hand-made chip and AI-hardware groups. This rebuilds the events for
about 80 US-listed chip, equipment, networking / optical, storage, server, contract-manufacturing and data-center
power names from SEC 8-K item 2.02 filings since 2024-02 (when Alpaca's option history starts), prices each report's
at-the-money straddle on the close before (research_earnings.straddle), and repeats both layers:
  stock:   the reaction day's close to 1 / 5 / 10 / 20 / 60 sessions later, minus SPY (Alpaca daily bars, adjusted);
  options: the next session from 10:00 (the first trade before 11:00) a 25-50 day call at the money or 5% out, the
           monthly expiry when listed; out after 10 / 20 sessions, a 2x take or a 2x-then-40%-giveback trail on daily
           option bars; 10% paid over / taken under (research_beat_drift, research_ideas).
What the earlier study could not see is reported apart: the names outside the original groups, and every report
after 2026-10-02 (out of sample). Foreign issuers (TSM, ASML, ARM, NVMI, CAMT, GFS) furnish 6-Ks with no item codes,
so their earnings 6-K is recognised by its press release: a title like "<company> reports ... quarter ... results /
EPS / net sales" in the filing or in an exhibit whose name says press release / earnings / results, excluding
pre-announcements, monthly revenue reports and "to announce" notices (owner, 10-29: 「TSM 为什么不可以」).
Survivorship: today's listings. Record only. Run: python -m hero.research_chip_beat
"""

from __future__ import annotations

import html
import re
import statistics
import sys
import time
from datetime import date, timedelta

from hero import robust
from hero.research_earnings_rules import GROUPS

START = "2024-02-01"
ORIGINAL_END = "2026-10-02"   # the earlier study's events end here
CORE = sorted(set((GROUPS["芯片"] + " " + GROUPS["AI 硬件 / 算力"]).split()))
EXTRA = {
    "半导体（扩展）": "TER NXPI ON MCHP MPWR ENTG ONTO LSCC SWKS QRVO FORM ACLS UCTT ICHR KLIC AMKR WOLF SITM RMBS CRUS SYNA "
                    "POWI VSH DIOD AOSL SLAB MTSI SMTC AMBA PI NVTS MXL ALGM VECO COHU PLAB AEHR NOVT MKSI CAMT NVMI TSM ASML ARM GFS",
    "网络 / 光通信 / 存储 / 服务器 / 代工（扩展）": "CLS FLEX JBL SANM TTMI CIEN VIAV AAOI CALX EXTR NTAP PSTG",
    "数据中心电力 / 散热（扩展）": "NVT MOD POWL",
}
EXTRA_GROUP = {s: g for g, ss in EXTRA.items() for s in ss.split()}
NAMES = sorted(set(CORE) | set(EXTRA_GROUP))
FOREIGN = ("TSM", "ASML", "ARM", "NVMI", "CAMT", "GFS")
HORIZONS = (1, 5, 10, 20, 60)
_HIT = re.compile(r"\b(reports?|reported|announce[sd]?)\b(?:[^.\n]|\.(?=\d)){0,160}?\b(results|EPS|net sales|net income|revenue)\b", re.I)
_PERIOD = re.compile(r"\b(quarter|Q[1-4]|fiscal|full year)\b", re.I)
_NOT = re.compile(r"\b(pre-?announce\w*|preliminary|to (announce|report|release|host)|will (announce|report|release|host)|invitation|"
                  r"monthly revenue|revenue report|(january|february|march|april|may|june|july|august|september|october|november|"
                  r"december) (\d{4} )?revenue)\b", re.I)
_EXHIBIT = re.compile(r"press|release|earn|result|ex-?99|exhibit", re.I)


def page_text(raw: bytes, limit: int = 80000) -> str:
    t = re.sub(rb"<[^>]+>", b" ", raw[:limit]).decode("utf-8", "ignore")
    return re.sub(r"\s+", " ", html.unescape(t))


def earnings_title(text: str) -> str | None:
    """The press-release headline of a quarterly earnings report, or None (pre-announcements, monthly revenue,
    'to announce' notices and product news fail)."""
    head = text[:8000]
    if re.search(r"pre-?announc", head[:3000], re.I):
        return None
    for m in _HIT.finditer(head):
        span = head[max(0, m.start() - 120):m.end() + 120]
        if _PERIOD.search(span) and not _NOT.search(span):
            return span
    return None


def exhibit_order(names: list[str], primary: str) -> list[str]:
    """A 6-K's other HTML documents, press-release-looking names first (no XBRL R pages)."""
    docs = [n for n in names if n.lower().endswith((".htm", ".html")) and n != primary and "index" not in n.lower()
            and not re.match(r"R\d+\.htm$", n)]
    return sorted(docs, key=lambda n: (not _EXHIBIT.search(n), n))[:3]


def releases_6k(cik: int, since: str) -> list:
    """Acceptance times (ET) of a foreign issuer's earnings 6-Ks."""
    import requests
    from datetime import datetime
    from hero import research_earnings as re_
    data = re_._sec(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    blocks = [data["filings"]["recent"]] + [re_._sec(f"https://data.sec.gov/submissions/{f['name']}")
                                            for f in data["filings"].get("files", []) if f.get("filingTo", "9999") >= since]

    def raw(url):
        r = requests.get(url, headers=re_.SEC_HEADERS, timeout=60)
        r.raise_for_status()
        time.sleep(0.12)
        return r.content
    out = []
    for b in blocks:
        for form, filed, accepted, acc, doc in zip(b["form"], b["filingDate"], b["acceptanceDateTime"], b["accessionNumber"],
                                                   b["primaryDocument"]):
            if form != "6-K" or filed < since:
                continue
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
            try:
                found = earnings_title(page_text(raw(base + doc)))
                if not found:
                    items = [x["name"] for x in re_._sec(base + "index.json")["directory"]["item"]]
                    for n in exhibit_order(items, doc):
                        found = earnings_title(page_text(raw(base + n)))
                        if found:
                            break
            except Exception:
                continue
            if found:
                out.append(datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(re_.ET))
    return spaced(sorted(set(out)))


def spaced(times: list, days: int = 40) -> list:
    """Quarterly reports are ~3 months apart: drop a release within `days` of the last one kept (a second press
    release the same day, or other news that reads like results)."""
    kept = []
    for t in sorted(times):
        if not kept or (t - kept[-1]).days >= days:
            kept.append(t)
    return kept
STOCK_COST = 0.001
BEAT = 1.5


def build_events(sym: str, bars: list[dict], rawc: dict[str, float], rel: list, straddle_fn, start: str = START) -> list[dict]:
    """One row per 8-K earnings release reacting on or after `start`: reaction day, close-to-close move, gap, the
    straddle priced on the close before (via `straddle_fn(spot, on, react)`), and the move over the implied move."""
    from hero.research_earnings import reaction_index
    dates = [b["t"][:10] for b in bars]
    closes = [float(b["c"]) for b in bars]
    out = []
    for t in rel:
        i = reaction_index(dates, t)
        if i is None or i < 1 or dates[i] < start:
            continue
        ev = {"symbol": sym, "accepted": t.isoformat(timespec="minutes"), "reaction_day": dates[i],
              "move": round(closes[i] / closes[i - 1] - 1, 4), "gap": round(float(bars[i]["o"]) / closes[i - 1] - 1, 4),
              "group": "核心（原来的芯片 / AI 硬件组）" if sym in CORE else EXTRA_GROUP.get(sym, "其他")}
        prev = dates[i - 1]
        if prev in rawc:
            st = straddle_fn(rawc[prev], prev, dates[i])
            if st and st.get("implied_move"):
                ev.update(st)
                ev["ratio"] = round(ev["move"] / st["implied_move"], 2)   # signed: + up, - down
        out.append(ev)
    return out


def is_new(e: dict) -> bool:
    """Not seen by the 10-28 study: a name outside its groups, or a report after its events end."""
    return e["symbol"] not in CORE or e["reaction_day"] > ORIGINAL_END


def stock_groups(fw: list[dict]) -> dict[str, list[dict]]:
    up = lambda lo, hi=99.0: (lambda r: r["ratio"] is not None and lo <= r["ratio"] < hi)
    g = {}
    for scope, keep in (("全部", lambda r: True), ("新样本（扩展名单 + 10-02 之后）", is_new),
                        ("核心名单", lambda r: r["symbol"] in CORE), ("扩展名单", lambda r: r["symbol"] not in CORE)):
        rs = [r for r in fw if keep(r)]
        g[f"{scope} · 涨超定价 1.5 倍"] = [r for r in rs if up(BEAT)(r)]
        g[f"{scope} · 涨超定价 2 倍"] = [r for r in rs if up(2.0)(r)]
        g[f"{scope} · 对照：涨 1–1.5 倍"] = [r for r in rs if up(1.0, BEAT)(r)]
        g[f"{scope} · 对照：涨了但没超过定价"] = [r for r in rs if up(0.0, 1.0)(r)]
        g[f"{scope} · 对照：跌超定价 1.5 倍"] = [r for r in rs if r["ratio"] is not None and r["ratio"] <= -BEAT]
    for name in EXTRA:
        g[f"{name} · 涨超定价 1.5 倍"] = [r for r in fw if r["group"] == name and up(BEAT)(r)]
    return g


def forward(events: list[dict], bars: dict[str, dict[str, float]], spy: dict[str, float]) -> list[dict]:
    """Each event with its return from the reaction day's close h sessions on, raw and minus SPY."""
    sd = sorted(spy)
    sidx = {d: i for i, d in enumerate(sd)}
    out = []
    for e in events:
        b = bars.get(e["symbol"]) or {}
        days = sorted(b)
        idx = {d: i for i, d in enumerate(days)}
        i, j = idx.get(e["reaction_day"]), sidx.get(e["reaction_day"])
        if i is None or j is None:
            continue
        row = {**e, "fwd": {}, "exc": {}}
        for h in HORIZONS:
            if i + h < len(days) and j + h < len(sd):
                r = b[days[i + h]] / b[days[i]] - 1
                row["fwd"][h] = r
                row["exc"][h] = r - (spy[sd[j + h]] / spy[sd[j]] - 1)
        out.append(row)
    return out


def stock_table(fw: list[dict], end: str) -> tuple[dict, str]:
    g = stock_groups(fw)
    md = ["| 情况 | 次数 | 第 5 天（减 SPY） | 第 20 天：平均 | 中位数 | 跑赢 SPY 的比例 | 第 60 天：平均 | 中位数 |",
          "|---|---|---|---|---|---|---|---|"]
    tab, rob = {}, {}
    for name, rs in g.items():
        x20 = [r["exc"][20] for r in rs if 20 in r["exc"]]
        if len(x20) < 3:
            md.append(f"| {name} | {len(rs)} | — | — | — | — | — | — |")
            continue
        x5 = [r["exc"][5] for r in rs if 5 in r["exc"]]
        x60 = [r["exc"][60] for r in rs if 60 in r["exc"]]
        row = {"n": len(x20), "d5": statistics.mean(x5) if x5 else None, "d20": statistics.mean(x20),
               "d20_med": statistics.median(x20), "win": sum(x > 0 for x in x20) / len(x20),
               "d60": statistics.mean(x60) if x60 else None, "d60_med": statistics.median(x60) if x60 else None, "n60": len(x60)}
        tab[name] = row
        f = lambda v: "—" if v is None else f"{v:+.1%}"
        md.append(f"| {name} | {row['n']} | {f(row['d5'])} | {f(row['d20'])} | {f(row['d20_med'])} | {row['win']:.0%} | "
                  f"{f(row['d60'])}（{row['n60']}） | {f(row['d60_med'])} |")
        if "1.5 倍" in name and "对照" not in name:
            rob[f"{name} · 20 天减 SPY"] = robust.summary([(r["reaction_day"], r["exc"][20]) for r in rs if 20 in r["exc"]],
                                                          end, extra_cost=STOCK_COST)
    return {"groups": tab, "robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, "稳健性检查（正股，20 天减 SPY）") + "\n"


def option_groups(rows: list[dict]) -> dict[str, list[dict]]:
    g = {}
    for leg, name in (("atm", "平值"), ("otm5", "价外 5%")):
        rs = [r for r in rows if r["leg"] == leg]
        g[f"全部 · 涨超 1.5 倍 · {name}"] = [r for r in rs if r["ratio"] >= BEAT]
        g[f"　新样本 · 涨超 1.5 倍 · {name}"] = [r for r in rs if r["ratio"] >= BEAT and is_new(r)]
        g[f"　核心名单 · 涨超 1.5 倍 · {name}"] = [r for r in rs if r["ratio"] >= BEAT and r["symbol"] in CORE]
        g[f"　扩展名单 · 涨超 1.5 倍 · {name}"] = [r for r in rs if r["ratio"] >= BEAT and r["symbol"] not in CORE]
        g[f"对照：涨 1–1.5 倍 · {name}"] = [r for r in rs if 1.0 <= r["ratio"] < BEAT]
    return g


def run(client) -> dict:
    from hero import research_earnings as re_
    from hero.research_beat_drift import entry, exits, monthly_contract
    from hero.research_ideas import dailies, minutes
    since = (date.fromisoformat(START) - timedelta(days=60)).isoformat()
    ciks = re_.cik_map()
    syms = NAMES + ["SPY"]
    adj, raw = {}, {}
    for i in range(0, len(syms), 100):
        adj.update(client.daily_bars(syms[i:i + 100], since))
        raw.update(client.daily_bars(syms[i:i + 100], since, adjustment="raw"))
    events, missing = [], []
    for s in NAMES:
        if s not in ciks or s not in adj:
            missing.append(s)
            continue
        try:
            rel = releases_6k(ciks[s], since) if s in FOREIGN else re_.releases(ciks[s], since)
        except Exception:
            missing.append(s)
            continue
        if not rel:
            missing.append(s)
            continue
        rawc = {b["t"][:10]: float(b["c"]) for b in raw.get(s, [])}

        def priced(spot, on, react, s=s):
            time.sleep(0.15)
            return re_.straddle(client, s, spot, on, react)
        events += build_events(s, adj[s], rawc, rel, priced)
    closes = {s: {b["t"][:10]: float(b["c"]) for b in bs} for s, bs in adj.items()}
    fw = forward(events, closes, closes.get("SPY", {}))
    end = (date.today() - timedelta(days=3)).isoformat()
    opts = []
    for e in events:
        if (e.get("ratio") or 0) < 1.0:
            continue
        bs = adj.get(e["symbol"], [])
        nxt = next((b for b in bs if b["t"][:10] > e["reaction_day"]), None)
        if not nxt or nxt["t"][:10] > end:
            continue
        day = nxt["t"][:10]
        spot = float(next((b for b in raw.get(e["symbol"], []) if b["t"][:10] == day), nxt)["o"])
        for leg, off in (("atm", 0.0), ("otm5", 0.05)):
            got = monthly_contract(client, e["symbol"], "C", day, spot, off)
            if not got:
                continue
            sym, exp = got
            ec = entry(minutes(client, sym, day))
            if not ec:
                continue
            res = exits(ec[1], dailies(client, sym, day, exp))
            time.sleep(0.03)
            if res:
                opts.append({"symbol": e["symbol"], "reaction_day": e["reaction_day"], "day": day, "ratio": e["ratio"],
                             "leg": leg, "sym": sym, "cost": ec[1], "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "names": len(NAMES),
            "missing": sorted(missing), "events": events, "forward": fw, "options": opts}


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_beat_drift import EXITS
    from hero.research_ideas import report, save
    rep = run(Alpaca())
    priced = [e for e in rep["events"] if e.get("ratio") is not None]
    stab, smd = stock_table(rep["forward"], rep["end"])
    otab, omd = report("期权版：第二天买 1–2 个月看涨", "同 research/2026-10-28-beat-drift.md 的做法，名单扩大。",
                       option_groups(rep["options"]), EXITS, rep["end"])
    omd = omd.split("\n", 1)[1]   # drop report()'s own title line
    beats = sorted((e for e in priced if e["ratio"] >= BEAT), key=lambda e: e["reaction_day"])
    lines = [f"| {e['reaction_day']} | {e['symbol']} | {e['group']} | {e['move']:+.1%} | {e['implied_move']:.1%} | {e['ratio']:.1f} | "
             + " | ".join(next((f"{r['exc'][h]:+.1%}" if h in r['exc'] else "—" for r in rep["forward"]
                                if r["symbol"] == e["symbol"] and r["reaction_day"] == e["reaction_day"]), "—") for h in (5, 20, 60)) + " |"
             for e in beats]
    head = [f"# 芯片 / AI 硬件财报大超之后：扩大名单 {rep['generated']}", "",
            f"{rep['names']} 只美国上市的芯片、设备、网络 / 光通信、存储、服务器、代工和数据中心电力散热公司（原研究是自己分组里的 {len(CORE)} 只），"
            f"SEC 8-K 财报公告，{START} 起（Alpaca 期权数据从这时开始）；{len(rep['events'])} 次财报，{len(priced)} 次有财报前一天收盘的平值跨式定价。"
            f"外国公司（{'、'.join(FOREIGN)}）交的是 6-K，按新闻稿标题认财报；找不到财报公告或行情的：{'、'.join(rep['missing']) or '无'}。"
            "「涨超定价 1.5 倍」= 反应日收盘涨幅 ≥ 1.5 × 期权定价。「新样本」= 原研究没见过的：扩展名单的公司，加上 10-02 之后的财报。"
            "正股从反应日收盘算，减去 SPY 同期；稳健性检查的成本按买卖各 0.1%。幸存者偏差：都是今天还在交易的公司。只研究，不改交易。", "",
            "## 正股版", "", smd, "## 每一次涨超 1.5 倍（减 SPY：第 5 / 20 / 60 天）", "",
            "| 反应日 | 股票 | 组 | 当天 | 定价 | 倍数 | 5 天 | 20 天 | 60 天 |", "|---|---|---|---|---|---|---|---|---|", *lines, "",
            "## 期权版", "", omd]
    rep["stock_table"], rep["option_table"] = stab, otab
    save("chip-beat", rep, "\n".join(head) + "\n")


if __name__ == "__main__":
    sys.exit(main())
