"""Shared pieces for the six idea studies of 2026-10-13 (owner: "都做"): research_lasthalf, research_overnight,
research_pead, research_vixspike, research_eventday, research_btcweekend.

Every study prices real Alpaca option bars (minute bars for same-day trading, daily bars for multi-day holds),
pays 10% over to get in and gets 10% under to get out, and reports per exit rule: mean per $1, the share of tickets
ending at 2x or more, and the sleeve game (half the ledger a ticket, a round ends below 10%): rounds that doubled /
went to zero / reached 20x. Record only.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import SLIP, at_or_after, et_minutes
from hero.research_0dte_names import pick
from hero.research_zdte_timing import game

ROOT = Path(__file__).resolve().parent.parent


def cost_of(px: float) -> float:
    return px + max(px * SLIP, 0.01)


def out_of(px: float) -> float:
    return max(px - max(px * SLIP, 0.01), 0.0)


def contract(client, und: str, kind: str, day: str, spot: float, off: float, min_days: int = 0, max_days: int = 7) -> tuple[str, str] | None:
    """(symbol, expiry): the nearest expiry from day + min_days to day + max_days, first strike `off` beyond spot."""
    d0 = date.fromisoformat(day)
    cs = []
    for status in ("inactive", "active"):
        try:
            cs = client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                         expiration_date_gte=(d0 + timedelta(days=min_days)).isoformat(),
                                         expiration_date_lte=(d0 + timedelta(days=max_days)).isoformat(),
                                         strike_price_gte=f"{spot * (1 - off - 0.08):.2f}", strike_price_lte=f"{spot * (1 + off + 0.08):.2f}")
        except Exception:
            cs = []
        if cs:
            break
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, off)
    return (sym, exp) if sym else None


def minutes(client, sym: str, day: str) -> dict[str, dict]:
    try:
        return et_minutes(client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min").get(sym, []))
    except Exception:
        return {}


def dailies(client, sym: str, after: str, until: str) -> list[dict]:
    """The option's daily bars after `after` up to `until` (inclusive)."""
    try:
        bs = client.option_bars([sym], (date.fromisoformat(after) + timedelta(days=1)).isoformat(), f"{until}T23:00:00Z",
                                timeframe="1Day").get(sym, [])
    except Exception:
        return []
    return [b for b in bs if b["t"][:10] <= until]


def entry_cost(mins: dict[str, dict], at: str) -> tuple[str, float] | None:
    got = at_or_after(mins, at)
    if not got or float(got[1]["c"]) <= 0:
        return None
    return got[0], cost_of(float(got[1]["c"]))


def intraday(mins: dict[str, dict], at: str, out: str, takes=(2, 3, 5)) -> dict[str, float] | None:
    """Same-day exits on minute closes: a take at k x the cost, a trail (2x then a 40% giveback), else out at `out`."""
    got = entry_cost(mins, at)
    if not got:
        return None
    t0, cost = got
    path = [float(v["c"]) for k, v in sorted(mins.items()) if t0 < k <= out]
    last = out_of(path[-1]) / cost - 1 if path else -1.0
    res = {"hold": last}
    for k in takes:
        res[f"take{k}"] = k - 1 if any(c >= k * cost for c in path) else last
    best, armed, res["trail2"] = 0.0, False, last
    for c in path:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail2"] = out_of(c) / cost - 1
            break
    return {k: round(v, 4) for k, v in res.items()}


def multiday(cost: float, days: list[dict], hold: int = 3) -> dict[str, float] | None:
    """Exits over the next sessions' daily option bars: next open, next close, the `hold`-th close, a 2x take on a
    daily close (else the `hold`-th close), and a trail on daily closes (2x then a 40% giveback, else the `hold`-th)."""
    if not days:
        return None
    days = days[:hold]
    closes = [float(b["c"]) for b in days]
    end = out_of(closes[-1]) / cost - 1
    res = {"next_open": out_of(float(days[0]["o"])) / cost - 1, "next_close": out_of(closes[0]) / cost - 1, f"close_d{hold}": end}
    res["take2"] = 1.0 if any(c >= 2 * cost for c in closes) else end
    best, armed, res["trail2"] = 0.0, False, end
    for c in closes:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail2"] = out_of(c) / cost - 1
            break
    return {k: round(v, 4) for k, v in res.items()}


def cell(rows: list[dict], key: str) -> dict | None:
    xs = [(r["day"], r["res"][key]) for r in rows if key in r.get("res", {})]
    if not xs:
        return None
    one: dict[str, float] = {}
    for d, x in xs:
        one.setdefault(d, x)  # one ticket a day for the sleeve game
    seq = sorted(one.items())
    g2, g20 = game(seq, 2), game(seq, 20)
    v = [x for _, x in xs]
    return {"n": len(v), "mean": round(statistics.mean(v), 3), "x2": round(sum(x >= 1 for x in v) / len(v), 3),
            "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}


def report(title: str, intro: str, groups: dict[str, list[dict]], exits: dict[str, str]) -> tuple[dict, str]:
    table = {f"{g}|{x}": cell(rs, x) for g, rs in groups.items() for x in exits}
    out = [f"# {title}", "", intro, "", "每格「平均每 $1 / 收在 2 倍以上 / 账本到 2 倍 成功·归零·到 20 倍」（账本每天只做一笔）。只研究，不改交易。", "",
           "| 情况 | 笔数 | " + " | ".join(exits.values()) + " |", "|---|---|" + "---|" * len(exits)]
    for g, rs in groups.items():
        cs = [table[f"{g}|{x}"] for x in exits]
        n = next((c["n"] for c in cs if c), 0)
        out.append(f"| {g} | {n} | " + " | ".join(f"{c['mean']:+.0%} / {c['x2']:.0%} / {c['h2']}·{c['z2']}·{c['h20']}" if c else "—"
                                                for c in cs) + " |")
    return table, "\n".join(out) + "\n"


def save(name: str, rep: dict, md: str) -> None:
    gen = date.today().isoformat()
    (ROOT / "research" / f"{gen}-{name}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (ROOT / "research" / f"{gen}-{name}.md").write_text(md)
    print(md)
