"""One page per trading direction of the multi-ledger account (site/f/<direction>.html) and a directory (site/f/index.html).

Owner, 10-21: 「要不每个功能做个 page 可以直接点进去看」. s500.html shows every ledger in one table; these pages show one
direction at a time: what the rule is and whether it is live, stopped or only simulated, its $500 / $1,000 ledgers
next to the plan-only twins that record what a stopped rule would have made, the realized profit over time, what it
holds now, every trade with why it was bought and how it was sold, the recent "why nothing today" lines from the
journal, and the minute-bar backfill of the plan-only picks before the engine recorded them (research/*-plan-results).

A direction is a ledger id without its size: spy0dte-500 and spy0dte-1000 are "spy0dte"; a plan twin goes with the rule
it shadows (gap-plan-500 -> gap, spy0dte-old-plan-500 -> spy0dte, earn-opt-plan-500 -> earn).
Run: python -m hero.feature_pages --out site/f (pages.yml does it after every push).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.exchange_page import esc, styles
from hero.notify import direction, readable

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
SIZE = re.compile(r"-(500|1000)$")
GROUPS = [
    ("末日单（当天到期期权）", ["spy0dte", "spy0dte-near", "qqq0dte", "tsla0dte", "tsla0dte-5x", "nvda0dte",
                          "iwm0dte", "dia0dte", "amd0dte", "amzn0dte", "meta0dte", "msft0dte",
                          "spydip", "oneshot", "vixspike"]),
    ("周内期权", ["wk-trend", "wk-momo", "wk-news", "net"]),
    ("抄底和跳空", ["fadelotto", "gap", "flush", "opengap", "breakgap", "dipweek", "dipweek-hold", "dipweek-take", "pullback", "bottom", "bounce"]),
    ("财报", ["earn", "pead", "relay"]),
    ("狙击和隔夜", ["sniper", "overnight"]),
    ("杠杆 ETF", ["tqqq", "hikerally"]),
    ("跟别人的判断", ["peerc"]),
]
TITLE = {"spy0dte": "SPY 末日单", "spy0dte-near": "SPY 末日单（离现价 0.3%）", "qqq0dte": "QQQ 末日单",
         "tsla0dte": "TSLA 末日单", "tsla0dte-5x": "TSLA 末日单（5 倍止盈）", "nvda0dte": "NVDA 末日单",
         "iwm0dte": "IWM 末日单", "dia0dte": "DIA 末日单", "amd0dte": "AMD 末日单", "amzn0dte": "AMZN 末日单",
         "meta0dte": "META 末日单", "msft0dte": "MSFT 末日单",
         "spydip": "SPY 昨跌后买涨", "oneshot": "一把到", "vixspike": "恐慌反弹", "wk-trend": "周内趋势",
         "wk-momo": "周内动量", "wk-news": "周内新闻", "net": "撒网", "gap": "低开反弹", "flush": "急跌再拉",
         "opengap": "开盘跳空延续", "breakgap": "突破大跳空", "dipweek": "抄底给一周", "dipweek-hold": "抄底给一周（拿满 5 天）",
         "dipweek-take": "抄底给一周（2 倍止盈）", "pullback": "强势股回调", "bottom": "抄底三连跌", "bounce": "板块反弹日龙头", "earn": "财报正股 / 两倍",
         "pead": "财报后漂移", "relay": "接力", "sniper": "开盘狙击", "overnight": "隔夜动量", "tqqq": "TQQQ 均线",
         "hikerally": "加息期快涨", "fadelotto": "高开回落彩票", "peerc": "跟 claude-c 的方向"}
EVENT_KINDS = {"lotto_alert": "提醒", "sleeve_skip": "跳过", "sleeve_plan": "演算", "sleeve_round": "换轮",
               "sleeve_plan_fill": "演算买进", "sleeve_plan_result": "演算结果"}
MAX_EVENTS = 40
SHOW_SPEC = ("und", "offset", "min_move", "take", "half_take", "entry", "side", "after_drop", "n", "mode", "fraction",
             "switch_at", "zero_line", "sym", "ma", "gap", "hold", "unds", "instrument", "stop_entries", "stop_if_losing")


def family(sleeve_id: str) -> str:
    return direction(sleeve_id)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def et(ts: str) -> str:
    """An ISO UTC time from the journal as 'MM-DD HH:MM' New York time."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def short(day: str | None) -> str:
    """'2026-10-12 10:00' -> '10-12 10:00'."""
    return str(day)[5:16] if day else "—"


def multiplier(t: dict) -> int:
    return 100 if t.get("asset") == "option" or re.search(r"\d{6}[CP]\d{8}$", str(t.get("sym", ""))) else 1


def ledger_row(spec: dict, s: dict, plan: bool, prices: dict[str, float]) -> dict:
    """One ledger's numbers. Live open tickets are valued at the broker's last price; plan ones at cost."""
    closed = [t for t in s.get("closed", []) if t.get("sim") or not plan]
    opened = [t for t in s.get("open", []) if t.get("sim") or not plan]
    held = sum(float(t.get("paid") or 0) for t in opened)
    now = None
    if not plan and opened:
        vals = [prices.get(t.get("sym")) for t in opened]
        if all(v is not None for v in vals):
            now = sum(float(t.get("qty") or 0) * v * multiplier(t) for t, v in zip(opened, vals))
    realized = sum(float(t.get("proceeds") or 0) - float(t.get("paid") or 0) for t in closed)
    start, cash = float(s.get("start") or spec.get("start") or 0), float(s.get("cash") or 0)
    value = cash + (now if now is not None else held)
    return {"id": spec["id"], "plan": plan, "start": start, "cash": cash, "held": held, "now": now, "value": value,
            "ret": value / start - 1 if start else 0.0, "realized": round(realized, 2), "trades": len(closed),
            "wins": sum(1 for t in closed if float(t.get("ret") or 0) > 0), "open": len(opened),
            "round": s.get("round", 1), "heroes": s.get("heroes", 0), "zeros": s.get("zeros", 0),
            "last": max([str(t.get("closed") or "") for t in closed] + [str(t.get("opened") or "") for t in opened] + [""])}


def trade_rows(spec: dict, s: dict, plan: bool, prices: dict[str, float]) -> list[dict]:
    out = []
    for state, ts in (("open", s.get("open", [])), ("closed", s.get("closed", []))):
        for t in ts:
            if plan and not t.get("sim"):
                continue
            paid = float(t.get("paid") or 0)
            row = {"sleeve": spec["id"], "plan": plan, "state": state, "sym": t.get("sym", ""),
                   "label": readable(t.get("sym", "")), "qty": t.get("qty"), "cost": t.get("cost"), "paid": paid,
                   "opened": t.get("opened"), "closed": t.get("closed"), "why": t.get("why", ""),
                   "how": t.get("how") or t.get("sell_why") or ""}
            if state == "closed":
                got = float(t.get("proceeds") or 0)
                row.update(got=got, pnl=got - paid, ret=t.get("ret"), price=t.get("exit_price"))
            else:
                p = None if plan else prices.get(t.get("sym"))
                if p is not None:
                    got = float(t.get("qty") or 0) * p * multiplier(t)
                    row.update(got=got, pnl=got - paid, ret=got / paid - 1 if paid else None, price=p)
            out.append(row)
    return sorted(out, key=lambda r: str(r["opened"] or ""), reverse=True)


def curve(trades: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """Ledger id -> [(day, realized profit so far)] at the end of each day it closed something."""
    by: dict[str, dict[str, float]] = {}
    for r in sorted((r for r in trades if r["state"] == "closed" and r.get("closed")), key=lambda r: str(r["closed"])):
        d = by.setdefault(r["sleeve"], {})
        day = str(r["closed"])[:10]
        d[day] = d.get(day, 0.0) + float(r.get("pnl") or 0)
    out = {}
    for sid, days in by.items():
        run, pts = 0.0, []
        for day in sorted(days):
            run += days[day]
            pts.append((day, round(run, 2)))
        out[sid] = pts
    return out


def latest_backfill(root: Path) -> tuple[str, list[dict], dict[str, dict]]:
    """(report name, backfilled rows, each ledger played in order -- research_plan_results.ledgers, when present)."""
    files = sorted((root / "research").glob("*-plan-results.json"))
    if not files:
        return "", [], {}
    d = _load(files[-1], {})
    rows = [r for r in d.get("rows", []) if r.get("src") == "backfill"]
    return files[-1].stem, rows, d.get("ledgers") or {}


def collect(root: Path = ROOT) -> dict:
    cfg = _load(root / "config" / "s500.json", {})
    specs = (cfg.get("sleeves") or {}).get("list") or []
    jdir = root / "journal-s500"
    live = (_load(jdir / "sleeves.json", {}) or {}).get("sleeves") or {}
    plan = (_load(jdir / "sleeves_plan.json", {}) or {}).get("sleeves") or {}
    snap = _load(jdir / "snapshot.json", {}) or {}
    prices = {}
    for p in snap.get("positions") or []:
        try:
            prices[p["symbol"]] = float(p["current_price"])
        except Exception:
            pass
    ids = {s["id"]: family(s["id"]) for s in specs}
    events: dict[str, list[dict]] = {}
    tp = jdir / "trades.jsonl"
    if tp.exists():
        for line in tp.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("kind") == "lotto_alert":            # the lottery scan's alerts belong to its page
                events.setdefault("fadelotto", []).append({"ts": e.get("ts", ""), "kind": e["kind"], "sleeve": "", "why": e.get("why", "")})
            elif e.get("kind") in EVENT_KINDS and e.get("sleeve") in ids:
                events.setdefault(ids[e["sleeve"]], []).append(
                    {"ts": e.get("ts", ""), "kind": e["kind"], "sleeve": e["sleeve"], "why": e.get("why", "")})
    bf_name, bf_rows, bf_led = latest_backfill(root)
    fams: dict[str, dict] = {}
    for spec in specs:
        key = ids[spec["id"]]
        is_plan = bool(spec.get("plan_only"))
        s = (plan if is_plan else live).get(spec["id"]) or {"start": spec.get("start"), "cash": spec.get("start"),
                                                            "open": [], "closed": []}
        f = fams.setdefault(key, {"key": key, "title": TITLE.get(key, key), "specs": [], "ledgers": [], "trades": []})
        f["specs"].append(spec)
        row = ledger_row(spec, s, is_plan, prices)
        if spec.get("stop_if_losing") and not is_plan:
            from hero.sleeves import finished_entries
            n, net = finished_entries(s)
            row["stop_line"] = {"after": int(spec["stop_if_losing"].get("after", 10)), "done": n, "net": net}
        f["ledgers"].append(row)
        f["trades"] += trade_rows(spec, s, is_plan, prices)
    for key, f in fams.items():
        live_specs = [s for s in f["specs"] if not s.get("plan_only")]
        if not live_specs:
            f["status"], f["tone"] = "只演算（不下单）", "plan"
        elif all(s.get("stop_entries") for s in live_specs):
            f["status"], f["tone"] = "实盘已停开新仓", "stopped"
        elif any(r.get("stop_line") and r["stop_line"]["done"] >= r["stop_line"]["after"] and r["stop_line"]["net"] < 0
                 for r in f["ledgers"]):
            f["status"], f["tone"] = "实盘已停（做满次数还是亏）", "stopped"
        else:
            f["status"], f["tone"] = "实盘", "live"
        f["trades"].sort(key=lambda r: str(r["opened"] or ""), reverse=True)
        f["curve"] = curve(f["trades"])
        f["events"] = sorted(events.get(key, []), key=lambda e: e["ts"], reverse=True)[:MAX_EVENTS]
        f["backfill"] = [r for r in bf_rows if family(str(r.get("sleeve", ""))) == key]
        led = {sid: x for sid, x in bf_led.items() if family(sid) == key}
        f["backfill_seq"] = ({"pnl": round(sum(x["pnl"] for x in led.values()), 2), "zeros": sum(x["zeros"] for x in led.values()),
                              "ledgers": led} if led else None)
        lv = [r for r in f["ledgers"] if not r["plan"]]
        pl = [r for r in f["ledgers"] if r["plan"]]
        f["live_pl"] = round(sum(r["value"] - r["start"] for r in lv), 2) if lv else None
        f["plan_pl"] = round(sum(r["value"] - r["start"] for r in pl), 2) if pl else None
        f["n_trades"] = sum(r["trades"] for r in f["ledgers"])
        f["last"] = max([r["last"] for r in f["ledgers"]] + [""])
    order = [k for _, ks in GROUPS for k in ks]
    groups = [(g, [fams[k] for k in ks if k in fams]) for g, ks in GROUPS]
    rest = [f for k, f in fams.items() if k not in order]
    if rest:
        groups.append(("其他", rest))
    common = {k: (cfg.get("sleeves") or {}).get(k) for k in ("fraction", "zero_line", "target")}
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "snapshot_ts": snap.get("ts"),
            "groups": [(g, fs) for g, fs in groups if fs], "families": fams, "common": common, "backfill_name": bf_name}


# ---- rendering ----

def usd(x, d: int = 2) -> str:
    if x is None:
        return "—"
    x = float(x)
    return ("-" if x < 0 else "") + f"${abs(x):,.{d}f}"


def signed(x, d: int = 2) -> str:
    if x is None:
        return "—"
    x = float(x)
    cls = "up" if x > 0 else "down" if x < 0 else ""
    return f'<span class="{cls}">{"+" if x > 0 else "-" if x < 0 else ""}${abs(x):,.{d}f}</span>'


def pct(x) -> str:
    if x is None:
        return "—"
    x = float(x)
    cls = "up" if x > 0 else "down" if x < 0 else ""
    return f'<span class="{cls}">{x:+.0%}</span>'


def ledger_name(r: dict) -> str:
    sid = r["id"] if "id" in r else r["sleeve"]
    if not sid:
        return "—"
    size = SIZE.search(sid)
    return f'{"演算 " if r.get("plan") else ""}{"$" + size.group(1) if size else "样本"}'


def table(head: list[tuple[str, str]], rows: list[list[str]], cls: str = "") -> str:
    """head: (label, column class) with class "" / "num" (right-aligned figures) / "why" (wrapping text).
    cls="stack": on a phone each row becomes a short line with its text below instead of a wide table."""
    th = "".join(f'<th{f" class={c}" if c else ""}>{esc(h)}</th>' for h, c in head)
    body = "".join("<tr>" + "".join(f'<td{f" class={c}" if c else ""}>{v}</td>' for v, (_, c) in zip(r, head)) + "</tr>"
                   for r in rows)
    return f'<div class="table-wrap"><table class="{cls}"><tr>{th}</tr>{body}</table></div>'


def chart(f: dict) -> str:
    """Realized profit so far, one line per ledger ($500 blue, $1,000 orange, plan-only twins dashed). The browser
    draws it at the card's real width (script below), so labels stay readable on a phone."""
    series = [{"name": ledger_name(r), "c": 2 if r["id"].endswith("-1000") else 1, "dash": r["plan"], "pts": f["curve"][r["id"]]}
              for r in f["ledgers"] if f["curve"].get(r["id"])]
    if not series:
        return '<div class="empty">还没有卖出的交易</div>'
    legend = "".join(f'<span><i class="{"dash" if x["dash"] else ""}" style="--c:var(--series-{x["c"]})"></i>'
                     f'{esc(x["name"])}</span>' for x in series)
    data = esc(json.dumps(series, ensure_ascii=False, separators=(",", ":")))
    return (f'<div class="legend">{legend}</div><div class="chart-wrap" data-series="{data}"></div>'
            '<p class="meta">每个点是那天收盘时卖出交易的累计盈亏；点或指着看数字。</p>')


def spec_lines(spec: dict) -> str:
    keep = {k: spec[k] for k in SHOW_SPEC if k in spec}
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in keep.items())


NAV = ('<nav class="meta"><a href="../index.html">Claude 主实验 $100k</a> · <a href="../s500.html">Claude-500 多账本 $50k</a>'
       ' · {here} · <a href="../exchange.html">三方交流</a> · <a href="../earnings.html">财报</a></nav>')
POLL = """<script>
// rebuilt after every trading cycle: reload when a newer build is published
const GEN = document.body.dataset.generated;
setInterval(async () => { try { const t = await (await fetch(location.pathname + "?t=" + Date.now(), {cache: "no-store"})).text();
  const m = t.match(/data-generated="([^"]+)"/); if (m && m[1] !== GEN) location.replace(location.pathname + "?t=" + Date.now()); } catch (e) {} }, 60000);
function niceTicks(lo, hi) {
  const span = Math.max(hi - lo, 1), raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw);
  const out = []; for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + 1e-9; v += step) out.push(v);
  return out;
}
const money = (v) => (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", {maximumFractionDigits: 0});
function drawChart(w) {
  const series = JSON.parse(w.dataset.series), W = w.clientWidth, H = W < 500 ? 200 : 240, L = 56, R = 12, T = 10, B = 26;
  const days = [...new Set(series.flatMap((s) => s.pts.map((p) => p[0])))].sort();
  const vals = series.flatMap((s) => s.pts.map((p) => p[1])).concat([0]);
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals)), lo = ticks[0], hi = ticks[ticks.length - 1];
  const x = (d) => days.length < 2 ? (L + W - R) / 2 : L + (W - L - R) * days.indexOf(d) / (days.length - 1);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="累计已实现盈亏">`;
  for (const v of ticks) svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--${v === 0 ? "axis" : "grid"})"/>` +
    `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${money(v)}</text>`;
  const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor((W - L) / 56))));
  days.forEach((d, i) => { if (i % every === 0 || i === days.length - 1)
    svg += `<text x="${x(d)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--muted)">${d.slice(5)}</text>`; });
  const pts = [];
  for (const s of series) {
    const c = `var(--series-${s.c})`;
    svg += `<polyline points="${s.pts.map((p) => x(p[0]) + "," + y(p[1])).join(" ")}" fill="none" stroke="${c}" stroke-width="2"` +
      `${s.dash ? ' stroke-dasharray="6 4"' : ""} stroke-linejoin="round"/>`;
    for (const p of s.pts) { svg += `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="4" fill="${c}" stroke="var(--surface)" stroke-width="2"/>`;
      pts.push({x: x(p[0]), y: y(p[1]), t: `${s.name} · ${p[0].slice(5)} · 累计 ${money(p[1])}`}); }
  }
  w.innerHTML = svg + "</svg><div class=tooltip></div>";
  const tip = w.querySelector(".tooltip"), el = w.querySelector("svg");
  const show = (ev) => { const r = el.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
    let best = null, bd = 1e9; for (const p of pts) { const d = Math.hypot(p.x - mx, p.y - my); if (d < bd) { bd = d; best = p; } }
    if (!best || bd > 32) { tip.style.display = "none"; return; }
    tip.textContent = best.t; tip.style.display = "block";
    tip.style.left = Math.max(0, Math.min(best.x + 10, W - tip.offsetWidth - 2)) + "px"; tip.style.top = Math.max(0, best.y - 34) + "px"; };
  el.addEventListener("mousemove", show); el.addEventListener("click", show);
  el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
}
const charts = [...document.querySelectorAll(".chart-wrap[data-series]")];
charts.forEach(drawChart);
let lastW = innerWidth; addEventListener("resize", () => { if (innerWidth !== lastW) { lastW = innerWidth; charts.forEach(drawChart); } });
</script>"""
EXTRA_CSS = """
.groups h2 { margin-top: 8px; }
.fgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 20px; }
a.fcard { display: block; text-decoration: none; color: var(--ink); background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px; }
a.fcard:hover { border-color: var(--series-1); }
.fcard h3 { font-size: 15px; margin: 0 0 4px; }
.fcard .desc { color: var(--ink-2); font-size: 12px; margin: 6px 0 8px; display: -webkit-box; -webkit-line-clamp: 3;
  -webkit-box-orient: vertical; overflow: hidden; }
.fcard .nums { display: flex; gap: 16px; font-size: 13px; font-variant-numeric: tabular-nums; flex-wrap: wrap; }
.fcard .nums span { color: var(--ink-2); font-size: 12px; margin-right: 4px; }
.chip.live { background: color-mix(in srgb, var(--up) 18%, transparent); color: var(--up); }
.chip.stopped { background: color-mix(in srgb, var(--down) 15%, transparent); color: var(--down); }
.chip.plan { background: color-mix(in srgb, var(--series-1) 15%, transparent); color: var(--series-1); }
.rule { font-size: 13px; line-height: 1.6; }
.rule p { margin: 4px 0; }
.rule code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--ink-2); word-break: break-all; }
td.why { white-space: normal; min-width: 260px; color: var(--ink-2); font-size: 12px; }
.legend { flex-wrap: wrap; gap: 6px 16px; }
.legend span { white-space: nowrap; }
.legend i { width: 14px; height: 0; border-top: 2px solid var(--c); background: none !important; }
.legend i.dash { border-top-style: dashed; }
.chart-wrap svg { width: 100%; height: auto; overflow: visible; }
@media (max-width: 600px) {
  table.stack tr:first-child { display: none; }
  table.stack tr { display: block; padding: 8px 0; border-bottom: 1px solid var(--grid); }
  table.stack tr:last-child { border-bottom: 0; }
  table.stack td { display: inline; border: 0; padding: 0 6px 0 0; }
  table.stack td.why { display: block; min-width: 0; padding: 4px 0 0; }
}
"""


def page(title: str, here: str, body: str, data: dict) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{styles()}{EXTRA_CSS}</style></head>
<body data-generated="{esc(data['generated'])}"><main>
<header><h1>Zero-or-Hero <span>· {esc(title)}</span></h1>
{NAV.format(here=here)}
<div class="meta">数据：多账本 {esc(et(data['snapshot_ts']) if data.get('snapshot_ts') else '—')}（纽约时间）· 每个交易周期后更新 · 模拟盘，参考记录，不是投资建议</div></header>
{body}
<footer>由 hero/feature_pages.py 从 config/s500.json 和 journal-s500/ 生成。</footer>
</main>{POLL}</body></html>
"""


def directory(data: dict) -> str:
    out = []
    for g, fs in data["groups"]:
        cards = []
        for f in fs:
            desc = f["specs"][0].get("name", "")
            nums = []
            if f["live_pl"] is not None:
                nums.append(f'<div><span>实盘</span>{signed(f["live_pl"], 0)}</div>')
            if f["plan_pl"] is not None:
                nums.append(f'<div><span>演算</span>{signed(f["plan_pl"], 0)}</div>')
            if f["backfill"]:
                won = sum(1 for r in f["backfill"] if float(r.get("ret") or 0) > 0)
                nums.append(f'<div><span>补算</span>{len(f["backfill"])} 笔赚 {won}</div>')
                if f.get("backfill_seq"):
                    z = f["backfill_seq"]["zeros"]
                    nums.append(f'<div><span>按账本</span>{signed(f["backfill_seq"]["pnl"], 0)}{f"（归零 {z}）" if z else ""}</div>')
            nums.append(f'<div><span>交易</span>{f["n_trades"]}</div>')
            nums.append(f'<div><span>最近</span>{esc(short(f["last"]) if f["last"] else "—")}</div>')
            cards.append(f'<a class="fcard" href="{esc(f["key"])}.html"><h3>{esc(f["title"])} '
                         f'<span class="chip {f["tone"]}">{esc(f["status"])}</span></h3>'
                         f'<div class="desc">{esc(desc)}</div><div class="nums">{"".join(nums)}</div></a>')
        out.append(f'<h2>{esc(g)}</h2><div class="fgrid">{"".join(cards)}</div>')
    c = data["common"]
    intro = ('<p class="meta">每个方向有 $500 和 $1,000 两个小账本；停掉的和新想法的规则另有「只演算」账本，不下单，只记会赚多少'
             '（10-22 起由引擎记录）。实盘 / 演算 = 两个账本现值合计减起始（持仓按最新价，演算持仓按成本；归零重开过的只算本轮）；'
             '补算 = 10-22 以前的演算选单按分钟线补算的笔数和赚钱的笔数；交易 = 已卖出的笔数。点进去看规则、每笔交易和每天为什么没做。</p>')
    return page("各方向", "<b>各方向</b>", f'<section class="groups">{intro}{"".join(out)}</section>', data)


def feature(data: dict, f: dict) -> str:
    c = data["common"]
    specs = f["specs"]
    rule = []
    seen = set()
    for s in specs:
        txt = s.get("name", "")
        if txt in seen:
            continue
        seen.add(txt)
        ids = [x["id"] for x in specs if x.get("name") == txt]
        rule.append(f'<p><b>{esc("、".join(ids))}</b>：{esc(txt)}</p>')
        if s.get("note"):
            rule.append(f'<p class="meta">{esc(s["note"])}</p>')
        rule.append(f'<p><code>{esc(spec_lines(s))}</code></p>')
    rule.append(f'<p class="meta">共同规则：每笔押账本的 {c.get("fraction") or 0.5:.0%}（个别方向全仓，见上面 fraction），只买整张；'
                f'低于起始的 {c.get("zero_line") or 0.1:.0%} 算归零、到 {usd(c.get("target") or 10000, 0)} 算成功，都从公共资金重开。</p>')
    for r in f["ledgers"]:
        sl = r.get("stop_line")
        if sl:
            rule.append(f'<p>停止线（{esc(r["id"])}）：已做完 {sl["done"]} / {sl["after"]} 次，合计 {signed(sl["net"])}'
                        f'{"——已停" if sl["done"] >= sl["after"] and sl["net"] < 0 else ""}</p>')
    lrows = [[f'<b>{esc(ledger_name(r))}</b><br><span class="meta">{esc(r["id"])}</span>', usd(r["start"], 0), usd(r["cash"]),
              usd(r["held"]) if r["open"] else "—", usd(r["now"]) if r["now"] is not None else ("按成本" if r["open"] else "—"),
              usd(r["value"]), pct(r["ret"]), signed(r["realized"]), f'{r["trades"]}（赚 {r["wins"]}）',
              f'第 {r["round"]} 轮 · {r["heroes"]} / {r["zeros"]}'] for r in f["ledgers"]]
    ledgers = table([("账本", ""), ("起始", "num"), ("现金", "num"), ("持仓成本", "num"), ("持仓现值", "num"), ("现值", "num"),
                     ("本轮", "num"), ("已实现", "num"), ("卖出笔数", "num"), ("轮次 · 成功 / 归零", "")], lrows)

    def trow(r):
        note = f'买：{esc(r["why"])}' + (f'<br>卖：{esc(r["how"])}' if r["how"] else "")
        return [esc(ledger_name(r)), f'<b>{esc(r["label"] or r["sym"])}</b>', esc(r["qty"] or ""), usd(r["cost"]) if r["cost"] else "—",
                usd(r["paid"]), esc(short(r["opened"])), esc(short(r["closed"])) if r["state"] == "closed" else '<span class="chip">持有中</span>',
                usd(r.get("got")) if r.get("got") is not None else "—", signed(r.get("pnl")) if r.get("pnl") is not None else "—",
                pct(r.get("ret")) if r.get("ret") is not None else "—", note]
    head = [("账本", ""), ("合约", ""), ("数量", "num"), ("买价", "num"), ("花费", "num"), ("买入", ""), ("卖出", ""),
            ("收回 / 现值", "num"), ("盈亏", "num"), ("收益", "num"), ("为什么买、怎么卖的", "why")]
    trades = f["trades"]
    tr = table(head, [trow(r) for r in trades]) if trades else '<div class="empty">还没有交易</div>'
    ev = f["events"]
    erows = [[esc(et(e["ts"])), f'<span class="chip">{esc(EVENT_KINDS[e["kind"]])}</span>', esc(ledger_name({"id": e["sleeve"],
              "plan": e["sleeve"] in {s["id"] for s in specs if s.get("plan_only")}})), esc(e["why"])] for e in ev]
    events = table([("时间", ""), ("动作", ""), ("账本", ""), ("说明", "why")], erows, "stack") if ev else '<div class="empty">没有记录</div>'
    bf = f["backfill"]
    bfh = ""
    if bf:
        brows = [[esc(r.get("day", "")[5:] + " " + r.get("at", "")), esc(ledger_name({"id": r.get("sleeve", ""), "plan": True})),
                  f'<b>{esc(readable(r.get("sym", "")))}</b>', esc(r.get("qty", "")), usd(r.get("paid")), usd(r.get("got")),
                  signed(r.get("pnl")), pct(r.get("ret")), esc(r.get("how", ""))] for r in bf]
        rets = sorted(float(r.get("ret") or 0) for r in bf)
        won = sum(1 for x in rets if x > 0)
        mid = rets[len(rets) // 2] if len(rets) % 2 else (rets[len(rets) // 2 - 1] + rets[len(rets) // 2]) / 2
        seq = f.get("backfill_seq")
        seq_html = ""
        if seq:
            srows = [[esc(ledger_name({"id": sid, "plan": True})), str(x["n"]), signed(x["pnl"]), str(x["zeros"]),
                      usd(x["topups"]), str(x.get("skipped", 0))] for sid, x in sorted(seq["ledgers"].items())]
            seq_html = ('<p>按账本顺序（真账本会怎样）：每笔按那天账本剩下的钱缩放张数，低于起始 10% 归零、补钱重开；'
                        f'合计 {signed(seq["pnl"])}，归零 {seq["zeros"]} 次。</p>'
                        + table([("账本", ""), ("笔数", "num"), ("盈亏", "num"), ("归零", "num"), ("补回", "num"), ("买不起跳过", "num")], srows))
        bfh = (f'<section class="card"><h2>10-22 以前按分钟线补算（{len(bf)} 笔，赚 {won} 笔，中位收益 {pct(mid)}）</h2>'
               f'<p class="meta">{esc(data["backfill_name"])}：引擎开始记录以前的演算选单，用 Alpaca 一分钟成交价按同样的卖出规则补算。'
               f'下面每一笔都按新账本的大小算，金额不能直接相加。</p>{seq_html}'
               + table([("时间", ""), ("账本", ""), ("合约", ""), ("数量", "num"), ("花费", "num"), ("收回", "num"),
                        ("盈亏", "num"), ("收益", "num"), ("怎么卖的", "why")], brows) + "</section>")
    nav_groups = " · ".join(f'<a href="{esc(x["key"])}.html">{esc(x["title"])}</a>' if x["key"] != f["key"] else f'<b>{esc(x["title"])}</b>'
                            for _, fs in data["groups"] for x in fs)
    body = f"""<p class="meta"><a href="index.html">← 全部方向</a> · <span class="chip {f['tone']}">{esc(f['status'])}</span></p>
<section class="card"><h2>规则</h2><div class="rule">{''.join(rule)}</div></section>
<section class="card"><h2>账本</h2>{ledgers}<p class="meta">持仓现值按券商最新价（演算账本按成本）；本轮 = 现值对起始；已实现 = 卖出的收回减花费。</p></section>
<section class="card"><h2>累计已实现盈亏</h2>{chart(f)}</section>
<section class="card"><h2>每笔交易（{len(trades)}）</h2>{tr}</section>
<section class="card"><h2>最近的跳过和演算（为什么没做）</h2>{events}</section>
{bfh}
<section class="card"><h2>其他方向</h2><p class="meta">{nav_groups}</p></section>"""
    return page(f["title"], '<a href="index.html">各方向</a>', body, data)


def write(out: Path, root: Path = ROOT) -> list[Path]:
    data = collect(root)
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "index.html"]
    paths[0].write_text(directory(data))
    for key, f in data["families"].items():
        p = out / f"{key}.html"
        p.write_text(feature(data, f))
        paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site" / "f"))
    paths = write(Path(ap.parse_args().out))
    print(f"wrote {len(paths)} pages to {paths[0].parent}")


if __name__ == "__main__":
    main()
