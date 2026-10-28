"""Scores the signal follow card each evening (docs/questions/2026-10-28-follow-card.md, task 3).

Reads the day's card (exchange/claude/YYYY-MM-DD.follow.json, written by hero.follow_card), books what it named in
research/follow-card/book.json, marks every position still held on the evening's CBOE delayed quotes, closes it by
its line's rule, and rewrites the signal card's section of posts/follow/scorecard.md (between the two markers; the
rest of the page is left as it is):

- fast: bought at the card's ask; out once the bid is 2x that, else at the 20th session's bid.
- depth at the money / 5% out: out at the bid on the 63rd / 42nd session (or the expiry when sooner).
- the $5 spread: bought at the card's mid; out once its mid reaches $4, else on the expiry.

Per $1 = what it fetched (or would fetch now) / what it cost - 1, on real bids and asks (the studies added 5% a side
instead). A day without a signal is a $0 card. Paper-trading reference, not investment advice.

    python -m hero.follow_score [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from hero.follow_card import ET, ROOT, SPREAD_SELL, fetch_chain

BOOK = ROOT / "research" / "follow-card" / "book.json"
SCORECARD = ROOT / "posts" / "follow" / "scorecard.md"
START, END = "<!-- signal-card:start -->", "<!-- signal-card:end -->"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": {}, "positions": []}


def open_from_card(book: dict, card: dict) -> list[dict]:
    """Book what the card named (once per card day). Returns the positions added."""
    c = card.get("card") or {}
    day = c.get("day")
    if not day or day in book["days"]:
        return []
    added = []
    for s in c.get("signals") or []:
        und = s["und"]
        if s["line"] == "fast" and s.get("contract"):
            k = s["contract"]
            added.append({"id": f"{day}:fast:{und}", "day": day, "line": "fast", "und": und, "legs": [k["symbol"]],
                          "what": f"SMH {k['strike']:g}C {k['expiry']}", "entry": k["ask"], "rule": "take2",
                          "exit_day": min(s["exit_day"], k["expiry"])})
        if s["line"] == "depth":
            ct = s.get("contracts") or {}
            for leg, ex in (("atm", s.get("exit_atm")), ("otm5", s.get("exit_otm5"))):
                k = ct.get(leg)
                if k:
                    added.append({"id": f"{day}:{leg}:{und}", "day": day, "line": leg, "und": und, "legs": [k["symbol"]],
                                  "what": f"{und} {k['strike']:g}C {k['expiry']}", "entry": k["ask"], "rule": "hold",
                                  "exit_day": min(ex or k["expiry"], k["expiry"])})
            sp = ct.get("spread")
            if sp:
                added.append({"id": f"{day}:spread:{und}", "day": day, "line": "spread", "und": und,
                              "legs": [sp["long"]["symbol"], sp["short"]["symbol"]],
                              "what": f"{und} {sp['long']['strike']:g}/{sp['short']['strike']:g} {sp['long']['expiry']}",
                              "entry": sp["mid"], "rule": "take4", "exit_day": sp["long"]["expiry"]})
    for p in added:
        p.update(status="open", mark=None, mark_day=None, ret=None)
    book["positions"] += added
    book["days"][day] = "有信号" if c.get("signals") else "没有信号"
    return added


def value(p: dict, quotes: dict[str, dict]) -> float | None:
    """What the position fetches now: a single leg's bid; a spread's mid (long mid - short mid, at least 0)."""
    qs = [quotes.get(s) for s in p["legs"]]
    if any(q is None for q in qs):
        return None
    if len(qs) == 1:
        return qs[0]["bid"]
    mid = lambda q: (q["bid"] + q["ask"]) / 2
    return max(mid(qs[0]) - mid(qs[1]), 0.0)


def update(book: dict, day: str, quotes: dict[str, dict]) -> list[dict]:
    """Mark every open position on `day` and close the ones whose rule says so. Returns the ones closed."""
    closed = []
    for p in book["positions"]:
        if p["status"] != "open" or p["day"] >= day:
            continue
        v = value(p, quotes)
        if v is None:
            continue
        p.update(mark=round(v, 4), mark_day=day, ret=round(v / p["entry"] - 1, 4) if p["entry"] else None)
        out = ((p["rule"] == "take2" and v >= 2 * p["entry"]) or (p["rule"] == "take4" and v >= SPREAD_SELL)
               or day >= p["exit_day"])
        if out:
            p["status"] = "closed"
            closed.append(p)
    return closed


def block(book: dict) -> str:
    rows = sorted(book["positions"], key=lambda p: (p["day"], p["id"]))
    shut = [p for p in rows if p["status"] == "closed" and p["ret"] is not None]
    held = [p for p in rows if p["status"] == "open"]
    quiet = sum(1 for v in book["days"].values() if v == "没有信号")
    L = [START, "## 新卡（有信号才出卡，hero/follow_card.py；hero/follow_score.py 每天收盘打分）", "",
         "按真实的买价 / 卖价算：单腿按卡片上的卖价买、按买价卖；价差按中间价。每 $1 是现在能拿回的（或出场时拿回的）÷ 花的 − 1。", "",
         "| 卡片日期 | 线 | 合约 | 买入 | 最新 / 出场 | 每 $1 | 状态 |", "|---|---|---|---|---|---|---|"]
    names = {"fast": "快线", "atm": "深度 · 平值 63 天", "otm5": "深度 · 价外 5% 42 天", "spread": "深度 · $5 价差"}
    for p in rows:
        state = f"还拿着（{p['mark_day'] or '未标记'}，{p['exit_day']} 出场）" if p["status"] == "open" else f"已出场 {p['mark_day']}"
        mark = "—" if p["mark"] is None else f"{p['mark']:.2f}"
        ret = "—" if p["ret"] is None else f"{p['ret']:+.2f}"
        L.append(f"| {p['day'][5:]} | {names.get(p['line'], p['line'])} | {p['what']} | {p['entry']:.2f} | {mark} | {ret} | {state} |")
    total = sum(p["ret"] for p in shut)
    L += ["", f"- 出卡 {len(book['days'])} 天：有信号 {len(book['days']) - quiet} 天，没有信号 {quiet} 天（$0）。",
          f"- 已出场 {len(shut)} 笔，每 $1 合计 **{total:+.2f}**" + (f"，平均 {total / len(shut):+.2f}" if shut else "") + f"；还拿着 {len(held)} 笔。",
          "", "模拟盘，不是投资建议。", END]
    return "\n".join(L)


def write_block(text: str, blk: str) -> str:
    if START in text and END in text:
        head, rest = text.split(START, 1)
        return head + blk + rest.split(END, 1)[1]
    return text.rstrip("\n") + "\n\n" + blk + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, US Eastern)")
    a = ap.parse_args(argv)
    day = a.date or datetime.now(ET).date().isoformat()
    book = load(BOOK)
    card = ROOT / "exchange" / "claude" / f"{day}.follow.json"
    if card.exists():
        open_from_card(book, json.loads(card.read_text(encoding="utf-8")))
    unds = sorted({p["und"] for p in book["positions"] if p["status"] == "open" and p["day"] < day})
    quotes = {c["symbol"]: c for u in unds for c in fetch_chain(u)}
    update(book, day, quotes)
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    SCORECARD.write_text(write_block(SCORECARD.read_text(encoding="utf-8") if SCORECARD.exists() else "", block(book)), encoding="utf-8")
    print(block(book))
    return 0


if __name__ == "__main__":
    sys.exit(main())
