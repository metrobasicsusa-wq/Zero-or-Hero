"""Earnings forecasts: before each report, which way and how far -- then scored once the reaction day has closed.

Owner, 10-19: forecast every US company's earnings (direction and size), record and score them first; nothing trades
on them. Run each trading day around 15:10 ET (python -m hero.research_earnings_forecast; research.yml earnings_forecast):
  1. score every earlier forecast whose reaction day has closed: the gap (open / prior close) and the move (close /
     prior close), whether the move beat the options' implied move, and whether the direction call was right --
     judged at the reaction day's open (owner, 10-19: the trade is shares or a 2x fund sold at that open), with
     the close kept as a second column;
  2. forecast tonight's after-close and tomorrow's before-open reports from the Alpha Vantage calendar (every US
     listing in it), keeping the ones with a price of $5+ and listed options: the implied move is the at-the-money
     straddle of the first expiry on or after the reaction day (mid of the latest quotes) over the price.
The baseline every name gets comes from research/2026-10-02-earnings-reactions.json (1,022 reports with option
prices, 2024-02..2026-09): the close moved more than the implied move 40% of the time, 51% when the stock's own
average reaction had run 25%+ above today's implied move; the direction was a coin toss (51% up; no momentum or
timing split moved it more than a few points). So the baseline says "up" / "down" only as a placeholder at 50%;
direction calls come from the judgment file (YYYY-MM-DD.judgment.json, written by Claude for the names worth a
closer look) and are scored against the same results. Record only.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero import earnings as earn
from hero.research_ideas import ROOT

OUT = ROOT / "research" / "earnings-forecast"
HIST = ROOT / "research" / "2026-10-02-earnings-reactions.json"
MIN_PRICE = 5.0
P_BIG, P_BIG_HOT, HOT = 0.40, 0.51, 1.25   # P(|move| > implied): base; when own average reaction / implied >= HOT
WINDOW = 45          # days past the reaction day to look for an expiry
WEEKLY = 7           # an expiry this close to the reaction day prices mostly the report itself


def business_after(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due(rows: list[dict], today: date) -> list[dict]:
    """Calendar rows reporting after today's close or before tomorrow's open, with their reaction day."""
    nxt = business_after(today)
    out = []
    for r in rows:
        if r.get("disputed"):   # Alpha Vantage places it here, Nasdaq does not: probably a stale date
            continue
        when = (r.get("timeOfTheDay") or "").strip()
        if r.get("reportDate") == today.isoformat() and when == "post-market":
            out.append({"symbol": r["symbol"], "report_date": r["reportDate"], "time": when, "reaction_day": nxt.isoformat(),
                        "estimate": r.get("estimate") or None})
        elif r.get("reportDate") == nxt.isoformat() and when == "pre-market":
            out.append({"symbol": r["symbol"], "report_date": r["reportDate"], "time": when, "reaction_day": nxt.isoformat(),
                        "estimate": r.get("estimate") or None})
    return out


def history() -> dict[str, list[float]]:
    """symbol -> its past absolute reaction-day moves."""
    try:
        ev = json.loads(HIST.read_text())["events"]
    except Exception:
        return {}
    out: dict[str, list[float]] = {}
    for e in ev:
        out.setdefault(e["symbol"], []).append(abs(e["move"]))
    return out


def baseline(implied: float | None, past: list[float]) -> dict:
    prior = statistics.mean(past[-8:]) if past else None
    hot = bool(prior and implied and prior / implied >= HOT)
    return {"prior_avg_abs": round(prior, 4) if prior else None, "prior_n": len(past),
            "p_big": P_BIG_HOT if hot else P_BIG, "p_up": 0.5}


def mid(snap: dict) -> float | None:
    q = (snap or {}).get("latestQuote") or {}
    bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
    return round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else None


def legs(client, und: str, spot: float, react: str) -> dict | None:
    """The at-the-money call and put of the first expiry on or after the reaction day (up to WINDOW days out).

    Names with only monthly options get that month's straddle: it also prices the ordinary days before expiry,
    so `days` says how far past the reaction day it runs and the scorecard keeps those apart."""
    try:
        cs = client.option_contracts(und, status="active", expiration_date_gte=react,
                                     expiration_date_lte=(date.fromisoformat(react) + timedelta(days=WINDOW)).isoformat(),
                                     strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
    except Exception:
        return {"error": True}
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    cs = [c for c in cs if c["expiration_date"] == exp]
    k = min({float(c["strike_price"]) for c in cs}, key=lambda x: abs(x - spot))
    got = {c["type"]: c["symbol"] for c in cs if float(c["strike_price"]) == k}
    days = (date.fromisoformat(exp) - date.fromisoformat(react)).days
    return {"expiry": exp, "strike": k, "days": days, **got} if set(got) >= {"call", "put"} else None


def forecast(client, rows: list[dict], today: date, wait=lambda: None, confirmed=lambda syms: {}) -> dict:
    """`confirmed`: symbols -> {"date", "confirmed"} (Yahoo; the company has announced the date)."""
    reports = due(rows, today)
    days = {today.isoformat(), business_after(today).isoformat()}
    no_time = sorted({r["symbol"] for r in rows if r.get("reportDate") in days and not r.get("disputed")
                      and (r.get("timeOfTheDay") or "").strip() not in ("pre-market", "post-market")})
    disputed = sorted({r["symbol"] for r in rows if r.get("reportDate") in days and r.get("disputed")})
    syms = sorted({r["symbol"] for r in reports})
    snaps: dict[str, dict] = {}
    for i in range(0, len(syms), 100):
        try:
            snaps.update(client.stock_snapshots(syms[i:i + 100]))
        except Exception:
            pass
    past = history()
    out, skipped = [], {"no_price": 0, "under_5": 0, "no_options": 0, "error": 0}
    skipped_symbols: dict[str, list[str]] = {k: [] for k in skipped}
    pending = []
    for r in reports:
        s = snaps.get(r["symbol"]) or {}
        spot = float(((s.get("latestTrade") or {}).get("p")) or ((s.get("dailyBar") or {}).get("c")) or 0)
        if not spot:
            skipped["no_price"] += 1
            skipped_symbols["no_price"].append(r["symbol"])
            continue
        if spot < MIN_PRICE:
            skipped["under_5"] += 1
            skipped_symbols["under_5"].append(r["symbol"])
            continue
        wait()
        lg = legs(client, r["symbol"], spot, r["reaction_day"])
        if not lg or lg.get("error"):
            why = "error" if lg else "no_options"
            skipped[why] += 1
            skipped_symbols[why].append(r["symbol"])
            continue
        prev = s.get("prevDailyBar") or {}
        pending.append(({**r, "dollar_volume": round(float(prev.get("v") or 0) * float(prev.get("c") or 0))}, spot, lg))
    quotes = {}
    want = [lg[k] for _, _, lg in pending for k in ("call", "put")]
    try:
        quotes = client.option_snapshots(want) if want else {}
    except Exception:
        quotes = {}
    for r, spot, lg in pending:
        c, p = mid(quotes.get(lg["call"])), mid(quotes.get(lg["put"]))
        implied = round((c + p) / spot, 4) if c and p else None
        out.append({**r, "spot": round(spot, 2), "expiry": lg["expiry"], "strike": lg["strike"],
                    "expiry_days": lg["days"], "weekly": lg["days"] <= WEEKLY, "call_mid": c, "put_mid": p,
                    "implied_move": implied, **baseline(implied, past.get(r["symbol"], []))})
    out.sort(key=lambda x: -x["dollar_volume"])   # the most traded first: the judgment file covers the top of the list
    try:
        conf = confirmed([r["symbol"] for r in out[:60]]) or {}
    except Exception:
        conf = {}
    for r in out:
        y = conf.get(r["symbol"])
        if y:   # True: the company announced this date; False: still an estimate, or Yahoo has another date
            r["confirmed"] = bool(y["confirmed"] and y["date"] == r["report_date"])
            if y["confirmed"] and y["date"] != r["report_date"]:
                r["yahoo_date"] = y["date"]
    return {"date": today.isoformat(), "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "calendar_rows_due": len(reports), "calendar_no_time": no_time, "calendar_disputed": disputed, "skipped": skipped,
            "skipped_symbols": {k: v for k, v in skipped_symbols.items() if v}, "reports": out}


ET = ZoneInfo("America/New_York")
PRE_LAG = timedelta(minutes=30)   # an 8-K can trail the press release: the price before is taken this much earlier
AFTER = timedelta(minutes=60)     # the reference price after the report
RESCORE_DAYS = 10   # owner's audit, 10-26: rows this recent are scored again each run, so a late or corrected 8-K time
                    # (or a headline) replaces a previous-close fallback; a found release is never replaced by the fallback
MAX_NEWS = 2000     # headlines read per batch of symbols and night
MAX_VERIFY = 60     # index pages read per run to check an 8-K time that looks mislabelled
LATE = time(17, 35)   # an 8-K accepted later than this is rare (EDGAR dates it the next business day): checked
# A headline that is the results release itself (not a preview, a call recap or an analyst note).
PRESS = re.compile(r"\b(report(s|ed)?|announce[sd]?|post(s|ed)?|deliver(s|ed)?|release[sd]?)\b.*"
                   r"\b(quarter|results|earnings|eps|revenue|sales|q[1-4]|fy\d*|fiscal|net income|profit|loss)\b"
                   r"|\b(q[1-4]|quarter(ly)?|fy\d*|fiscal|full[- ]year)\b.*\b(eps|revenue|sales|results|earnings)\b.*"
                   r"\b(beat|miss|top|in[- ]line|vs\.?|estimate|est\.?|consensus)", re.I)
NOT_PRESS = re.compile(r"\b(preview|expect|ahead of|to report|will report|set to|to announce|to release|what to watch|"
                       r"scheduled|call|conference|webcast|transcript|highlights|recap|takeaways|analyst|price target|"
                       r"upgrade|downgrade|initiat)", re.I)


def business_before(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _t(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)


def _when(x) -> datetime:
    return x["accepted"] if isinstance(x, dict) else x


def _checked(verify, x: dict) -> datetime | None:
    try:
        return verify(x["cik"], x["accession"])
    except Exception as e:
        print(f"SEC: index page for {x.get('accession')}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def release_time(row: dict, accepted: list, verify=None) -> datetime | None:
    """The report's 8-K (item 2.02) filing time: the first one after the previous session's 09:30 and before the
    reaction day's open. `accepted` holds datetimes or the filing dicts from research_earnings.filings_202; with the
    dicts and `verify` (cik, accession -> the time on the filing's own index page) a time that looks mislabelled is
    checked first (owner's audit, 10-26: the submissions API showed GE's 06:30 filing as 10:30 and NFLX's 16:01 as
    20:05-style times for three days running): a before-open report whose only 8-K appears after the open, and any
    8-K after LATE."""
    react = date.fromisoformat(row["reaction_day"])
    lo, hi = datetime.combine(business_before(react), time(9, 30), ET), datetime.combine(react, time(9, 30), ET)
    xs = sorted(accepted, key=_when)
    inside = [x for x in xs if lo <= _when(x) < hi]
    if inside:
        x = inside[0]
        if verify and isinstance(x, dict) and _when(x).time() > LATE:
            v = _checked(verify, x)
            if v and lo <= v < hi:
                return v
        return _when(x)
    if verify and row.get("time") == "pre-market":
        for x in xs:
            if isinstance(x, dict) and hi <= _when(x) < datetime.combine(react, time(15, 0), ET):
                v = _checked(verify, x)
                if v and lo <= v < hi:
                    return v
    return None


def around(row: dict, mins: list[dict], prev_close: float, rel: datetime | None, src: str | None = None) -> dict:
    """Owner, 10-19: shares trade 24/5, so the price to beat is the last one before the report, not the last close.
    After-close reports land after 16:00, so that is the regular close. Before-open ones (some in the small hours)
    trade into the release: the last minute bar more than PRE_LAG before the release -- the 8-K filing or the press
    release's headline, whichever came first (the SIP tape covers 04:00-20:00 ET; overnight 20:00-04:00 trades are not
    in it) -- else the previous close. before_src says which was used: edgar / press / prev_close (owner's audit,
    10-26: the fallback has to be visible). Also the price an hour after the release, for reference."""
    before, bsrc, btime = prev_close, "prev_close", None
    if rel is None and row.get("time") == "post-market" and row.get("report_date"):
        # no release time: an after-close report is out by 16:05 on its date, so the first reaction can still be
        # read an hour later (owner, 10-22: META +5% after hours, -6% at the open)
        rel, src = datetime.combine(date.fromisoformat(row["report_date"]), time(16, 5), ET), "assumed"
    if rel is not None and src is None:
        src = "edgar"
    if row.get("time") == "pre-market" and rel:
        lo = datetime.combine(business_before(date.fromisoformat(row["reaction_day"])), time(16, 0), ET)
        xs = [b for b in mins if lo <= _t(b) < rel - PRE_LAG]
        if xs:
            before, bsrc, btime = float(xs[-1]["c"]), src, _t(xs[-1]).isoformat(timespec="minutes")
    after = None
    if rel:
        ys = [b for b in mins if rel + AFTER <= _t(b) < rel + AFTER + timedelta(minutes=30)]
        after = float(ys[0]["c"]) if ys else None
    ah_close = None   # the extended-hours close (19:45-20:00) of an after-close report's day: the call digested,
    if row.get("time") == "post-market" and row.get("report_date"):   # the overnight still to come (owner, 10-22)
        rd = date.fromisoformat(row["report_date"])
        zs = [b for b in mins if datetime.combine(rd, time(19, 45), ET) <= _t(b) <= datetime.combine(rd, time(20, 0), ET)]
        ah_close = float(zs[-1]["c"]) if zs else None
    return {"before": before, "before_src": bsrc, "before_time": btime,
            "release": rel.isoformat(timespec="minutes") if rel else None, "release_src": src if rel else None,
            "after_1h": after, "ah_close": ah_close}


def score_row(row: dict, bars: list[dict], mins: list[dict] | None = None, rel: datetime | None = None,
              press: datetime | None = None) -> dict | None:
    """The reaction day against the session before it, and the open against the last price before the report.
    `rel` is the 8-K filing time, `press` the press release's headline time; the earlier one is the release."""
    days = {b["t"][:10]: b for b in bars}
    order = sorted(days)
    if row["reaction_day"] not in days:
        return None
    i = order.index(row["reaction_day"])
    if i == 0:
        return None
    prev, b = float(days[order[i - 1]]["c"]), days[row["reaction_day"]]
    gap, move = float(b["o"]) / prev - 1, float(b["c"]) / prev - 1
    imp = row.get("implied_move")
    cands = [(t, name) for t, name in ((rel, "edgar"), (press, "press")) if t is not None]
    when, src = min(cands) if cands else (None, None)
    a = around(row, mins or [], prev, when, src)
    gap_pre = float(b["o"]) / a["before"] - 1
    return {"gap": round(gap, 4), "move": round(move, 4), "big": (abs(move) > imp) if imp else None,
            "before": a["before"], "before_src": a["before_src"], "before_time": a["before_time"],
            "release": a["release"], "release_src": a["release_src"],
            "gap_pre": round(gap_pre, 4),
            "after_1h": round(a["after_1h"] / a["before"] - 1, 4) if a["after_1h"] else None,
            "ah_close": round(a["ah_close"] / a["before"] - 1, 4) if a["ah_close"] else None,
            "up": gap_pre > 0, "up_close": move > 0}   # direction: the open against the price before the report


def press_time(row: dict, items: list[dict]) -> datetime | None:
    """The press release's time, from Benzinga headlines (Alpaca's news feed): the first one in the 8-K's window that
    reads like the results themselves, tagged with the row's symbol first (or as one of two) so a peer's release does
    not count. A second source for the release time (owner's audit, 10-26): foreign filers and FDIC-registered banks
    file no 8-K, and an 8-K can trail the release or come through mistimed."""
    react = date.fromisoformat(row["reaction_day"])
    lo, hi = datetime.combine(business_before(react), time(9, 30), ET), datetime.combine(react, time(9, 30), ET)
    sym, xs = row["symbol"], []
    for n in items:
        tags = n.get("symbols") or []
        if sym not in tags or (tags[0] != sym and len(tags) > 2):
            continue
        head = n.get("headline") or ""
        if not PRESS.search(head) or NOT_PRESS.search(head):
            continue
        try:
            t = datetime.fromisoformat(str(n.get("created_at")).replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            continue
        if lo <= t < hi:
            xs.append(t)
    return min(xs) if xs else None


def headlines(client, symbols: list[str], lo: datetime, hi: datetime) -> dict[str, list[dict]]:
    """symbol -> its Benzinga headlines between lo and hi; {} when the client has no news feed or it fails."""
    get = getattr(client, "news_range", None)
    if get is None:
        return {}
    out: dict[str, list[dict]] = {}
    try:
        for i, n in enumerate(get(symbols, lo.isoformat(), hi.isoformat())):
            if i >= MAX_NEWS:
                break
            for s in n.get("symbols") or []:
                if s in symbols:
                    out.setdefault(s, []).append(n)
    except Exception as e:
        print(f"news: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def releases_for(symbols: list[str], since: str) -> dict[str, list[dict]]:
    """symbol -> 8-K item 2.02 filings from SEC EDGAR (research_earnings.filings_202: accepted time, accession, filing
    date, cik); empty when SEC cannot be reached. What failed goes to stderr (owner's audit, 10-26: a silent miss scored
    a whole week against the previous close)."""
    try:
        from hero import research_earnings as rer
        ciks = rer.cik_map()
    except Exception as e:
        print(f"SEC: no 8-K times this run ({type(e).__name__}: {e})", file=sys.stderr)
        return {}
    out, unknown, failed = {}, [], []
    for s in symbols:
        cik = ciks.get(s) or ciks.get(s.replace(".", "-"))   # EDGAR writes BRK-B for Alpaca's BRK.B
        if cik is None:
            unknown.append(s)
            out[s] = []
            continue
        try:
            out[s] = rer.filings_202(cik, since)
        except Exception as e:
            failed.append(f"{s} ({type(e).__name__})")
            out[s] = []
    if unknown or failed:
        print(f"SEC: no CIK for {unknown or '-'}; lookup failed for {failed or '-'}", file=sys.stderr)
    return out


def edgar_check(budget: int = MAX_VERIFY):
    """research_earnings.accepted_at with a per-run budget of index-page reads; does nothing until cik_map() has set the
    SEC headers (no contact address, no requests)."""
    try:
        from hero import research_earnings as rer
    except Exception:
        return None
    left = [budget]

    def check(cik, accession):
        if left[0] <= 0 or not rer.SEC_HEADERS:
            return None
        left[0] -= 1
        return rer.accepted_at(cik, accession)
    return check


def better(new: dict, old: dict | None) -> bool:
    """Owner's audit, 10-26: a row within RESCORE_DAYS is scored again each run. The new result replaces the old one
    when the old is from before 10-26 (no release_src), when it found a release time the old one lacked (assumed or
    none), or when its release is earlier (a second source, or an 8-K time corrected); a run that finds less keeps
    what is on file, so a day without EDGAR or the news feed does not undo a score."""
    if old is None or "release_src" not in old:
        return True
    o_found, n_found = old.get("release_src") in ("edgar", "press"), new.get("release_src") in ("edgar", "press")
    if o_found != n_found:
        return n_found
    if o_found:
        return datetime.fromisoformat(new["release"]) < datetime.fromisoformat(old["release"])
    return True   # neither found a release: the same fallback either way


def score_due(client, today: date, root: Path = OUT, sec=releases_for, verify=None) -> list[str]:
    """Fill in results for every forecast file whose reaction days have closed; returns the files changed. Rows whose
    reaction day is within RESCORE_DAYS are scored again (see `better`: a found or earlier release replaces what is on
    file, a run that finds less does not). `verify` checks a filing time that looks mislabelled (see release_time)."""
    changed = []
    check = verify if verify is not None else edgar_check()
    recent = (today - timedelta(days=RESCORE_DAYS)).isoformat()
    for f in sorted(root.glob("20??-??-??.json")):
        text = f.read_text()
        rep = json.loads(text)
        open_rows = [r for r in rep.get("reports", []) if r["reaction_day"] < today.isoformat()
                     and ("result" not in r or r["reaction_day"] >= recent)]
        if not open_rows:
            continue
        first = date.fromisoformat(min(r["reaction_day"] for r in open_rows))
        syms = sorted({r["symbol"] for r in open_rows})
        bars: dict[str, list[dict]] = {}
        for i in range(0, len(syms), 100):
            try:
                bars.update(client.daily_bars(syms[i:i + 100], (first - timedelta(days=7)).isoformat(),
                                              adjustment="raw", feed="sip"))
            except Exception:
                pass
        rel = sec(syms, (first - timedelta(days=5)).isoformat())
        mins: dict[tuple[str, str], list[dict]] = {}
        heads: dict[tuple[str, str], list[dict]] = {}
        for day in sorted({r["reaction_day"] for r in open_rows}):
            ds = sorted({r["symbol"] for r in open_rows if r["reaction_day"] == day})
            lo = datetime.combine(business_before(date.fromisoformat(day)), time(15, 59), ET)
            hi = datetime.combine(date.fromisoformat(day), time(9, 31), ET)
            for i in range(0, len(ds), 100):
                try:
                    got = client.stock_bars(ds[i:i + 100], lo.isoformat(), hi.isoformat(), feed="sip")
                except Exception:
                    got = {}
                for k, v in got.items():
                    mins.setdefault((day, k), []).extend(v)
            for i in range(0, len(ds), 20):
                for k, v in headlines(client, ds[i:i + 20], lo, hi).items():
                    heads.setdefault((day, k), []).extend(v)
        for r in open_rows:
            key = (r["reaction_day"], r["symbol"])
            res = score_row(r, bars.get(r["symbol"], []), mins.get(key, []),
                            release_time(r, rel.get(r["symbol"], []), check), press_time(r, heads.get(key, [])))
            if res and better(res, r.get("result")):
                r["result"] = res
        new = json.dumps(rep, ensure_ascii=False) + "\n"
        if new != text:
            f.write_text(new)
            changed.append(f.name)
    return changed


CUTOFF = "16:00"   # ET on the forecast date: before tonight's after-close reports and tomorrow's early-morning ones
EVERY_FROM = "2026-10-20"   # owner, 10-19: from this forecast day every report gets an up / down call


def on_time(written: str | None, day: str) -> bool:
    """Owner, 10-19: judge before the report. Some before-open reports land in the small hours, so every call must be
    in by 16:00 ET on the forecast date (the session before the reaction); a missing or later time does not count."""
    try:
        w = datetime.fromisoformat(str(written))
    except ValueError:
        return False
    et = ZoneInfo("America/New_York")
    w = w.replace(tzinfo=et) if w.tzinfo is None else w
    return w <= datetime.fromisoformat(f"{day}T{CUTOFF}").replace(tzinfo=et)


def judged(root: Path = OUT) -> dict[tuple[str, str], dict]:
    """(forecast date, symbol) -> Claude's judgment for it, from YYYY-MM-DD.judgment.json; `late` when it came in after
    the cutoff (kept for the record, left out of the score)."""
    out = {}
    for f in sorted(root.glob("20??-??-??.judgment.json")):
        d = json.loads(f.read_text())
        day = d.get("date") or f.name[:10]
        late = not on_time(d.get("written"), day)
        for j in d.get("calls") or []:
            out[(day, j["symbol"])] = {**j, "late": late}
    return out


def scorecard(root: Path = OUT) -> str:
    rows, js = [], judged(root)
    for f in sorted(root.glob("20??-??-??.json")):
        rep = json.loads(f.read_text())
        for r in rep.get("reports", []):
            if "result" in r:
                rows.append({**r, "day": rep["date"], "judgment": js.get((rep["date"], r["symbol"]))})
    md = [f"# 财报预测记分卡 {date.today().isoformat()}", "",
          "每个交易日 15:10 ET 前后，对当晚盘后、次日盘前发财报的美股（$5 以上、有期权）记下期权隐含的涨跌幅（平值跨式 / 股价），"
          "基线给每只「超过隐含幅度」的概率（历史 40%；自身过去平均反应比隐含高 25% 以上的 51%）；方向的判断只在判断文件里。"
          "判断在预测日 16:00 ET 前写好（财报之前，包括凌晨发的盘前财报）。反应日收盘后记分：方向按反应日开盘对财报前最后一笔（24 小时交易，盘前财报用发布前的盘前价——8-K 提交时间和新闻稿标题时间取早的那个、再提前 30 分钟，"
          "两个都找不到就用前收并在明细里标明，盘后财报就是收盘）（10-19 起，和『第二天开盘卖』的做法一致），收盘另列一栏参考；"
          "幅度（有没有超过隐含）按收盘对前收，和基线的统计口径一致。只记录，不交易。", ""]
    if not rows:
        return "\n".join(md + ["还没有可记分的财报。", ""])
    allbig = [r for r in rows if r["result"]["big"] is not None]
    big = [r for r in allbig if r.get("weekly", True)]   # files from before 10-19 noon had only weekly expiries
    md += ["## 基线：涨跌幅有没有超过期权定价", "", "| 组 | 次数 | 实际超过隐含 | 基线预计 |", "|---|---|---|---|"]
    for name, rs in (("一周内到期的全部", big), ("自身平均反应偏大（基线 51%）", [r for r in big if r["p_big"] >= P_BIG_HOT]),
                     ("其他（基线 40%）", [r for r in big if r["p_big"] < P_BIG_HOT]),
                     ("月度到期（隐含偏大，只作参考）", [r for r in allbig if r.get("weekly") is False])):
        if rs:
            md.append(f"| {name} | {len(rs)} | {sum(r['result']['big'] for r in rs) / len(rs):.0%} | "
                      f"{statistics.mean(r['p_big'] for r in rs):.0%} |")
    md += before_sources(rows)
    late = [r for r in rows if r["judgment"] and r["judgment"].get("late")]
    for r in late:
        r["judgment"] = None   # judged after the cutoff: not scored
    jr = [r for r in rows if r["judgment"]]
    every = [r for r in rows if r["day"] >= EVERY_FROM]
    md += ["", "## 判断", "",
           f"判断必须在预测日 {CUTOFF} ET 前写好（盘后财报之前；明天盘前、凌晨发的财报也在这之前），晚了不算分。"
           f"{EVERY_FROM} 起每个财报都要给涨或跌（用户 10-19），没给或晚交的在「全部财报」那行算错。"
           + (f"这次有 {len(late)} 条晚交，没算。" if late else ""), ""]
    if not jr and not every:
        md.append("还没有带判断、而且已经记分的财报。")
    else:
        dirs = [r for r in jr if r["judgment"].get("direction") in ("up", "down")]
        bigs = [r for r in jr if r["judgment"].get("big") in (True, False) and r["result"]["big"] is not None]
        md += ["| 项目 | 次数 | 对的比例 |", "|---|---|---|"]
        if every:
            called = [r for r in every if r["judgment"] and r["judgment"].get("direction") in ("up", "down")]
            right = sum((r["judgment"]["direction"] == "up") == r["result"]["up"] for r in called)
            md.append(f"| 方向按开盘，全部财报（没给或晚交算错） | {len(every)} | {right / len(every):.0%} |")
            md.append(f"| 给了涨跌的比例 | {len(every)} | {len(called) / len(every):.0%} |")
        if dirs:
            md.append(f"| 方向（按开盘） | {len(dirs)} | "
                      f"{sum((r['judgment']['direction'] == 'up') == r['result']['up'] for r in dirs) / len(dirs):.0%} |")
            cl = [r for r in dirs if "up_close" in r["result"]]
            if cl:
                md.append(f"| 方向（按收盘，参考） | {len(cl)} | "
                          f"{sum((r['judgment']['direction'] == 'up') == r['result']['up_close'] for r in cl) / len(cl):.0%} |")
        if bigs:
            md.append(f"| 幅度（会不会超过隐含） | {len(bigs)} | "
                      f"{sum(r['judgment']['big'] == r['result']['big'] for r in bigs) / len(bigs):.0%} |")
        hi = [r for r in dirs if float(r["judgment"].get("confidence") or 0) >= 0.6]
        if hi:
            md.append(f"| 方向按开盘，信心 0.6 以上 | {len(hi)} | "
                      f"{sum((r['judgment']['direction'] == 'up') == r['result']['up'] for r in hi) / len(hi):.0%} |")
    md += share_trades(rows, jr)
    md += ["", "## 明细（最近 40 条）", "", "| 预测日 | 股票 | 时间 | 财报前价 | 隐含 | 财报后 1 小时 | 盘后 20:00 | 开盘 | 收盘（对前收） | 超过隐含 | 判断 | 方向对不对（开盘 / 收盘） |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["day"], r["symbol"]))[-40:]:
        j, res = r["judgment"], r["result"]
        a1h = f"{res['after_1h']:+.1%}" if res.get("after_1h") is not None else "—"
        a1h += f" | {res['ah_close']:+.1%}" if res.get("ah_close") is not None else " | —"
        jtxt = f"{'涨' if j.get('direction') == 'up' else '跌' if j.get('direction') == 'down' else '—'} / " \
               f"{'大' if j.get('big') is True else '小' if j.get('big') is False else '—'} / {float(j.get('confidence') or 0):.1f}" if j else ""
        ok = ""
        if j and j.get("direction") in ("up", "down"):
            ok = "对" if (j["direction"] == "up") == res["up"] else "错"
            if "up_close" in res:
                ok += " / " + ("对" if (j["direction"] == "up") == res["up_close"] else "错")
        md.append(f"| {r['day']} | {r['symbol']} | {'盘后' if r['time'] == 'post-market' else '盘前'} | {before_label(r)} | "
                  f"{(r.get('implied_move') or 0):.1%}{'（月）' if r.get('weekly') is False else ''} | "
                  f"{a1h} | {res.get('gap_pre', res['gap']):+.1%} | {res['move']:+.1%} | "
                  f"{'是' if res['big'] else '否' if res['big'] is not None else '—'} | {jtxt} | {ok} |")
    return "\n".join(md) + "\n"


def before_label(r: dict) -> str:
    """What the row's price before the report was: the close for an after-close report; for a before-open one the
    pre-market trade before the 8-K or the press release, or the previous close when neither time was found."""
    res, src = r["result"], str(r["result"].get("before_src") or "")
    if r.get("time") != "pre-market":
        return "收盘"
    at = (res.get("before_time") or "")[11:16]
    if src.startswith("财报前"):   # files from before 10-26 wrote the trade's time into the label
        return f"8-K 前 {src.split()[-1]}"
    if src == "edgar":
        return f"8-K 前 {at}"
    if src == "press":
        return f"新闻稿前 {at}"
    return "前收（没找到发布时间）"


def before_sources(rows: list[dict]) -> list[str]:
    """Owner's audit, 10-26: 92 of 94 before-open reports in the week of 10-19 were scored against the previous close
    because no 8-K time came through, which put the overnight index move into every gap. The scorecard now says how
    many before-open reports got a real pre-report price and from where."""
    pre = [r for r in rows if r.get("time") == "pre-market"]
    if not pre:
        return []
    n = {"edgar": 0, "press": 0, "prev_close": 0}
    for r in pre:
        src = str(r["result"].get("before_src") or "")
        n["edgar" if src == "edgar" or src.startswith("财报前") else "press" if src == "press" else "prev_close"] += 1
    return ["", "## 盘前财报的「财报前价」从哪来", "",
            f"盘前财报 {len(pre)} 只。前收是回落：发布时间没找到，开盘价里就混进了隔夜大盘的涨跌（10-22 那晚期货 -1.1%）。", "",
            "| 来源 | 次数 |", "|---|---|",
            f"| 8-K 提交前 30 分钟的盘前成交 | {n['edgar']} |",
            f"| 新闻稿标题前 30 分钟的盘前成交 | {n['press']} |",
            f"| 前收（回落） | {n['prev_close']} |"]


def share_trades(rows: list[dict], jr: list[dict]) -> list[str]:
    """Owner, 10-19: earnings only in shares or 2x ETFs (option IV collapses after the report), and shares trade
    24/5. What the judgment would have made that way: bought at the last price before the report, sold at the
    reaction day's open; also bought at the close before (what our ledgers do at 15:40). A down call is a short or
    an inverse fund. 2x is twice the move (a daily-reset fund over one night). No costs."""
    def line(name, rs, sign, key="gap_pre"):
        if not rs:
            return None
        g = [sign(r) * r["result"].get(key, r["result"]["gap"]) for r in rs]
        return (f"| {name} | {len(g)} | {sum(x > 0 for x in g) / len(g):.0%} | {statistics.mean(g):+.2%} | "
                f"{statistics.median(g):+.2%} | {2 * statistics.mean(g):+.2%} |")
    d = lambda r: 1 if r["judgment"]["direction"] == "up" else -1
    dirs = [r for r in jr if r["judgment"].get("direction") in ("up", "down")]
    out = ["", "## 按判断做正股 / 两倍 ETF（财报前最后一笔买，反应日开盘卖）", "",
           "周一到周五 24 小时能交易，所以买价用财报前最后一笔：盘后财报就是当天收盘；盘前（包括凌晨）财报是发布（8-K 提交或新闻稿标题，取早的）"
           "前 30 分钟以前的最后一笔（数据只有 04:00–20:00，夜盘 20:00–04:00 的成交拿不到，那时用前一天盘后最后一笔；发布时间没找到的用前收，明细里标明）。"
           "看跌的判断要做空或买反向基金；两倍按 2 倍算。不算买卖价差。", "",
           "| 组 | 次数 | 赚钱比例 | 正股平均 | 正股中位数 | 两倍平均 |", "|---|---|---|---|---|---|"]
    out += [x for x in (
        line("按判断方向，全部", dirs, d),
        line("只做看涨的判断", [r for r in dirs if d(r) > 0], d),
        line("只做看跌的判断", [r for r in dirs if d(r) < 0], d),
        line("判断信心 0.6 以上", [r for r in dirs if float(r["judgment"].get("confidence") or 0) >= 0.6], d),
        line("按判断方向，前一天 15:40 买（我们账本的做法）", dirs, d, "gap"),
        line("按判断方向，盘后 20:00 就卖（只有盘后财报；10-22：META 盘后 +2%、开盘 -6%）",
             [r for r in dirs if r["result"].get("ah_close") is not None], d, "ah_close"),
        line("对照：全部财报都买（不看判断）", rows, lambda r: 1),
        line("对照：全部盘后财报都买，20:00 卖", [r for r in rows if r["result"].get("ah_close") is not None], lambda r: 1, "ah_close")) if x]
    return out


def markdown(rep: dict) -> str:
    md = [f"# 财报预测 {rep['date']}", "",
          f"当晚盘后、下一个交易日盘前发财报：日历里 {rep['calendar_rows_due']} 只"
          f"（另有 {len(rep.get('calendar_no_time', []))} 只今明两天发、但日历没写盘前盘后，没算进来），记下 {len(rep['reports'])} 只"
          f"（跳过：没有价格 {rep['skipped']['no_price']}、股价不到 $5 {rep['skipped']['under_5']}、"
          f"{WINDOW} 天内没有期权 {rep['skipped']['no_options']}、查期权出错 {rep['skipped'].get('error', 0)}）。"
          "隐含幅度 = 反应日当天或之后第一个到期的平值跨式 / 股价；到期离反应日超过一周的（多是只有月度期权的）标『月』，"
          "那个跨式还包含财报以外的日子，隐含偏大，记分时分开算。成交额来自 IEX（只占全市场一小部分），只用来排序。"
          "基线概率见记分卡说明；方向判断在同日的 judgment 文件里。只记录，不交易。", "",
          "| 股票 | 时间 | 反应日 | 公司确认 | 股价 | 前一天 IEX 成交额 | 隐含幅度 | 过去平均反应 | 超过隐含的基线概率 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rep["reports"]:
        prior = f"{r['prior_avg_abs']:.1%}（{r['prior_n']} 次）" if r.get("prior_avg_abs") else "—"
        imp = f"{r['implied_move']:.1%}" if r.get("implied_move") else "—"
        if r.get("implied_move") and r.get("weekly") is False:
            imp += f"（月，{r['expiry']}）"
        ok = {True: "✓", False: "估计"}.get(r.get("confirmed"), "—") + (f"（Yahoo：{r['yahoo_date']}）" if r.get("yahoo_date") else "")
        md.append(f"| {r['symbol']} | {'盘后' if r['time'] == 'post-market' else '盘前'} | {r['reaction_day']} | {ok} | "
                  f"${r['spot']:,.2f} | ${r.get('dollar_volume', 0) / 1e6:,.1f}M | {imp} | {prior} | {r['p_big']:.0%} |")
    for why, label in (("no_options", f"{WINDOW} 天内没有期权"), ("error", "查期权出错"), ("under_5", "股价不到 $5"), ("no_price", "没有价格")):
        if rep.get("skipped_symbols", {}).get(why):
            md += ["", f"{label}：{'、'.join(rep['skipped_symbols'][why])}"]
    if rep.get("calendar_disputed"):
        md += ["", f"Alpha Vantage 放在今明两天、Nasdaq 没有的（日期可能过时，没记）：{'、'.join(rep['calendar_disputed'])}"]
    if rep.get("calendar_no_time"):
        md += ["", f"今明两天发、日历没写盘前盘后（没记）：{'、'.join(rep['calendar_no_time'])}"]
    return "\n".join(md) + "\n"


def big_names(client, root: Path = ROOT) -> list[str]:
    """Names worth asking Yahoo about when no calendar lists them: today's 100 most traded, our pool, both accounts'
    holdings, and research/earnings-forecast/extra_watch.json (new listings, added by hand)."""
    out = set()
    try:
        out |= set(client.most_actives(100))
    except Exception:
        pass
    try:
        out |= set(json.loads((root / "data" / "universe.json").read_text()).get("stocks") or [])
    except Exception:
        pass
    for d in ("journal", "journal-s500"):
        try:
            for p in json.loads((root / d / "snapshot.json").read_text()).get("positions") or []:
                sym = str(p.get("symbol") or "")
                out.add(sym[:-15] if len(sym) > 15 and sym[-9] in "CP" else sym)
        except Exception:
            pass
    try:
        out |= set(json.loads((OUT / "extra_watch.json").read_text()).get("symbols") or [])
    except Exception:
        pass
    return sorted(s for s in out if s and s.isalpha())


def main() -> None:
    import os
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    OUT.mkdir(parents=True, exist_ok=True)
    changed = score_due(client, today)
    rows, check = earn.cross_check(earn.fetch(os.environ["ALPHAVANTAGE_API_KEY"]), today, days=4)   # Nasdaq fixes dates / times
    rows, check["yahoo_added"] = earn.fill_missing(rows, today, big_names(client))   # what both calendars miss (SpaceX, 10-20)
    rep = forecast(client, rows, today, wait=Pace(client).wait,  # the main account's key: slow while the market is open
                   confirmed=earn.yahoo_dates)
    rep["cross_check"] = {k: check.get(k) for k in ("days", "moved", "time_filled", "disputed", "added", "error", "yahoo_added") if k in check}
    (OUT / f"{today.isoformat()}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (OUT / f"{today.isoformat()}.md").write_text(markdown(rep))
    (OUT / "scorecard.md").write_text(scorecard())
    print(markdown(rep))
    print("scored:", changed)


if __name__ == "__main__":
    sys.exit(main())
