"""Push the important journal events to a Discord channel (one-way: nothing is read back).

After each trading cycle the workflow runs `python -m hero.notify journal journal-s500`: for each journal,
the events logged since the last push (a cursor in <journal>/notify_cursor.json) whose kind matters --
fills, exits, results, rounds, errors, halts, live orders other than the resting protective stops -- are
sent to the webhook in DC_WEBHOOK, a few to a message. Without DC_WEBHOOK it does nothing. The first run
for a journal only sets the cursor, so the history is not replayed. It never raises: a failed push is
printed (without the address) and the cursor stays put, so the next cycle sends it again.
`python -m hero.notify --test` sends one line to check the connection.
"""

from __future__ import annotations

import json
import os
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


def line(e: dict) -> str:
    try:
        t = datetime.fromisoformat(e["ts"]).astimezone(ET).strftime("%H:%M")
    except Exception:
        t = "?"
    text = e.get("why") or e.get("error") or e.get("headline") or e.get("kind")
    if e.get("kind") == "alert_error":
        text = f"出错（{e.get('source', '?')}）：{text}"
    sym = e.get("symbol")
    head = f"{t} {sym}：" if sym and sym not in str(text) else f"{t} "
    return (head + str(text))[:600]


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
    for msg in batches(title, [line(e) for e in sorted(new, key=lambda e: e.get("ts", ""))]):
        if not post(url, msg):
            print(f"notify: Discord push failed for {root.name}; will retry next cycle")
            return 0
    cur_path.write_text(json.dumps({"ts": last}) + "\n")
    return len(new)


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
    for name in [a for a in argv if not a.startswith("-")] or ["journal", "journal-s500"]:
        n = push_journal(root / name, url)
        print(f"notify: {name}: {n} event(s) pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
