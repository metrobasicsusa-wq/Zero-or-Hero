"""Post-market fact sheet: the numbers a daily review starts from, as Markdown."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2%}"


def _usd(x: float | None) -> str:
    return "—" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.0f}"


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
        "| | Claude | SPY |",
        "|---|---|---|",
        f"| Day | {_pct(ret(equity, last))} ({_usd(equity - last if equity and last else None)}) | {_pct(ret(bench, bench_prev))} |",
        f"| Since start ({first['date']}) | {_pct(ret(equity, _f(first['equity'])))} | {_pct(ret(bench, bench_first))} |",
        "",
        f"Equity {_usd(equity)} · cash {_usd(_f(acct.get('cash')) if fresh else _f(row['cash']))} · "
        f"generation {cfg['generation']} · snapshot {snap.get('ts', 'none')}",
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
    for e in events:
        if e["kind"] == "order":
            price = f" @ {e['limit_price']}" if e.get("limit_price") else ""
            lines.append(f"- {e['side']} {e['qty']} {option_label(e['symbol'])}{price} ({e['reason']})")
        elif e["kind"] == "close":
            lines.append(f"- close {option_label(e['symbol'])} ({e['reason']})")
        elif e["kind"] == "halt":
            lines.append(f"- HALT: day P/L {e['day_pl']:+.2%}")
    if len(lines) and lines[-1] == "## Today's actions":
        lines.append("- none")

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
        notes.append(f"今日 {_pct(mine)}，SPY {_pct(spy)}，{verdict} {abs(diff):.2%}。")
    elif mine is not None:
        notes.append(f"今日 {_pct(mine)}（SPY 前一日数据不足，暂无对比）。")

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

    orders = snap.get("open_orders") or []
    if orders:
        notes.append(f"⚠️ 收盘时还有 {len(orders)} 笔挂单未完全成交：{', '.join(option_label(o['symbol']) for o in orders)}。")

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
    return f"# 盘后复盘 {day}（Claude）\n\n## 要点（规则自动生成）\n{notes}\n\n{body}"
