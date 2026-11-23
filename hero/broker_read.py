"""Six broker and market-data reads we did not use before (2026-11-23, owner: 「可以，先接第一组」).

Read-only: every request here is a GET. Nothing in this module can place, change or cancel an order, and the
trading code does not import it. What each read is for:
  auctions     the official opening and closing auction prices (/v2/stocks/auctions). Scoring has used the
               first minute bar as the open by mistake three times; the open is what against_gap, the follow
               card and the earnings reactions are scored on.
  activities   the account's non-fill records: option expiries, exercises, assignments, dividends, fees
               (/v2/account/activities). The goal book now infers expiries; the broker says what happened.
  history      the account's daily equity curve (/v2/account/portfolio/history).
  quotes       for the options the account holds: the snapshot's quote beside the latest-quote and latest-trade
               endpoints, with their ages (claude-b 11-20: the snapshot may be 15 minutes old with a fresh stamp).
  corporate    splits and dividends (/v1/corporate-actions): which side of a minute / daily comparison is adjusted.
  levels       the options level and the account settings (/v2/account, /v2/account/configurations).

Writes data/broker/<account>.json (one per account), data/broker/auctions.json and data/broker/corporate.json, and
research/broker/<day>.md. Never writes an account number, an order id or an activity id: activities keep a hashed
key only, and every string goes through journal.redact. The keys live in GitHub Secrets; run it from broker.yml.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.journal import execution_key, redact

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data" / "broker", ROOT / "research" / "broker"
ET = ZoneInfo("America/New_York")
ACCOUNTS = {"main": "主账户", "s500": "多账本"}
LOOKBACK = 60                     # days of auctions on a first run
PAGE = 100
# Trade conditions of the auction prints (CTA and UTP): Q = the market center's official open, O = opening print;
# M = the official close, 6 = closing print. The listing exchange's official print is the open / close.
OPEN_CODES, CLOSE_CODES = ("Q", "O"), ("M", "6")
PRIMARY = {"ARCA": "P", "NYSE": "N", "NASDAQ": "Q", "AMEX": "A", "BATS": "Z"}
KEEP = ("activity_type", "date", "symbol", "qty", "net_amount", "per_share_amount", "description", "status")
LEVEL_KEYS = ("options_trading_level", "options_approved_level")
CONFIG_KEYS = ("max_options_trading_level", "no_shorting", "pdt_check", "dtbp_check", "fractional_trading", "suspend_trade",
               "max_margin_multiplier")
ACTION_TYPES = "forward_split,reverse_split,cash_dividend,stock_dividend"


def get_t(a, path: str, params: dict | None = None):
    return a._t("GET", path, params=params or {})


def get_d(a, path: str, params: dict):
    return a._d(path, params)


# --- auctions ---------------------------------------------------------------------------------------------------

def auctions(a, symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """{symbol: [{d, o: [...], c: [...]}, ...]} from /v2/stocks/auctions, every page."""
    out, token = {}, None
    while True:
        p = {"symbols": ",".join(symbols), "start": start, "end": end, "limit": 10000}
        if token:
            p["page_token"] = token
        page = get_d(a, "/v2/stocks/auctions", p) or {}
        for s, days in (page.get("auctions") or {}).items():
            out.setdefault(s, []).extend(days or [])
        token = page.get("next_page_token")
        if not token:
            return out


def official(prints: list[dict], primary: str | None, codes: tuple[str, ...]) -> dict | None:
    """The listing exchange's official print: its first code in `codes` order, then any exchange's, else None."""
    for exchanges in ((primary,) if primary else (), None):
        for code in codes:
            for p in prints or []:
                if p.get("c") == code and p.get("p") and (exchanges is None or p.get("x") in exchanges):
                    return p
    return None


def auction_rows(raw: dict[str, list[dict]], primaries: dict[str, str]) -> dict[str, dict[str, dict]]:
    """{symbol: {day: {open, close, open_x, open_c, close_x, close_c}}}; a side with no official print is None."""
    out = {}
    for sym, days in raw.items():
        prim = PRIMARY.get(primaries.get(sym, ""))
        for d in days:
            day = (d.get("d") or "")[:10]
            if not day:
                continue
            o, c = official(d.get("o"), prim, OPEN_CODES), official(d.get("c"), prim, CLOSE_CODES)
            out.setdefault(sym, {})[day] = {"open": o and o["p"], "open_x": o and o.get("x"), "open_c": o and o.get("c"),
                                            "close": c and c["p"], "close_x": c and c.get("x"), "close_c": c and c.get("c")}
    return out


def merge_auctions(old: dict, new: dict) -> dict:
    out = {s: dict(v) for s, v in old.items()}
    for s, days in new.items():
        out.setdefault(s, {}).update(days)
    return {s: dict(sorted(v.items())) for s, v in sorted(out.items())}


def official_open(sym: str, day: str, path: Path | None = None) -> float | None:
    """The stored official open, for scoring: None when the day is not stored yet."""
    p = path or DATA / "auctions.json"
    if not p.exists():
        return None
    return (json.loads(p.read_text()).get(sym) or {}).get(day, {}).get("open")


# --- activities, history, levels --------------------------------------------------------------------------------

def activities(a, after: str, until: str | None = None) -> list[dict]:
    """Every activity (fills included) after `after`, oldest first."""
    rows, token = [], None
    while True:
        p = {"after": after, "direction": "asc", "page_size": PAGE}
        if until:
            p["until"] = until
        if token:
            p["page_token"] = token
        page = get_t(a, "/v2/account/activities", p) or []
        rows += page
        if len(page) < PAGE:
            return rows
        token = page[-1]["id"]


def slim(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """The non-fill records with ids dropped (a hashed key kept), and a count of every type."""
    keep, counts = [], {}
    for r in rows:
        kind = r.get("activity_type") or "?"
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "FILL":
            continue
        row = {k: redact(r[k]) for k in KEEP if r.get(k) not in (None, "")}
        row["key"] = execution_key(str(r.get("id", json.dumps(row, sort_keys=True))))
        keep.append(row)
    return keep, dict(sorted(counts.items()))


def merge_activities(old: list[dict], new: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in old + new:
        if r["key"] not in seen:
            seen.add(r["key"])
            out.append(r)
    return sorted(out, key=lambda r: (r.get("date", ""), r["key"]))


def history(a, period: str = "3M") -> list[dict]:
    """[{day, equity, pl, pl_pct}] from the daily portfolio history."""
    h = get_t(a, "/v2/account/portfolio/history", {"period": period, "timeframe": "1D"}) or {}
    ts, eq, pl, pct = (h.get(k) or [] for k in ("timestamp", "equity", "profit_loss", "profit_loss_pct"))
    out = []
    for i, t in enumerate(ts):
        if i < len(eq) and eq[i]:
            out.append({"day": datetime.fromtimestamp(t, ET).date().isoformat(), "equity": eq[i],
                        "pl": pl[i] if i < len(pl) else None, "pl_pct": pct[i] if i < len(pct) else None})
    return out


def levels(a) -> dict:
    acct, cfg = a.account() or {}, get_t(a, "/v2/account/configurations") or {}
    return {**{k: acct.get(k) for k in LEVEL_KEYS}, **{k: cfg.get(k) for k in CONFIG_KEYS}}


# --- option quotes ---------------------------------------------------------------------------------------------

def _stamp(stamp: str | None) -> str | None:
    """'2026-11-23T17:40:01.123456789Z' -> a parseable ISO string."""
    if not stamp:
        return None
    s = stamp.replace("Z", "")
    if "." in s:
        head, frac = s.split(".", 1)
        frac = "".join(ch for ch in frac if ch.isdigit())[:6]
        s = f"{head}.{frac}" if frac else head
    return s + "+00:00" if "+" not in s[10:] and "-" not in s[10:] else s


def age(stamp: str | None, now: datetime) -> float | None:
    """Minutes from the stamp to now."""
    s = _stamp(stamp)
    return None if s is None else round((now - datetime.fromisoformat(s)).total_seconds() / 60, 1)


def quotes(a, symbols: list[str], now: datetime | None = None) -> dict:
    """The snapshot quote beside the latest-quote and latest-trade endpoints, per held option; and whether the
    'opra' feed (the full one) answers on this plan."""
    now = now or datetime.now(timezone.utc)
    if not symbols:
        return {"rows": [], "opra": None}
    sym = ",".join(symbols[:100])
    snap = (get_d(a, "/v1beta1/options/snapshots", {"symbols": sym, "feed": "indicative"}) or {}).get("snapshots") or {}
    lq = (get_d(a, "/v1beta1/options/quotes/latest", {"symbols": sym, "feed": "indicative"}) or {}).get("quotes") or {}
    lt = (get_d(a, "/v1beta1/options/trades/latest", {"symbols": sym, "feed": "indicative"}) or {}).get("trades") or {}
    try:
        get_d(a, "/v1beta1/options/quotes/latest", {"symbols": symbols[0], "feed": "opra"})
        opra = "answers"
    except Exception as e:                                     # the plan may not include it
        opra = f"refused ({getattr(e, 'status', None) or type(e).__name__})"
    rows = []
    for s in symbols[:100]:
        sq = (snap.get(s) or {}).get("latestQuote") or {}
        st = (snap.get(s) or {}).get("latestTrade") or {}
        q, t = lq.get(s) or {}, lt.get(s) or {}
        rows.append({"symbol": s, "snap_bid": sq.get("bp"), "snap_ask": sq.get("ap"), "snap_quote_age": age(sq.get("t"), now),
                     "snap_trade_age": age(st.get("t"), now), "bid": q.get("bp"), "ask": q.get("ap"),
                     "quote_age": age(q.get("t"), now), "trade": t.get("p"), "trade_age": age(t.get("t"), now),
                     "same_quote": sq.get("t") == q.get("t") and sq.get("bp") == q.get("bp") and sq.get("ap") == q.get("ap")})
    return {"at": now.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"), "rows": rows, "opra": opra}


# --- corporate actions -----------------------------------------------------------------------------------------

def corporate(a, symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """{kind: [action, ...]} for splits and dividends, a year at a time."""
    out = {}
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        s, e = max(start, f"{y}-01-01"), min(end, f"{y}-12-31")
        token = None
        while True:
            p = {"symbols": ",".join(symbols), "types": ACTION_TYPES, "start": s, "end": e, "limit": 1000}
            if token:
                p["page_token"] = token
            page = get_d(a, "/v1/corporate-actions", p) or {}
            for kind, rows in (page.get("corporate_actions") or {}).items():
                out.setdefault(kind, []).extend(redact(rows or []))
            token = page.get("next_page_token")
            if not token:
                break
    return out


def splits(actions: dict[str, list[dict]]) -> list[dict]:
    """[{symbol, day, ratio}] for every split, oldest first; ratio = new shares per old share."""
    out = []
    for kind in ("forward_splits", "reverse_splits"):
        for r in actions.get(kind) or []:
            new, old = r.get("new_rate"), r.get("old_rate")
            if new and old:
                out.append({"symbol": r.get("symbol"), "day": r.get("ex_date") or r.get("process_date"), "ratio": new / old})
    return sorted(out, key=lambda r: (r["day"] or "", r["symbol"] or ""))


# --- symbols, files, report ------------------------------------------------------------------------------------

def watch_symbols(root: Path = ROOT) -> list[str]:
    """The stock pool, the scout's funds and the earnings extra watch list."""
    from hero import scout
    syms = set(json.loads((root / "config" / "strategy.json").read_text())["universe"]) | set(scout.SECTORS)
    p = root / "research" / "earnings-forecast" / "extra_watch.json"
    if p.exists():
        w = json.loads(p.read_text()).get("symbols") or []
        syms |= {x if isinstance(x, str) else x.get("symbol") for x in w} - {None}
    return sorted(syms)


def load(path: Path, empty):
    return json.loads(path.read_text()) if path.exists() else empty


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n")


def run_account(a, name: str, today: date, data: Path = DATA) -> dict:
    """The four account reads, merged into data/broker/<name>.json. A read that fails is recorded, not fatal."""
    path, errors = data / f"{name}.json", {}
    old = load(path, {})
    rec = {"account": name, "updated": today.isoformat()}
    try:
        rec["levels"] = levels(a)
    except Exception as e:
        errors["levels"] = redact(str(e))[:200]
    try:
        after = (today - timedelta(days=400 if not old.get("activities") else 10)).isoformat()
        keep, counts = slim(activities(a, after))
        rec["activities"], rec["activity_counts"] = merge_activities(old.get("activities", []), keep), counts
    except Exception as e:
        errors["activities"] = redact(str(e))[:200]
        rec["activities"] = old.get("activities", [])
    try:
        hist = {r["day"]: r for r in old.get("history", [])} | {r["day"]: r for r in history(a, "1A")}
        rec["history"] = [hist[d] for d in sorted(hist)]
    except Exception as e:
        errors["history"] = redact(str(e))[:200]
        rec["history"] = old.get("history", [])
    try:
        clock = a.clock() or {}
        held = sorted(p["symbol"] for p in (a.positions() or []) if p.get("asset_class") == "us_option")
        rec["quotes"] = quotes(a, held) if clock.get("is_open") else {"rows": [], "note": "收盘后不查报价新旧"}
    except Exception as e:
        errors["quotes"] = redact(str(e))[:200]
    rec["errors"] = errors
    save(path, rec)
    return rec


def recent_end(now: datetime | None = None) -> str:
    """20 minutes ago, RFC3339: the free data plan refuses SIP data from the last 15 minutes."""
    return ((now or datetime.now(timezone.utc)) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_market(a, today: date, data: Path = DATA, symbols: list[str] | None = None) -> dict:
    """Auctions since the last stored day (or LOOKBACK days) and splits / dividends since 2015."""
    symbols, errors = symbols or watch_symbols(), {}
    old = load(data / "auctions.json", {})
    last = max((d for v in old.values() for d in v), default=None)
    start = (date.fromisoformat(last) - timedelta(days=3)).isoformat() if last else (today - timedelta(days=LOOKBACK)).isoformat()
    try:
        prim = {}
        for s in symbols:
            try:
                prim[s] = (a.asset(s) or {}).get("exchange", "")
            except Exception:
                prim[s] = ""
        new = auction_rows(auctions(a, symbols, start, recent_end()), prim)
        save(data / "auctions.json", merge_auctions(old, new))
    except Exception as e:
        errors["auctions"] = redact(str(e))[:200]
    try:
        acts = corporate(a, symbols, "2015-01-01", today.isoformat())
        save(data / "corporate.json", {"updated": today.isoformat(), "symbols": symbols, "splits": splits(acts),
                                       "dividends": sorted(({"symbol": r.get("symbol"), "ex_date": r.get("ex_date"), "rate": r.get("rate")}
                                                            for r in acts.get("cash_dividends") or []),
                                                           key=lambda r: (r["ex_date"] or "", r["symbol"] or "")),
                                       "counts": {k: len(v) for k, v in acts.items()}})
    except Exception as e:
        errors["corporate"] = redact(str(e))[:200]
    save(data / "market-errors.json", {"updated": today.isoformat(), "errors": errors})
    return errors


def px(x) -> str:
    return "" if x is None else f"{x:.2f}"


def money(x) -> str:
    return "—" if x is None else (f"−${abs(float(x)):,.2f}" if float(x) < 0 else f"${float(x):,.2f}")


def compare_opens(auc: dict, daily: dict[str, dict[str, float]], first_minute: dict[str, dict[str, float]]) -> list[dict]:
    """Per symbol and day: the official open beside the daily bar's open and the first minute bar's open."""
    out = []
    for sym, days in auc.items():
        for day, r in days.items():
            if r.get("open") is None:
                continue
            d, m = (daily.get(sym) or {}).get(day), (first_minute.get(sym) or {}).get(day)
            out.append({"symbol": sym, "day": day, "official": r["open"], "daily": d, "minute": m,
                        "daily_gap": None if d is None else d / r["open"] - 1, "minute_gap": None if m is None else m / r["open"] - 1})
    return out


def report(today: date, accounts: dict[str, dict], market_errors: dict, auc: dict, corp: dict, opens: list[dict]) -> str:
    out = [f"# 券商的六个只读接口（{today.isoformat()}）", "",
           "模拟盘，只读：这里的请求全是查询，不下单、不改单。账户号、订单号、流水编号都不写。"
           "用户 11-23「可以，先接第一组」。", ""]
    out += ["## 期权等级和账户设置", "", "| 账户 | 期权等级（现在 / 批准 / 上限） | 不许开空 | 日内交易检查 | 碎股 |", "|---|---|---|---|---|"]
    for name, rec in accounts.items():
        lv = rec.get("levels") or {}
        out.append(f"| {ACCOUNTS.get(name, name)} | {lv.get('options_trading_level')} / {lv.get('options_approved_level')} / "
                   f"{lv.get('max_options_trading_level')} | {lv.get('no_shorting')} | {lv.get('pdt_check')} | {lv.get('fractional_trading')} |")
    out += ["", "期权等级：1 = 备兑和现金担保卖看跌，2 = 买期权，3 = 价差（多腿单要 3）。", ""]
    out += ["## 流水里除了成交以外的记录（到期、行权、指派、分红、费用）", ""]
    for name, rec in accounts.items():
        acts = rec.get("activities") or []
        counts = rec.get("activity_counts") or {}
        out.append(f"**{ACCOUNTS.get(name, name)}**：这次读到的各类条数 " + ("，".join(f"{k} {v}" for k, v in counts.items()) or "无") + "。")
        since = (today - timedelta(days=14)).isoformat()
        recent = [r for r in acts if r.get("date", "") >= since and r.get("activity_type") != "FEE"]
        fees = sum(float(r.get("net_amount") or 0) for r in acts if r.get("date", "") >= since and r.get("activity_type") == "FEE")
        out.append(f"最近 14 天的费用（监管费、清算费）合计 {money(fees)}，不逐条列。")
        if recent:
            out += ["", "| 日期 | 类型 | 代码 | 数量 | 金额 | 说明 |", "|---|---|---|---|---|---|"]
            out += [f"| {r.get('date', '')[:10]} | {r.get('activity_type')} | {r.get('symbol', '')} | {r.get('qty', '')} | {money(r.get('net_amount'))} | "
                    f"{(r.get('description') or '')[:60]} |" for r in recent[-25:]]
        out.append("")
    out += ["## 净值曲线（券商记的每天收盘净值）", "", "| 账户 | 天数 | 最早 | 最近 | 最近 5 天的每天盈亏 |", "|---|---|---|---|---|"]
    for name, rec in accounts.items():
        h = rec.get("history") or []
        if h:
            last5 = "，".join(f"{r['day'][5:]} {money(r.get('pl'))}" for r in h[-5:])
            out.append(f"| {ACCOUNTS.get(name, name)} | {len(h)} | {h[0]['day']} {money(h[0]['equity'])} | {h[-1]['day']} {money(h[-1]['equity'])} | {last5} |")
    out += ["", "## 持有期权的报价新旧（快照对最新报价、最新成交）", ""]
    for name, rec in accounts.items():
        q = rec.get("quotes") or {}
        rows = q.get("rows") or []
        if not rows:
            out.append(f"- {ACCOUNTS.get(name, name)}：{q.get('note') or '没有持有的期权'}。")
            continue
        ages = sorted(r["snap_quote_age"] for r in rows if r["snap_quote_age"] is not None)
        same = sum(r["same_quote"] for r in rows)
        out.append(f"- {ACCOUNTS.get(name, name)}（{q.get('at')}，{len(rows)} 张）：快照报价离现在的分钟数中位 "
                   f"{ages[len(ages) // 2] if ages else '—'}、最旧 {ages[-1] if ages else '—'}；和最新报价接口完全一样的 {same} 张；"
                   f"完整行情（opra）：{q.get('opra')}。")
        out += ["", "| 合约 | 快照买 / 卖 | 快照报价几分钟前 | 最新报价买 / 卖 | 几分钟前 | 最新成交 | 几分钟前 |", "|---|---|---|---|---|---|---|"]
        out += [f"| {r['symbol']} | {r['snap_bid']} / {r['snap_ask']} | {r['snap_quote_age']} | {r['bid']} / {r['ask']} | {r['quote_age']} | "
                f"{r['trade']} | {r['trade_age']} |" for r in rows[:30]]
        out.append("")
    out += ["", "## 官方开盘价对我们用过的开盘价", ""]
    if opens:
        def stat(key):
            xs = [abs(r[key]) for r in opens if r[key] is not None]
            return (f"{len(xs)} 个，差的中位 {sorted(xs)[len(xs) // 2]:.3%}，最大 {max(xs):.3%}，超过 0.1% 的 {sum(x > 0.001 for x in xs)} 个"
                    if xs else "没有")
        out += [f"- 日线开盘价（券商、全市场）：{stat('daily_gap')}。", f"- 第一根 1 分钟线的开盘价（雅虎）：{stat('minute_gap')}。", ""]
        spy = [r for r in opens if r["symbol"] == "SPY"][-10:]
        if spy:
            out += ["| 日期 | SPY 官方开盘 | 日线开盘 | 第一根分钟线 |", "|---|---|---|---|"]
            out += [f"| {r['day']} | {r['official']:.2f} | {px(r['daily'])} | {px(r['minute'])} |" for r in spy]
            out.append("")
    else:
        out += ["没有取到官方开盘价。", ""]
    days = sorted({d for v in auc.values() for d in v})
    out.append(f"存了 {len(auc)} 只、{len(days)} 个交易日（{days[0] if days else '—'} 到 {days[-1] if days else '—'}），在 `data/broker/auctions.json`；"
               "记分取开盘价用 `broker_read.official_open(代码, 日期)`，没有的那天再用日线开盘价，不用分钟线。")
    out += ["", "## 拆股和分红", ""]
    sp = corp.get("splits") or []
    out.append(f"2015 年以来拆股 {len(sp)} 次，分红 {len(corp.get('dividends') or [])} 次（{len(corp.get('symbols') or [])} 只）。"
               "分钟线是原始价、日线是拆股调整价：比较之前看这张表里那天之后有没有拆股。")
    if sp:
        out += ["", "| 代码 | 日期 | 一股变几股 |", "|---|---|---|"] + [f"| {r['symbol']} | {r['day']} | {r['ratio']:g} |" for r in sp]
    errs = {**{f"{ACCOUNTS.get(n, n)}·{k}": v for n, rec in accounts.items() for k, v in (rec.get("errors") or {}).items()},
            **{f"行情·{k}": v for k, v in market_errors.items()}}
    out += ["", "## 没取到的", ""] + ([f"- {k}：{v}" for k, v in errs.items()] or ["- 都取到了。"])
    return "\n".join(out) + "\n"


def opens_to_compare(auc: dict, days_back: int = 30) -> tuple[dict, dict]:
    """The daily bars' opens (Alpaca SIP, raw) and Yahoo's first minute bar, for SPY and QQQ: what we used before."""
    import os
    from hero.alpaca import Alpaca
    from hero.daybook import yahoo
    daily, minute = {}, {}
    syms = [s for s in ("SPY", "QQQ") if s in auc]
    if os.getenv("ALPACA_API_KEY") and syms:
        start = (date.today() - timedelta(days=days_back + 5)).isoformat()
        try:
            page = Alpaca()._d("/v2/stocks/bars", {"symbols": ",".join(syms), "timeframe": "1Day", "start": start, "end": recent_end(),
                                                "adjustment": "raw", "feed": "sip", "limit": 10000}) or {}
            for s, bars in (page.get("bars") or {}).items():
                daily[s] = {b["t"][:10]: b["o"] for b in bars}
        except Exception:
            pass
    for s in syms:
        try:
            first = {}
            now = datetime.now(ET)
            for t, o, *_ in yahoo(s, "1m", now - timedelta(days=days_back), now):
                first.setdefault(t.date().isoformat(), o if t.hour == 9 and t.minute == 30 else None)
            minute[s] = {d: o for d, o in first.items() if o}
        except Exception:
            pass
    return daily, minute


def main() -> None:
    from hero.alpaca import Alpaca
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    today = datetime.now(ET).date()
    if cmd == "account":
        rec = run_account(Alpaca(), sys.argv[2], today)
        print(sys.argv[2], "errors:", rec["errors"], "activities:", rec.get("activity_counts"), "history days:", len(rec.get("history", [])))
    elif cmd == "market":
        print("errors:", run_market(Alpaca(), today))
    elif cmd == "report":
        accounts = {n: load(DATA / f"{n}.json", {}) for n in ACCOUNTS}
        auc, corp = load(DATA / "auctions.json", {}), load(DATA / "corporate.json", {})
        daily, minute = opens_to_compare(auc)
        text = report(today, accounts, load(DATA / "market-errors.json", {}).get("errors", {}), auc, corp, compare_opens(
            {s: auc[s] for s in ("SPY", "QQQ") if s in auc}, daily, minute))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{today.isoformat()}.md").write_text(text)
        print(text)
    else:
        raise SystemExit("usage: python -m hero.broker_read account main|s500 | market | report")


if __name__ == "__main__":
    main()
