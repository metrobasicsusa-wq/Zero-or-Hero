"""Post-market fact sheet: the numbers a daily review starts from, as Markdown."""

from __future__ import annotations

import csv
import json
from datetime import datetime, time
from pathlib import Path

from hero.journal import ET, Journal

CLOSE = time(16, 0)


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2%}"


def _usd(x: float | None) -> str:
    return "—" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.0f}"


def _snapshot(journal: Path) -> dict:
    path = journal / "snapshot.json"
    return json.loads(path.read_text()) if path.exists() else {}


def snapshot_as_of(journal: Path) -> datetime | None:
    ts = _snapshot(journal).get("ts")
    return datetime.fromisoformat(ts).astimezone(ET) if ts else None


def is_final(journal: Path, day: str) -> bool:
    """A review is final only when the snapshot was taken after that day's close."""
    t = snapshot_as_of(journal)
    return bool(t) and (t.date().isoformat() > day or (t.date().isoformat() == day and t.time() >= CLOSE))


def decisions(journal: Path, day: str) -> list[dict]:
    """Each order/close decision of the day, joined to its broker acknowledgement and fills.

    Decisions are journaled before the broker call with an intent_key; the order_ack event maps
    that to the hashed broker order id (order_key), which fills.jsonl also carries."""
    path = journal / "trades.jsonl"
    if not path.exists():
        return []
    events = [json.loads(line) for line in open(path)]
    acks = {e["intent_key"]: e for e in events if e["kind"] in ("order_ack", "order_error")}
    fills = Journal(journal).fills(day)
    out = []
    for e in events:
        if e["kind"] not in ("order", "close") or not e["ts"].startswith(day) or e.get("dry_run"):
            continue
        ack = acks.get(e.get("intent_key")) or {}
        # Older entries (before intent keys) carried order_key directly on the event.
        order_key = ack.get("order_key") or e.get("order_key")
        got = [f for f in fills if order_key and f.get("order_key") == order_key]
        qty = sum(float(f["qty"]) for f in got)
        out.append({
            "kind": e["kind"], "ts": e["ts"], "symbol": e["symbol"], "side": e.get("side", "close"),
            "qty": e.get("qty"), "type": e.get("type"), "limit_price": e.get("limit_price"),
            "reason": e["reason"], "why": e.get("why", ""), "evidence": e.get("evidence") or {},
            "dry_run": e.get("dry_run", False),
            "order_key": order_key, "broker_status": ack.get("status"), "error": ack.get("error"),
            "filled_qty": qty, "fill_vwap": sum(float(f["qty"]) * float(f["price"]) for f in got) / qty if qty else None,
            "fill_keys": [f["execution_key"] for f in got],
            "legacy": "intent_key" not in e and not order_key,
        })
    return out


def rehearsal(journal: Path, cfg: dict, day: str) -> dict | None:
    """Before live_from the bot runs dry: what the last cycle of the day would have done."""
    if not day < cfg.get("live_from", ""):
        return None
    path = journal / "trades.jsonl"
    events = [json.loads(l) for l in open(path)] if path.exists() else []
    dry = [e for e in events if e["ts"].startswith(day) and e.get("dry_run")
           and e["kind"] in ("order", "close", "option_skip", "attempt_end")]
    cycles = sorted({e["ts"] for e in dry})
    last = [e for e in dry if cycles and e["ts"] == cycles[-1]]
    skips = [e["why"] for e in dry if e["kind"] == "option_skip"]
    return {"live_from": cfg["live_from"], "cycles": len(cycles), "last_cycle": cycles[-1] if cycles else None,
            "last_cycle_events": [{k: e.get(k) for k in ("kind", "symbol", "side", "qty", "notional", "type",
                                                            "limit_price", "reason", "why")} for e in last],
            "option_skips": len(skips), "last_option_skip": skips[-1] if skips else None}


def option_label(sym: str) -> str:
    if len(sym) <= 15:
        return sym
    t = sym[-15:]
    return f"{sym[:-15]} {t[2:4]}/{t[4:6]} {'call' if t[6] == 'C' else 'put'} {int(t[7:]) / 1000:g}"


def facts(journal: Path, cfg: dict, day: str) -> str | None:
    """Markdown facts for one trading day, or None if the bot did not trade that day."""
    eq_path = journal / "equity.csv"
    rows = list(csv.DictReader(open(eq_path))) if eq_path.exists() else []
    idx = next((i for i, r in enumerate(rows) if r["date"] == day), None)
    if idx is None:
        return None
    row, prev, first = rows[idx], rows[idx - 1] if idx else None, rows[0]

    snap_path = journal / "snapshot.json"
    snap = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    acct = snap.get("account") or {}
    fresh = snap.get("ts", "").startswith(day)
    equity = _f(acct.get("equity")) if fresh else _f(row["equity"])
    last = _f(acct.get("last_equity")) if fresh else (_f(prev["equity"]) if prev else None)

    def ret(a, b):
        return a / b - 1 if a is not None and b else None

    bench, bench_prev, bench_first = _f(row.get("benchmark")), _f(prev.get("benchmark")) if prev else None, _f(first.get("benchmark"))
    lines = [
        f"# Facts for {day}",
        "",
        f"| | {cfg.get('name', 'Claude')} | {cfg['regime_symbol']} |",
        "|---|---|---|",
        f"| Day | {_pct(ret(equity, last))} ({_usd(equity - last if equity and last else None)}) | {_pct(ret(bench, bench_prev))} |",
        f"| Since start ({first['date']}) | {_pct(ret(equity, _f(first['equity'])))} | {_pct(ret(bench, bench_first))} |",
        "",
        f"Equity {_usd(equity)} · cash {_usd(_f(acct.get('cash')) if fresh else _f(row['cash']))} · "
        f"generation {cfg['generation']}",
        "",
        f"Status: **{'FINAL (post-close)' if is_final(journal, day) else 'PRELIMINARY (intraday)'}** · "
        f"as of {snapshot_as_of(journal).strftime('%Y-%m-%d %H:%M ET') if snapshot_as_of(journal) else 'unknown'}",
        "",
        "## Today's actions",
    ]
    events = []
    trades_path = journal / "trades.jsonl"
    if trades_path.exists():
        for line in open(trades_path):
            e = json.loads(line)
            if e["ts"].startswith(day) and not e.get("dry_run"):
                events.append(e)
    targets = [e for e in events if e["kind"] == "targets"]
    if targets:
        lines.append(f"- targets: {', '.join(f'{s} {w:.0%}' for s, w in targets[-1]['weights'].items()) or 'cash'}")
    for o in decisions(journal, day):
        ev = o["evidence"]
        quote = (f" · quote bid {ev['bid']} / ask {ev['ask']}, spread {ev['spread_pct_of_mid']:.1%} of mid"
                 if ev.get("bid") is not None and ev.get("spread_pct_of_mid") is not None else "")
        if o["filled_qty"]:
            outcome = f" → filled {o['filled_qty']:g} @ {o['fill_vwap']:.2f}"
        elif o["error"]:
            outcome = f" → REJECTED/ERROR: {o['error'][:80]}"
        elif o["order_key"]:
            outcome = f" → accepted ({o['broker_status']}), no fills linked"
        else:
            outcome = (" → (journaled before order linking existed)" if o["legacy"]
                       else " → no broker acknowledgement recorded")
        why = f"\n  - 理由：{o['why']}" if o["why"] else ""
        if o["kind"] == "close":
            lines.append(f"- close {option_label(o['symbol'])} ({o['reason']}){outcome}{why}")
        else:
            price = f" @ {o['limit_price']}" if o.get("limit_price") else ""
            lines.append(f"- {o['side']} {o['qty']} {option_label(o['symbol'])}{price} ({o['reason']}){quote}{outcome}{why}")
    for e in events:
        if e["kind"] == "halt":
            lines.append(f"- HALT: day P/L {e['day_pl']:+.2%}")
    if len(lines) and lines[-1] == "## Today's actions":
        lines.append("- none")

    reh = rehearsal(journal, cfg, day)
    if reh:
        lines += ["", f"## 演练（dry-run，未下单；{reh['live_from']} 起真实下单）",
                  f"- 今天共 {reh['cycles']} 轮演练，下面是最后一轮（{reh['last_cycle'] or '无'}）会做的事："]
        for e in reh["last_cycle_events"]:
            size = f"${e['notional']}" if e.get("notional") else (e.get("qty") or "")
            what = "期权未买" if e["kind"] == "option_skip" else f"{e.get('side') or 'close'} {size} {option_label(e['symbol'])}"
            lines.append(f"- {what}（{e.get('reason') or e['kind']}）\n  - 理由：{e.get('why') or ''}")
        if not reh["last_cycle_events"]:
            lines.append("- 无动作")

    fills = Journal(journal).fills(day)
    lines += ["", f"## Broker-confirmed fills ({len(fills)})"]
    if fills:
        lines += ["| Time (ET) | Symbol | Side | Qty | Price |", "|---|---|---|---|---|"]
        for f in fills:
            t = datetime.fromisoformat(f["transaction_time"].replace("Z", "+00:00")).astimezone(ET)
            lines.append(f"| {t:%H:%M:%S} | {option_label(f['symbol'])} | {f['side']} | {f['qty']} | {float(f['price']):.2f} |")
    else:
        lines.append("- none recorded")

    lines += ["", "## Positions"]
    positions = snap.get("positions") or []
    if positions:
        lines += ["| Symbol | Qty | Avg | Last | Value | P/L | P/L % |", "|---|---|---|---|---|---|---|"]
        for p in sorted(positions, key=lambda p: -float(p["market_value"])):
            lines.append(
                f"| {option_label(p['symbol'])} | {p['qty']} | {float(p['avg_entry_price']):.2f} | "
                f"{float(p['current_price']):.2f} | {_usd(_f(p['market_value']))} | {_usd(_f(p['unrealized_pl']))} | "
                f"{_pct(_f(p['unrealized_plpc']))} |")
    else:
        lines.append("- none")

    orders = snap.get("open_orders") or []
    lines += ["", "## Unfilled orders"]
    lines += [f"- {o['side']} {o['qty']} {option_label(o['symbol'])} {o['type']}"
              f"{' @ ' + o['limit_price'] if o.get('limit_price') else ''} ({o['status']})" for o in orders] or ["- none"]
    return "\n".join(lines) + "\n"


TECH = {"QQQ", "XLK", "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "GOOGL", "META"}
CONCENTRATION_ALERT = 0.6


def insights(journal: Path, cfg: dict, day: str) -> list[str]:
    """Rule-based observations (Chinese) to sit above the fact sheet."""
    rows = list(csv.DictReader(open(journal / "equity.csv")))
    idx = next(i for i, r in enumerate(rows) if r["date"] == day)
    row, prev = rows[idx], rows[idx - 1] if idx else None
    snap_path = journal / "snapshot.json"
    snap = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    acct = snap.get("account") or {}
    equity = _f(acct.get("equity")) or _f(row["equity"])
    last = _f(acct.get("last_equity")) or (_f(prev["equity"]) if prev else None)
    notes = []

    mine = equity / last - 1 if equity and last else None
    b, bp = _f(row.get("benchmark")), _f(prev.get("benchmark")) if prev else None
    spy = b / bp - 1 if b and bp else None
    if mine is not None and spy is not None:
        diff = mine - spy
        verdict = "跑赢" if diff > 0.0005 else "跑输" if diff < -0.0005 else "持平"
        notes.append(f"今日 {_pct(mine)}，{cfg['regime_symbol']} {_pct(spy)}，{verdict} {abs(diff):.2%}。")
    elif mine is not None:
        notes.append(f"今日 {_pct(mine)}（{cfg['regime_symbol']} 前一日数据不足，暂无对比）。")
    reh = rehearsal(journal, cfg, day)
    if reh:
        notes.append(f"演练阶段：今天 {reh['cycles']} 轮只记录不下单，{reh['live_from']} 起真实下单。")
        if reh["last_option_skip"]:
            notes.append(f"期权：{reh['option_skips']} 轮没买到合格合约。最后一次：{reh['last_option_skip']}")

    positions = snap.get("positions") or []
    if positions:
        ranked = sorted(positions, key=lambda p: float(p["unrealized_pl"]))
        worst, best = ranked[0], ranked[-1]
        if float(best["unrealized_pl"]) > 0:
            notes.append(f"浮盈最多：{option_label(best['symbol'])} {_usd(_f(best['unrealized_pl']))}（{_pct(_f(best['unrealized_plpc']))}）。")
        if float(worst["unrealized_pl"]) < 0:
            notes.append(f"浮亏最多：{option_label(worst['symbol'])} {_usd(_f(worst['unrealized_pl']))}（{_pct(_f(worst['unrealized_plpc']))}）。")
        total = sum(abs(float(p["market_value"])) for p in positions)
        tech = sum(abs(float(p["market_value"])) for p in positions
                   if (p["symbol"][:-15] if len(p["symbol"]) > 15 else p["symbol"]) in TECH)
        if total and tech / total >= CONCENTRATION_ALERT:
            notes.append(f"⚠️ 科技相关持仓占 {tech / total:.0%}，板块集中度高。")
        opts = sum(abs(float(p["market_value"])) for p in positions if p.get("asset_class") == "us_option")
        if equity and opts:
            notes.append(f"期权市值占净值 {opts / equity:.1%}（上限 {cfg['options']['allocation']:.0%}）。")

    all_orders = snap.get("open_orders") or []
    orders = [o for o in all_orders if not o.get("protective_stop")]
    if orders:
        notes.append(f"⚠️ 收盘时还有 {len(orders)} 笔挂单未完全成交：{', '.join(option_label(o['symbol']) for o in orders)}。")
    stocks = [p["symbol"] for p in positions if p.get("asset_class") == "us_equity"]
    if stocks:
        covered = {o["symbol"] for o in all_orders if o.get("protective_stop")}
        missing = [s for s in stocks if s not in covered]
        notes.append(f"券商端止损单覆盖 {len(stocks) - len(missing)}/{len(stocks)} 只股票"
                     + (f"；⚠️ 缺少：{', '.join(missing)}。" if missing else "。"))

    trades_path = journal / "trades.jsonl"
    if trades_path.exists():
        for line in open(trades_path):
            e = json.loads(line)
            if e["ts"].startswith(day) and e["kind"] == "halt" and not e.get("dry_run"):
                notes.append(f"⚠️ 今日触发了停止开仓（当日 {e['day_pl']:+.2%}）。")
                break
    return notes


def report(journal: Path, cfg: dict, day: str) -> str | None:
    body = facts(journal, cfg, day)
    if body is None:
        return None
    notes = "\n".join(f"- {n}" for n in insights(journal, cfg, day)) or "- 无"
    kind = "盘后复盘" if is_final(journal, day) else "盘中预审（非收盘数据）"
    if rehearsal(journal, cfg, day):
        kind += "·演练"
    return f"# {kind} {day}（{cfg.get('name', 'Claude')}）\n\n## 要点（规则自动生成）\n{notes}\n\n{body}"


def export(journal: Path, cfg: dict, day: str) -> dict | None:
    """Machine-readable daily summary for exchange/claude/, mirroring Codex's exchange fields."""
    rows = list(csv.DictReader(open(journal / "equity.csv"))) if (journal / "equity.csv").exists() else []
    idx = next((i for i, r in enumerate(rows) if r["date"] == day), None)
    if idx is None:
        return None
    row, prev = rows[idx], rows[idx - 1] if idx else None
    snap = _snapshot(journal)
    acct = snap.get("account") or {}
    as_of = snapshot_as_of(journal)
    fresh = bool(as_of) and as_of.date().isoformat() == day
    equity = _f(acct.get("equity")) if fresh else _f(row["equity"])
    previous = _f(acct.get("last_equity")) if fresh else (_f(prev["equity"]) if prev else None)
    fills = Journal(journal).fills(day)
    return {
        "schema_version": "1.0",
        "producer": "claude",
        "experiment": cfg.get("name", "Claude"),
        "mode": "alpaca_paper",
        "currency": "USD",
        "trading_date": day,
        "timezone": "America/New_York",
        "status": "post_market_review" if is_final(journal, day) else "intraday_snapshot",
        "as_of": as_of.isoformat() if as_of else None,
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "scope": "US equities and long single-leg options; momentum strategy, daily rebalance, 10-minute cycle",
        "equity": {
            "current_usd": equity,
            "previous_day_usd": previous,
            "previous_day_basis": "broker last_equity (prior close)" if fresh else "journal equity.csv prior row",
            "day_return": equity / previous - 1 if equity and previous else None,
            "since_start_return": equity / _f(rows[0]["equity"]) - 1 if equity and rows else None,
            "start_date": rows[0]["date"] if rows else None,
            "external_cash_flows_usd": 0,
        },
        "benchmark": {"symbol": cfg["regime_symbol"], "close": _f(row.get("benchmark")),
                      "previous_close": _f(prev.get("benchmark")) if prev else None},
        "orders": decisions(journal, day),
        "orders_basis": "decisions journaled before submission, with the quote/reference evidence they used, "
                        "joined to broker acks and fills via hashed order ids",
        "fills": fills,
        "fill_count": len(fills),
        "positions": snap.get("positions") or [],
        "positions_basis": f"broker positions at {as_of.isoformat() if as_of else 'unknown'}",
        "open_orders": snap.get("open_orders") or [],
        "strategy_generation": cfg["generation"],
        "notes": insights(journal, cfg, day),
        "rehearsal": rehearsal(journal, cfg, day),
        "replies": [],
    }
