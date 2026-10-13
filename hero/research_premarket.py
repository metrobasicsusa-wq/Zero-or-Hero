"""Pre-market scan for the sniper sleeves: which stocks are moving before the open, and on what headlines?

Owner, 2026-10-13: scan everything instead of a fixed list. Candidates are Alpaca's screener (top movers and
most actives) plus data/universe.json; each is priced from its latest trade (IEX, extended hours) against the
prior close, kept if it traded today, costs $3+ and is 2%+ away; the 40 largest moves get the headlines published
since the prior close. Written to research/premarket/YYYY-MM-DD.md (and .json) for the 09:04 ET pick.
Record only. Run: python -m hero.research_premarket
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
MIN_MOVE = 0.02
MIN_PRICE = 3.0
TOP = 40


def screener(client) -> tuple[list[str], dict[str, float]]:
    """Symbols from Alpaca's movers / most-actives screens, with the screen's own % change where given."""
    syms, pct = [], {}
    try:
        mv = client._d("/v1beta1/screener/stocks/movers", {"top": 50}) or {}
        for side in ("gainers", "losers"):
            for x in mv.get(side) or []:
                syms.append(x["symbol"])
                pct[x["symbol"]] = float(x.get("percent_change") or 0) / 100
    except Exception:
        pass
    try:
        ma = client._d("/v1beta1/screener/stocks/most-actives", {"by": "volume", "top": 50}) or {}
        syms += [x["symbol"] for x in ma.get("most_actives") or []]
    except Exception:
        pass
    return syms, pct


def move(snap: dict, today: str) -> tuple[float, float] | None:
    """(price, change vs the prior close) when the latest trade is from today; the daily bar may still be yesterday's."""
    lt, bar, prev = snap.get("latestTrade") or {}, snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
    if not lt.get("p") or not str(lt.get("t", "")).startswith(today):
        return None
    base = prev.get("c") if str(bar.get("t", "")).startswith(today) else bar.get("c")
    if not base:
        return None
    return float(lt["p"]), float(lt["p"]) / float(base) - 1


def run(client, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ET).date().isoformat()
    uni = json.loads((ROOT / "data" / "universe.json").read_text()).get("stocks") or []
    scr, scr_pct = screener(client)
    syms = sorted({s for s in uni + scr if s and s.isalpha()})
    snaps: dict[str, dict] = {}
    for i in range(0, len(syms), 100):
        try:
            snaps.update(client.stock_snapshots(syms[i:i + 100]) or {})
        except Exception:
            continue
    rows = []
    for s, v in snaps.items():
        got = move(v, today)
        if got and got[0] >= MIN_PRICE and abs(got[1]) >= MIN_MOVE:
            rows.append({"symbol": s, "price": round(got[0], 2), "move": round(got[1], 4), "screen": s in scr,
                         "screen_pct": scr_pct.get(s)})
    rows.sort(key=lambda r: -abs(r["move"]))
    rows = rows[:TOP]
    since = (now - timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    heads: dict[str, list[str]] = {}
    for i in range(0, len(rows), 10):
        chunk = [r["symbol"] for r in rows[i:i + 10]]
        try:
            for n in client.news(chunk, since, limit=50):
                for s in n.get("symbols") or []:
                    if s in chunk and len(heads.setdefault(s, [])) < 3:
                        heads[s].append(n.get("headline", "")[:140])
        except Exception:
            continue
    for r in rows:
        r["news"] = heads.get(r["symbol"], [])
    return {"date": today, "generated": now.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"), "scanned": len(snaps), "rows": rows}


def markdown(rep: dict) -> str:
    out = [f"# 盘前异动 {rep['date']}（{rep['generated']}）", "",
           f"扫描 {rep['scanned']} 只（券商的涨跌榜和成交榜 + data/universe.json），今天有盘前成交、股价 $3 以上、离昨收 2% 以上的前 {TOP} 只。"
           "价格是 IEX 最新成交（盘前成交稀，仅供参考），新闻是昨收以来的 Benzinga 标题。给开盘狙击选股用，只记录。", "",
           "| 股票 | 盘前价 | 离昨收 | 在涨跌榜 | 新闻 |", "|---|---|---|---|---|"]
    for r in rep["rows"]:
        news = "；".join(h.replace("|", "/") for h in r["news"]) or "—"
        out.append(f"| {r['symbol']} | ${r['price']:,.2f} | {r['move']:+.1%} | {'是' if r['screen'] else ''} | {news} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    d = ROOT / "research" / "premarket"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep['date']}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (d / f"{rep['date']}.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
