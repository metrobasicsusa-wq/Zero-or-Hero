"""Push the important journal events to a Discord channel (one-way: nothing is read back).

After each trading cycle the workflow runs `python -m hero.notify journal journal-s500`: for each journal,
the events logged since the last push (a cursor in <journal>/notify_cursor.json) whose kind matters --
fills, exits, results, rounds, errors, halts, live orders other than the resting protective stops -- are
sent to the webhook in DC_WEBHOOK, a few to a message. Without DC_WEBHOOK it does nothing. The first run
for a journal only sets the cursor, so the history is not replayed. It never raises: a failed push is
printed (without the address) and the cursor stays put, so the next cycle sends it again.
`python -m hero.notify --test` sends one line to check the connection; `--replay [YYYY-MM-DD]` re-sends\none day's events as a preview without moving the cursor.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
LIMIT = 1900  # Discord allows 2,000 characters per message
LABEL = {"journal": "主账户 $100k", "journal-s500": "多账本 $50k"}
PUSH = {"order", "close", "zdte_fill", "zdte_result", "zdte_skip", "sleeve_fill", "sleeve_exit", "sleeve_result",
        "sleeve_round", "sleeve_skip", "alert_error", "halt", "attempt_end", "rebalance_deferred", "buy_blocked",
        "news_alert", "order_error"}
QUIET_REASONS = {"protective_stop"}  # re-placed every cycle; the fills that matter show up as closes


def wanted(e: dict) -> bool:
    if e.get("kind") not in PUSH or e.get("dry_run"):
        return False
    return not (e.get("kind") == "order" and e.get("reason") in QUIET_REASONS)


ICON = {"zdte_fill": "✅", "sleeve_fill": "✅", "order": "🛒", "close": "💰", "sleeve_exit": "💰", "zdte_result": "📊",
        "sleeve_result": "📊", "sleeve_round": "🔁", "zdte_skip": "⏭️", "sleeve_skip": "⏭️", "alert_error": "⚠️",
        "order_error": "⚠️", "halt": "🛑", "attempt_end": "🛑", "rebalance_deferred": "⏳", "buy_blocked": "⏳",
        "news_alert": "📰"}
OCC = re.compile(r"\b([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})\b")


def readable(text: str) -> str:
    """SPY261008C00781000 -> SPY 10/08 781C."""
    def one(m):
        k = int(m.group(6)) / 1000
        return f"{m.group(1)} {m.group(3)}/{m.group(4)} {k:g}{m.group(5)}"
    return OCC.sub(one, text)


BROKER = {"no available quote": "合约没有报价（没人接手），市价单被拒",
          "insufficient": "资金或可卖数量不够", "market is closed": "市场已收盘",
          "potential wash trade": "可能构成对敲交易，被拒"}


def broker_error(text: str) -> str:
    """A raw API failure as one short Chinese line, without URLs or codes."""
    m = re.search(r'"message"\s*:\s*"([^"]+)"', text)
    msg = m.group(1) if m else re.sub(r"https?://\S+", "", text)
    for k, zh in BROKER.items():
        if k in msg.lower():
            return f"券商拒绝：{zh}"
    return f"券商拒绝：{msg.strip()[:160]}"


def action(e: dict) -> str:
    side, sym = e.get("side"), e.get("symbol") or ""
    if e.get("kind") != "order" or side not in ("buy", "sell"):
        return ""
    unit = "张" if OCC.search(sym) else "股"
    size = f"{e['qty']} {unit}" if e.get("qty") else (f"${float(e['notional']):,.0f}" if e.get("notional") else "")
    return f"{'买' if side == 'buy' else '卖'} {size}".strip()


def body(e: dict) -> str:
    text = str(e.get("why") or e.get("error") or e.get("headline") or e.get("kind"))
    if e.get("kind") in ("alert_error", "order_error"):
        text = broker_error(text) if ("http" in text or '"message"' in text) else text
        if e.get("source"):
            text = f"出错（{e['source']}）：{text}"
    sym, act = e.get("symbol"), action(e)
    lead = " ".join(x for x in (sym, act) if x)
    if lead and (act or (sym and sym not in text)):
        text = f"{lead}：{text}"
    return readable(text)


def lines(evs: list[dict]) -> list[str]:
    """One line per distinct message, in the order first seen; a message repeated by retries in later
    cycles is shown once with how many times and until when."""
    seen: dict[tuple, list] = {}
    for e in sorted(evs, key=lambda e: e.get("ts", "")):
        try:
            t = datetime.fromisoformat(e["ts"]).astimezone(ET).strftime("%H:%M")
        except Exception:
            t = "?"
        key = (e.get("kind"), body(e))
        if key in seen:
            seen[key][1] += 1
            seen[key][2] = t
        else:
            seen[key] = [t, 1, t]
    out = []
    for (k, text), (t0, n, t1) in seen.items():
        out.append((f"{ICON.get(k, '•')} `{t0}` {text}" + (f"（重复 {n} 次，到 {t1}）" if n > 1 else ""))[:600])
    return out


def line(e: dict) -> str:
    return lines([e])[0]


def header(root: Path) -> str:
    """The account's equity and today's change, from the snapshot this cycle just wrote."""
    try:
        a = json.loads((root / "snapshot.json").read_text())["account"]
        eq, last = float(a["equity"]), float(a["last_equity"])
        cash = float(a.get("cash") or 0)
        return f"净值 ${eq:,.0f}（今天 {eq - last:+,.0f}，{eq / last - 1:+.2%}）" + ("，⚠️ 现金为负" if cash < 0 else "")
    except Exception:
        return ""


def batches(title: str, lines: list[str]) -> list[str]:
    out, cur = [], f"**{title}**"
    for ln in lines:
        if len(cur) + 1 + len(ln) > LIMIT:
            out.append(cur)
            cur = f"**{title}（续）**"
        cur += "\n" + ln
    out.append(cur)
    return out


def post(url: str, content: str) -> bool:
    try:
        r = requests.post(url, json={"content": content}, timeout=10)
        return r.status_code < 300
    except Exception:
        return False


def push_journal(root: Path, url: str | None) -> int:
    path, cur_path = root / "trades.jsonl", root / "notify_cursor.json"
    if not url or not path.exists():
        return 0
    events = []
    for l in path.read_text().splitlines():
        try:
            events.append(json.loads(l))
        except ValueError:
            continue
    last = max((e.get("ts", "") for e in events), default="")
    cursor = json.loads(cur_path.read_text()).get("ts") if cur_path.exists() else None
    if cursor is None:  # first run: start from now, do not replay the history
        cur_path.write_text(json.dumps({"ts": last}) + "\n")
        return 0
    new = [e for e in events if e.get("ts", "") > cursor and wanted(e)]
    if not new:
        cur_path.write_text(json.dumps({"ts": last}) + "\n")
        return 0
    title = LABEL.get(root.name, root.name)
    head = header(root)
    if head:
        title = f"{title} · {head}"
    for msg in batches(title, lines(new)):
        if not post(url, msg):
            print(f"notify: Discord push failed for {root.name}; will retry next cycle")
            return 0
    cur_path.write_text(json.dumps({"ts": last}) + "\n")
    return len(new)


def replay(root: Path, url: str, day: str) -> int:
    """Send one day's pushable events again as a preview; the cursor is not touched."""
    path = root / "trades.jsonl"
    if not path.exists():
        return 0
    evs = []
    for l in path.read_text().splitlines():
        try:
            e = json.loads(l)
        except ValueError:
            continue
        try:
            d = datetime.fromisoformat(e["ts"]).astimezone(ET).date().isoformat()
        except Exception:
            continue
        if d == day and wanted(e):
            evs.append(e)
    if not evs:
        return 0
    title = f"【回放 {day}】{LABEL.get(root.name, root.name)}"
    head = header(root)
    if head:
        title = f"{title} · {head}"
    for msg in batches(title, lines(evs)):
        if not post(url, msg):
            return 0
    return len(evs)


def main(argv: list[str]) -> int:
    url = os.environ.get("DC_WEBHOOK", "").strip() or None
    if "--test" in argv:
        if not url:
            print("notify: DC_WEBHOOK is not set")
            return 1
        ok = post(url, "Zero-or-Hero：Discord 通知已接通。之后每个交易周期的成交、平仓、结果和出错会推送到这里。")
        print("notify: test message sent" if ok else "notify: test message failed")
        return 0 if ok else 1
    root = Path(__file__).resolve().parent.parent
    if "--replay" in argv:
        if not url:
            print("notify: DC_WEBHOOK is not set")
            return 1
        i = argv.index("--replay")
        day = argv[i + 1] if len(argv) > i + 1 else datetime.now(ET).date().isoformat()
        for name in ("journal", "journal-s500"):
            print(f"notify: replay {name} {day}: {replay(root / name, url, day)} event(s)")
        return 0
    for name in [a for a in argv if not a.startswith("-")] or ["journal", "journal-s500"]:
        n = push_journal(root / name, url)
        print(f"notify: {name}: {n} event(s) pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
