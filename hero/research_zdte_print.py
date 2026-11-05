"""Were those entries real? (owner, 11-05: 「为什么这几天能赚」)

research/2026-11-05-zdte-logic-review.md found the follow card's best day, 2026-08-03 (+713%), bought its call at
0.020% of SPY -- about a tenth of what a 0.3%-out same-day option is worth with VIX at 16 -- and that without that
one day the 52 trigger days net to zero. Ten more entries were under 40% of a VIX-based fair value. This dumps the
minute bars around 10:00 for those contracts, plus two ordinary ones for comparison, so the entry prints can be
read: a lone tiny print the replay bought at, or a real quote. Record only.
Run: python -m hero.research_zdte_print
"""

from __future__ import annotations

import sys
from datetime import date

from hero.research_0dte import at_or_before, et_minutes
from hero.research_ideas import save

PAIRS = [("2026-08-03", "SPY260803C00756000", "suspect"), ("2026-03-31", "SPY260331C00645000", "suspect"),
         ("2026-02-06", "SPY260206C00688000", "suspect"), ("2026-03-30", "SPY260330P00633000", "suspect"),
         ("2024-11-05", "SPY241105C00576000", "suspect"), ("2025-08-20", "SPY250820P00633000", "suspect"),
         ("2025-03-17", "SPY250317C00568000", "suspect"), ("2025-08-04", "SPY250804C00632000", "suspect"),
         ("2025-12-19", "SPY251219C00683000", "suspect"), ("2024-08-07", "SPY240807C00534000", "suspect"),
         ("2026-03-05", "SPY260305C00688000", "suspect"), ("2025-03-03", "SPY250303P00590000", "ordinary"),
         ("2025-04-21", "SPY250421P00515000", "ordinary")]


def run(client) -> list[dict]:
    out = []
    for day, sym, tag in PAIRS:
        try:
            bars = client.option_bars([sym], f"{day}T13:30:00Z", f"{day}T20:00:00Z", timeframe="1Min").get(sym, [])
        except Exception as e:  # noqa: BLE001
            out.append({"day": day, "symbol": sym, "tag": tag, "error": str(e)[:80]})
            continue
        m = et_minutes(bars)
        win = [{"t": k, **{x: m[k].get(x) for x in ("o", "h", "l", "c", "v", "n")}} for k in sorted(m) if "09:50" <= k <= "10:15"]
        late = at_or_before(m, "15:30")
        out.append({"day": day, "symbol": sym, "tag": tag, "n_bars": len(m), "first": min(m) if m else None,
                    "window": win, "c1530": float(late["c"]) if late else None})
    return out


def markdown(rows: list[dict]) -> str:
    out = ["# 末日单回测里的进场价是真的吗？ 2026-11-05", "",
           "可疑的进场（10:00 买价不到 VIX 估的公允价四成）和两笔正常的，列出 09:50–10:15 每分钟的开高低收和成交量，"
           "以及 15:30 的收盘价。只看不改。", ""]
    for r in rows:
        out.append(f"## {r['day']} {r['symbol']}（{r['tag']}）")
        if r.get("error"):
            out += ["", f"取不到：{r['error']}", ""]
            continue
        out += ["", f"全天 {r['n_bars']} 根分钟线，第一根 {r['first']}，15:30 收盘 {r['c1530']}", "",
                "| 分钟 | 开 | 高 | 低 | 收 | 量 | 笔数 |", "|---|---|---|---|---|---|---|"]
        for b in r["window"]:
            out.append(f"| {b['t']} | {b['o']} | {b['h']} | {b['l']} | {b['c']} | {b['v']} | {b.get('n')} |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rows = run(Alpaca())
    save("zdte-print", {"generated": date.today().isoformat(), "rows": rows}, markdown(rows))


if __name__ == "__main__":
    sys.exit(main())
