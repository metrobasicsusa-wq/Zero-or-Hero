"""Today's (or the last session's) biggest movers among the names we watch: data/universe.json stocks and ETFs.

Daily bars on SIP (the request ends 20 minutes ago: SIP refuses the last 15), the last completed or
current session against the one before: change, the day's range from the prior close, and volume
against its 20-day average. Record only. Run: python -m hero.research_movers
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = 15


def rows_from(bars: dict[str, list[dict]]) -> tuple[str, list[dict]]:
    last_day = max((b[-1]["t"][:10] for b in bars.values() if b), default="")
    out = []
    for s, bs in bars.items():
        if len(bs) < 2 or bs[-1]["t"][:10] != last_day:
            continue
        prev, cur = bs[-2], bs[-1]
        pc = float(prev["c"])
        vols = [float(b["v"]) for b in bs[-21:-1]]
        out.append({"symbol": s, "close": float(cur["c"]), "chg": float(cur["c"]) / pc - 1,
                    "high": float(cur["h"]) / pc - 1, "low": float(cur["l"]) / pc - 1,
                    "vol_x": float(cur["v"]) / (sum(vols) / len(vols)) if vols and sum(vols) else None})
    return last_day, out


def run(client) -> dict:
    u = json.loads((ROOT / "data" / "universe.json").read_text())
    syms = list(dict.fromkeys(list(u.get("stocks") or []) + list(u.get("etfs") or [])))
    end = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (date.today() - timedelta(days=45)).isoformat()
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 50):
        params = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "1Day", "start": start, "end": end,
                  "adjustment": "all", "feed": "sip", "limit": 10000}
        while True:
            page = client._d("/v2/stocks/bars", params)
            for s, bs in (page.get("bars") or {}).items():
                bars.setdefault(s, []).extend(bs)
            if not page.get("next_page_token"):
                break
            params["page_token"] = page["next_page_token"]
    day, rows = rows_from(bars)
    return {"generated": date.today().isoformat(), "day": day, "names": len(rows), "rows": rows}


def markdown(rep: dict) -> str:
    rows = rep["rows"]
    pct = lambda x: f"{x:+.1%}"
    vol = lambda x: f"{x:.1f} 倍" if x else "—"
    out = [f"# {rep['day']} 涨跌最多的 {TOP} 只", "", f"data/universe.json 的股票和 ETF，共 {rep['names']} 只有当天数据（SIP 日线，已复权）。成交量倍数 = 当天 / 前 20 天平均。只记录。", ""]
    for title, sub in (("涨得最多", sorted(rows, key=lambda r: -r["chg"])[:TOP]), ("跌得最多", sorted(rows, key=lambda r: r["chg"])[:TOP])):
        out += [f"## {title}", "", "| 股票 | 收盘 | 涨跌 | 盘中最高 | 盘中最低 | 成交量 |", "|---|---|---|---|---|---|"]
        out += [f"| {r['symbol']} | ${r['close']:,.2f} | {pct(r['chg'])} | {pct(r['high'])} | {pct(r['low'])} | {vol(r['vol_x'])} |" for r in sub]
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    (ROOT / "research" / f"{rep['day'] or rep['generated']}-movers.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
