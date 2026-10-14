"""Push the important journal events to a Discord channel as small cards (one-way: nothing is read back).

After each trading cycle the workflow runs `python -m hero.notify journal journal-s500`: for each journal,
the events logged since the last push (a cursor in <journal>/notify_cursor.json) whose kind matters --
fills, exits, results, rounds, errors, halts, live orders other than the resting protective stops -- are
sent to the webhook in DC_WEBHOOK as Discord embeds: one header card with the account's value, then one card
per position (contract or stock) with what happened to it and, while it is held, its size, cost, price and
open profit; the colour says how it is going. Sleeve lines lead with the ledger's name (SPY 末日 $1k) and a
result's percentage. Once a day after 15:55 ET: one card listing every holding, and for a journal with sleeves
the ledgers' scoreboard (each at cost against its start, today's realized result).
Without DC_WEBHOOK it does nothing. The first run for a journal only sets the cursor, so the history is not
replayed. It never raises: a failed push is printed (without the address) and the cursor stays put.
`python -m hero.notify --test` sends one line to check the connection; `--replay [YYYY-MM-DD]` re-sends
one day's events (and the holdings) as a preview without moving the cursor; `--note` sends notes/discord.md
as one card (a summary written by Claude while the owner is away).
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
LABEL = {"journal": "主账户 $100k", "journal-s500": "多账本 $50k"}
PUSH = {"order", "close", "zdte_fill", "zdte_result", "zdte_skip", "sleeve_fill", "sleeve_exit", "sleeve_result",
        "sleeve_round", "sleeve_skip", "alert_error", "halt", "attempt_end", "rebalance_deferred", "buy_blocked",
        "news_alert", "order_error", "sleeve_recover"}
QUIET_REASONS = {"protective_stop"}  # re-placed every cycle; the fills that matter show up as closes
ICON = {"zdte_fill": "✅", "sleeve_fill": "✅", "order": "🛒", "close": "💰", "sleeve_exit": "💰", "zdte_result": "📊",
        "sleeve_result": "📊", "sleeve_round": "🔁", "zdte_skip": "⏭️", "sleeve_skip": "⏭️", "alert_error": "⚠️",
        "order_error": "⚠️", "halt": "🛑", "attempt_end": "🛑", "rebalance_deferred": "⏳", "buy_blocked": "⏳",
        "news_alert": "📰", "sleeve_recover": "🩹"}
ERRORS = {"alert_error", "order_error", "halt", "attempt_end"}
BLUE, GREEN, RED, ORANGE, GREY = 0x3498DB, 0x2ECC71, 0xE74C3C, 0xE67E22, 0x95A5A6
EMBEDS_PER_MESSAGE, CHARS_PER_MESSAGE = 10, 5500  # Discord: 10 embeds and 6,000 characters per message
HOLDINGS_AFTER = "15:55"
OCC = re.compile(r"\b([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})\b")
SLEEVE = {"spy0dte": "SPY 末日", "qqq0dte": "QQQ 末日", "tsla0dte": "TSLA 末日", "nvda0dte": "NVDA 末日",
          "spy0dte-near": "SPY 末日·近档", "tsla0dte-5x": "TSLA 末日·5 倍", "wk-trend": "周票·趋势", "wk-momo": "周票·动量",
          "wk-news": "周票·新闻", "net": "撒网", "gap": "低开反弹", "flush": "急跌反弹", "earn": "财报彩票", "tqqq": "TQQQ 趋势",
          "relay": "接力", "spydip": "SPY 昨跌", "opengap": "开盘跳空", "oneshot": "一把到", "sniper": "开盘狙击",
          "overnight": "隔夜动量", "pead": "财报漂移", "vixspike": "恐慌反弹"}
BROKER = {"no available quote": "合约没有报价（没人接手），市价单被拒",
          "insufficient": "资金或可卖数量不够", "market is closed": "市场已收盘",
          "potential wash trade": "可能构成对敲交易，被拒"}


def wanted(e: dict) -> bool:
    if e.get("kind") not in PUSH or e.get("dry_run"):
        return False
    return not (e.get("kind") == "order" and e.get("reason") in QUIET_REASONS)


def readable(text: str) -> str:
    """SPY261008C00781000 -> SPY 10/08 781C."""
    def one(m):
        k = int(m.group(6)) / 1000
        return f"{m.group(1)} {m.group(3)}/{m.group(4)} {k:g}{m.group(5)}"
    return OCC.sub(one, text)


def broker_error(text: str) -> str:
    """A raw API failure as one short Chinese line, without URLs or codes."""
    m = re.search(r'"message"\s*:\s*"([^"]+)"', text)
    msg = m.group(1) if m else re.sub(r"https?://\S+", "", text)
    for k, zh in BROKER.items():
        if k in msg.lower():
            return f"券商拒绝：{zh}"
    return f"券商拒绝：{msg.strip()[:160]}"


def sleeve_name(sid: str) -> str:
    """spy0dte-1000 -> SPY 末日 $1k (unknown ids keep their own name)."""
    m = re.fullmatch(r"(.+)-(\d+)", sid or "")
    if not m:
        return sid
    amt = int(m.group(2))
    return f"{SLEEVE.get(m.group(1), m.group(1))} ${amt // 1000}k" if amt >= 1000 and amt % 1000 == 0 else \
        f"{SLEEVE.get(m.group(1), m.group(1))} ${amt:,}"


def sleeve_text(e: dict, text: str) -> str:
    """A sleeve event's sentence, led by the ledger's name; the contract (on the card's title) and the result's
    percentage (moved to the front) are not repeated."""
    sid, sym = e.get("sleeve") or "", e.get("symbol") or ""
    for lead in (f"{sid}：{sym} ", f"{sid}：{sym}", f"{sid}："):
        if sid and text.startswith(lead):
            text = text[len(lead):]
            break
    text = text.replace("挂在券商", "已挂单")
    name = f"**{sleeve_name(sid)}**" if sid else ""
    if e.get("kind") == "sleeve_result" and isinstance(e.get("ret"), (int, float)):
        text = re.sub(r"（[+-]?\d[\d,]*%）", "", text)
        return f"{name} **{e['ret']:+.0%}** · {text}".strip()
    return f"{name} {text}".strip()


def action(e: dict) -> str:
    side, sym = e.get("side"), e.get("symbol") or ""
    if e.get("kind") != "order" or side not in ("buy", "sell"):
        return ""
    unit = "张" if OCC.search(sym) else "股"
    size = f"{e['qty']} {unit}" if e.get("qty") else (f"${float(e['notional']):,.0f}" if e.get("notional") else "")
    return f"{'买' if side == 'buy' else '卖'} {size}".strip()


def body(e: dict) -> str:
    """The event's own sentence, without the symbol (the card's title carries it)."""
    text = str(e.get("why") or e.get("error") or e.get("headline") or e.get("kind"))
    if e.get("kind") in ("alert_error", "order_error"):
        text = broker_error(text) if ("http" in text or '"message"' in text) else text
        if e.get("source") and not e.get("symbol"):
            text = f"出错（{e['source']}）：{text}"
    if e.get("sleeve"):
        text = sleeve_text(e, text)
    act = action(e)
    return readable(f"**{act}**：{text}" if act else text)


def hhmm(e: dict) -> str:
    try:
        return datetime.fromisoformat(e["ts"]).astimezone(ET).strftime("%H:%M")
    except Exception:
        return "?"


def icon(kind: str, text: str) -> str:
    if kind in ("sleeve_result", "zdte_result"):
        m = re.search(r"\*\*([+-])\d", text) or re.search(r"（([+-])\d+%）", text)
        if m:
            return "🟢" if m.group(1) == "+" else "🔴"
    return ICON.get(kind, "•")


def lines(evs: list[dict]) -> list[str]:
    """One line per distinct message, in the order first seen; a message repeated by retries in later
    cycles is shown once with how many times and until when."""
    seen: dict[tuple, list] = {}
    for e in sorted(evs, key=lambda e: e.get("ts", "")):
        key, t = (e.get("kind"), body(e)), hhmm(e)
        if key in seen:
            seen[key][1] += 1
            seen[key][2] = t
        else:
            seen[key] = [t, 1, t]
    return [(f"{icon(k, text)} `{t0}` {text}" + (f"（重复 {n} 次，到 {t1}）" if n > 1 else ""))[:700]
            for (k, text), (t0, n, t1) in seen.items()]


def snapshot(root: Path) -> dict:
    try:
        return json.loads((root / "snapshot.json").read_text())
    except Exception:
        return {}


def money(x) -> str:
    return f"${float(x):,.2f}"


def position_fields(p: dict) -> list[dict]:
    unit = "张" if OCC.search(p.get("symbol", "")) else "股"
    fields = [{"name": "持仓", "value": f"{float(p.get('qty', 0)):g} {unit}", "inline": True},
              {"name": "成本", "value": money(p.get("avg_entry_price", 0)), "inline": True},
              {"name": "现价", "value": money(p.get("current_price", 0)), "inline": True}]
    if p.get("unrealized_pl") is not None:
        pl, pct = float(p["unrealized_pl"]), float(p.get("unrealized_plpc") or 0)
        fields.append({"name": "浮动盈亏", "value": f"{pl:+,.2f}（{pct:+.1%}）", "inline": True})
    if p.get("unrealized_intraday_pl") is not None:
        fields.append({"name": "今天", "value": f"{float(p['unrealized_intraday_pl']):+,.2f}", "inline": True})
    return fields


def colour(evs: list[dict], pos: dict | None) -> int:
    if any(e.get("kind") in ERRORS for e in evs):
        return RED
    rets = [e["ret"] for e in evs if isinstance(e.get("ret"), (int, float))]
    if rets:
        return GREEN if rets[-1] > 0 else RED
    if pos and pos.get("unrealized_plpc") is not None:
        return GREEN if float(pos["unrealized_plpc"]) >= 0 else RED
    if any(e.get("kind") in ("close", "sleeve_exit", "zdte_skip", "sleeve_skip") for e in evs):
        return ORANGE
    return BLUE


def header_card(root: Path, title: str) -> dict:
    a = snapshot(root).get("account") or {}
    try:
        eq, last, cash = float(a["equity"]), float(a["last_equity"]), float(a.get("cash") or 0)
    except Exception:
        return {"title": title, "color": GREY}
    card = {"title": title, "color": GREEN if eq >= last else RED,
            "description": f"净值 **${eq:,.0f}** · 今天 **{eq - last:+,.0f}**（{eq / last - 1:+.2%}）"}
    if cash < 0:
        card["fields"] = [{"name": "⚠️ 现金为负", "value": f"{money(cash)}（在借钱）", "inline": False}]
    return card


def symbol_of(e: dict) -> str:
    """The position an event is about; a broker error names it only inside its URL."""
    if e.get("symbol"):
        return e["symbol"]
    raw = str(e.get("error") or "")
    m = OCC.search(raw) or re.search(r"/positions/([A-Z.]{1,8})\b", raw)
    return m.group(0) if m and m.re is OCC else (m.group(1) if m else "")


def cards(root: Path, evs: list[dict], title: str, holdings: bool = False) -> list[dict]:
    """A header card, a card per position with events, then (if asked) a card for every other holding."""
    held = {p["symbol"]: p for p in snapshot(root).get("positions") or [] if p.get("symbol")}
    by_sym: dict[str, list[dict]] = {}
    for e in sorted(evs, key=lambda e: e.get("ts", "")):
        by_sym.setdefault(symbol_of(e), []).append(e)
    out = [header_card(root, title)]
    for sym, es in by_sym.items():
        pos = held.get(sym)
        card = {"title": readable(sym) if sym else "其他", "color": colour(es, pos),
                "description": "\n".join(lines(es))[:3800]}
        if pos:
            card["fields"] = position_fields(pos)
        elif sym:
            card["footer"] = {"text": "已不在持仓里"}
        out.append(card)
    if holdings:
        for extra in (lambda: holdings_cards(list(held.values())), lambda: ledger_cards(root, today_of(evs))):
            try:  # a summary card must never cost the events their push
                out += extra()
            except Exception as e:
                print(f"notify: summary card skipped ({type(e).__name__})")
    return out


def today_of(evs: list[dict]) -> str:
    try:
        return max(datetime.fromisoformat(e["ts"]).astimezone(ET).date().isoformat() for e in evs)
    except Exception:
        return datetime.now(ET).date().isoformat()


def chunked(title: str, rows: list[str], color: int, footer: str = "") -> list[dict]:
    """Rows as one card (or several, each under Discord's description limit)."""
    out, cur = [], []
    for r in rows:
        if cur and len("\n".join(cur + [r])) > 3800:
            out.append(cur)
            cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    cards_ = []
    for i, rs in enumerate(out):
        c = {"title": title if i == 0 else f"{title}（续）", "color": color, "description": "\n".join(rs)}
        if footer:
            c["footer"] = {"text": footer}
        cards_.append(c)
    return cards_


def holdings_cards(pos: list[dict]) -> list[dict]:
    """Every holding as one line in one card, the largest first, with the total open profit."""
    if not pos:
        return []
    rows, total = [], 0.0
    for p in sorted(pos, key=lambda p: -abs(float(p.get("market_value") or 0))):
        unit = "张" if OCC.search(p.get("symbol", "")) else "股"
        pl = float(p.get("unrealized_pl") or 0)
        pct = float(p.get("unrealized_plpc") or 0)
        total += pl
        rows.append(f"{'🟢' if pl >= 0 else '🔴'} **{readable(p['symbol'])}** {float(p.get('qty', 0)):g} {unit} · "
                    f"{money(p.get('avg_entry_price', 0))} → {money(p.get('current_price', 0))} · **{pl:+,.0f}**（{pct:+.0%}）")
    return chunked(f"收盘持仓 {len(pos)} 个 · 浮动 {total:+,.0f}", rows, GREEN if total >= 0 else RED)


def ledger_cards(root: Path, day: str) -> list[dict]:
    """The sleeves' scoreboard (journal-s500/sleeves.json): each ledger at cost, today's realized result, rounds."""
    try:
        book = json.loads((root / "sleeves.json").read_text()).get("sleeves") or {}
    except Exception:
        return []
    rows, sum_now, sum_start, sum_today = [], 0.0, 0.0, 0.0
    scored = []
    for sid, s in book.items():
        held = sum(float(t.get("paid") or 0) for t in s.get("open") or [])
        now, start = float(s.get("cash") or 0) + held, float(s.get("start") or 0)
        today = sum(float(t.get("proceeds") or 0) - float(t.get("paid") or 0) for t in s.get("closed") or []
                    if str(t.get("closed", "")).startswith(day))
        sum_now, sum_start, sum_today = sum_now + now, sum_start + start, sum_today + today
        scored.append((now / start if start else 0, sid, s, now, start, today, len(s.get("open") or [])))
    idle = [sid for ratio, sid, s, now, start, today, n in scored
            if abs(now - start) < 0.5 and not n and not today and int(s.get("round") or 1) == 1]
    for ratio, sid, s, now, start, today, n in sorted(scored, key=lambda x: -x[0]):
        if sid in idle:
            continue
        mark = "🏆" if ratio >= 2 else "🟢" if ratio >= 1 else "🔴"
        extra = [f"今天 {today:+,.0f}" if today else "", f"持 {n} 笔" if n else "",
                 f"第 {s['round']} 轮" if int(s.get("round") or 1) > 1 else ""]
        rows.append(f"{mark} **{sleeve_name(sid)}** ${now:,.0f}（{ratio:.2f} 倍）" + "".join(f" · {x}" for x in extra if x))
    if idle:
        names: dict[str, list[str]] = {}
        for sid in idle:
            base, _, amt = sid.rpartition("-")
            names.setdefault(SLEEVE.get(base, base), []).append(amt)
        rows.append("⚪ 还没出手：" + "、".join(f"{k}（{'/'.join(v)}）" for k, v in names.items()))
    if not rows:
        return []
    return chunked(f"账本榜 · 合计 ${sum_now:,.0f} / 起点 ${sum_start:,.0f} · 今天已实现 {sum_today:+,.0f}", rows,
                   GREEN if sum_today >= 0 else RED, "持仓按成本算；🏆 = 到起点 2 倍以上")


def size(card: dict) -> int:
    return len(json.dumps(card, ensure_ascii=False))


def messages(cards_: list[dict]) -> list[dict]:
    out, cur, n = [], [], 0
    for c in cards_:
        if cur and (len(cur) >= EMBEDS_PER_MESSAGE or n + size(c) > CHARS_PER_MESSAGE):
            out.append({"embeds": cur})
            cur, n = [], 0
        cur.append(c)
        n += size(c)
    if cur:
        out.append({"embeds": cur})
    return out


def post(url: str, payload: dict | str) -> bool:
    try:
        r = requests.post(url, json=payload if isinstance(payload, dict) else {"content": payload}, timeout=10)
        return r.status_code < 300
    except Exception:
        return False


def read_events(root: Path) -> list[dict]:
    path = root / "trades.jsonl"
    out = []
    if path.exists():
        for l in path.read_text().splitlines():
            try:
                out.append(json.loads(l))
            except ValueError:
                continue
    return out


def send(url: str, cards_: list[dict]) -> bool:
    return all(post(url, m) for m in messages(cards_))


def push_journal(root: Path, url: str | None, now: datetime | None = None) -> int:
    cur_path = root / "notify_cursor.json"
    if not url or not (root / "trades.jsonl").exists():
        return 0
    events = read_events(root)
    last = max((e.get("ts", "") for e in events), default="")
    state = json.loads(cur_path.read_text()) if cur_path.exists() else None
    if state is None:  # first run: start from now, do not replay the history
        cur_path.write_text(json.dumps({"ts": last}) + "\n")
        return 0
    now = (now or datetime.now(ET)).astimezone(ET)
    today = now.date().isoformat()
    holdings = now.strftime("%H:%M") >= HOLDINGS_AFTER and state.get("holdings_day") != today and now.weekday() < 5
    new = [e for e in events if e.get("ts", "") > state.get("ts", "") and wanted(e)]
    if new or holdings:
        title = LABEL.get(root.name, root.name) + ("（收盘持仓）" if holdings and not new else "")
        if not send(url, cards(root, new, title, holdings)):
            print(f"notify: Discord push failed for {root.name}; will retry next cycle")
            return 0
    state["ts"] = last
    if holdings:
        state["holdings_day"] = today
    cur_path.write_text(json.dumps(state) + "\n")
    return len(new)


def replay(root: Path, url: str, day: str) -> int:
    """Send one day's pushable events and the current holdings again as a preview; the cursor is not touched."""
    if not (root / "trades.jsonl").exists():
        return 0
    evs = []
    for e in read_events(root):
        try:
            d = datetime.fromisoformat(e["ts"]).astimezone(ET).date().isoformat()
        except Exception:
            continue
        if d == day and wanted(e):
            evs.append(e)
    send(url, cards(root, evs, f"【回放 {day}】{LABEL.get(root.name, root.name)}", holdings=True))
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
    if "--note" in argv:  # a written summary (notes/discord.md) as one card, for when the owner is away
        if not url:
            print("notify: DC_WEBHOOK is not set")
            return 1
        text = (root / "notes" / "discord.md").read_text().strip()
        title, _, rest = text.partition("\n")
        ok = send(url, [{"title": title.lstrip("# ")[:250], "description": rest.strip()[:4000], "color": GREY}])
        print("notify: note sent" if ok else "notify: note failed")
        return 0 if ok else 1
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
