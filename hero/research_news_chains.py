"""News chains (owner, 2026-11-12: 「这其实都有线索链的」「其实你应该做个系统 去分析」「这都是长期线」
「你可以验证在途中买一个长call 或长put」「你得自己算下在哪里止盈 而不是硬拿着等」).

Nvidia, 2026-11: a CEO scandal came out in steps -- a downgrade, a subpoena threat, a senator calling for the CEO
to go, a second affair, the board meeting -- and the stock fell with each step; the resignation itself (09:32,
11-12) marked the low. Is that a pattern? While bad news about one company keeps arriving, does the stock keep
going the same way, and once the story is settled (the CEO goes, the case closes) does it turn? And since these
stories take weeks, does a long-dated call or put bought part-way pay, and where should it be sold?

Headlines: Alpaca's news (Benzinga), 2024-02 on, for the first 100 stocks of the pool; a headline counts for a
stock when it names it (at most 3 tickers). A "pressure" headline matches one of THEMES and is not law-firm
advertising. Megacaps have such a headline most days, so a chain is judged against the stock's own usual rate:
- confirmed: the first day the last 5 sessions hold at least MIN_HITS pressure headlines, at least MULT times the
  stock's usual 5-session count (the 120 sessions before), on at least two days and of at least two themes.
  That is the earliest day you could have called it a chain; its start is the first headline in that window.
- it stays open while the 5-session count holds at least half of that; a new chain needs the old one closed.
- settled: the first headline from the confirmed day to 5 sessions after the chain's last one that settles it
  ("resigns", "steps down", "names ... CEO"; or, for chains about probes / politics, "settles", "cleared",
  "closes probe", "dismissed"), not hedged ("could", "weighs", "calls for", a question mark ...).
(A) The stock, split- and dividend-adjusted daily bars: the chain's direction is the move from the close before
the start to the confirmed close; then 5 / 21 / 42 sessions on from the confirmed close, and from the settled
day's close (and from its open when the settling headline came before the open). Each also less SPY.
(B) Long options, real daily option bars: the at-the-money put and call on the nearest monthly expiry 75-130 days
out, bought at the confirmed day's close ("along" = put when the chain's direction is down, call when up;
"against" = the other one), and again at the settled day's close. 5% paid on the way in and out. Exits: the 21st /
42nd session, the settled day, 1.5x / 2x / 3x limits, a 40% giveback after 2x; and where each option peaked.
A split between entry and expiry drops the trade (the contract's strike no longer matches raw prices).
Research only: nothing here changes a trading rule. Run: python -m hero.research_news_chains
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import time
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero import robust

ET = ZoneInfo("America/New_York")
START = "2024-02-01"
POOL = 100
WINDOW = 5           # sessions counted together
MIN_HITS = 4         # pressure headlines in the window ...
MULT = 3.0           # ... and this many times the stock's own usual count
BASE = 120           # sessions of history for the usual count
WARMUP = 20          # sessions of history before a chain can be called at all
GAP = 5              # sessions after the last headline in which a settling headline still belongs to the chain
HOLDS = (5, 21, 42)
STOCK_COST = 0.001   # robust's extra cost per side for a stock trade (its default 5% is for options)
DTE = (75, 130)
OPT_HOLD = 63
SLIP = 0.05
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "110"))

EXEC = r"(?:ceo|cfo|chief executive|chief financial|chairman|founder|president)"
THEMES = {
    "丑闻": r"\b(?:affair|misconduct|harass\w*|scandal\w*|inappropriate|personal conduct|allegations?|accus\w+|whistleblower)\b",
    "调查": r"\b(?:subpoena\w*|probes?|probed|investigat\w+|justice department|doj|antitrust|ftc|indict\w*|raid(?:s|ed)?|"
            r"fraud\w*|accounting (?:irregularit\w+|issues?|errors?|probe)|restat\w+)\b",
    "做空报告": r"\b(?:short[- ]seller|short report|hindenburg|muddy waters|citron|spruce point|kerrisdale|grizzly research|"
                r"culper|fuzzy panda|gotham city)\b",
    "政治": r"\b(?:senators?|lawmakers?|congress\w*|house (?:committee|panel)|senate (?:committee|panel)|hearing|testif\w+|"
            r"export (?:ban|controls?|curbs?|restrictions?|rules?)|entity list|blacklist\w*|sanction\w*)\b",
    "管理层": r"\b" + EXEC + r"\b.{0,50}\b(?:resign\w*|step(?:s|ped|ping)? down|oust\w*|fired|exit(?:s|ed|ing)?|depart\w*|"
              r"to leave|leaving|removed?|removal|retire[sd]?|retirement|leave of absence)\b|"
              r"\b(?:resignation|ouster|removal|departure|firing)\b.{0,30}\b" + EXEC + r"\b|"
              r"\bboard\b.{0,40}\b(?:meets?|meeting|reviews?|emergency)\b|\bspecial committee\b",
    "降级": r"\bdowngrade[sd]?\b",
}
THEME_RE = {k: re.compile(v) for k, v in THEMES.items()}
ADS = re.compile(r"\b(?:llp|attorneys?|law firm|law offices?|lead plaintiff|class action|securities litigation|alert|"
                 r"on behalf of|investors who|investors with|reminds|encourages|deadline|losses)\b|"
                 r"rosen|pomerantz|bragar|levi & korsinsky|faruqi|kessler topaz|bernstein liebhard|glancy|schall|"
                 r"block & leviton|hagens berman|kirby mcinerney|bronstein|robbins|portnoy|holzer|gross law|"
                 r"halper sadeh|kahn swick|johnson fistel|edelson|lowey|wohl & fruchter|monteverde|rigrodsky|"
                 r"brodsky|ademi|kaskela|wolf haldenstein|scott\+scott|berger montague|labaton|saxena white|djs law", re.I)
SETTLE = {
    "人走了": re.compile(r"\b(?:resign(?:s|ed)|step(?:s|ped)? down|ousted|(?:is|was|been) fired|fires (?:its |the )?(?:ceo|chief)|"
                         r"retire[sd]|names? .{0,40}\b(?:ceo|chief executive)|appoints? .{0,40}\b(?:ceo|chief executive)|"
                         r"interim (?:ceo|chief executive))\b", re.I),
    "案子了结": re.compile(r"\b(?:settle[sd]?|settlement|cleared|(?:closes?|ends?|drops?) (?:its |the )?(?:probe|investigation|"
                           r"inquiry|charges|case)|dismiss(?:es|ed)|wins? (?:dismissal|appeal))\b", re.I),
}
LEGAL_THEMES = {"调查", "政治", "做空报告"}
HEDGE = re.compile(r"\b(?:could|may|might|consider\w*|weigh\w*|mulls?|calls? for|urg\w+|demand\w*|would|should|if|whether|"
                   r"plans? to|poised to|expected to|reportedly|preparing to|seeks?|seeking|rumou?r\w*|talks|near\w*|close to|"
                   r"pressure\w*|refus\w*|won'?t|will not|denies|denied|no plans|speculat\w+|likely)\b|\?", re.I)
OPT_EXITS = {"d21": "第 21 天卖", "d42": "第 42 天卖", "settle42": "落地那天卖，没落地第 42 天",
             "take15": "到 1.5 倍卖，否则第 42 天", "take2": "翻倍卖，否则第 42 天", "take3": "3 倍卖，否则第 42 天",
             "trail": "翻倍后回撤 40% 卖，否则第 42 天"}


def themes_of(headline: str) -> list[str]:
    if not headline or ADS.search(headline):
        return []
    h = headline.lower()
    return [k for k, pat in THEME_RE.items() if pat.search(h)]


def settle_kind(headline: str) -> str | None:
    """'人走了' / '案子了结' when the headline says it happened, None when it only might."""
    if not headline or ADS.search(headline) or HEDGE.search(headline):
        return None
    return next((k for k, pat in SETTLE.items() if pat.search(headline)), None)


def reaction(created_at: str, days: list[str]) -> tuple[int, bool] | None:
    """(index of the session the headline can first move, known before that session's open)."""
    t = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(ET)
    d = t.date().isoformat()
    i = bisect_right(days, d) if t.hour >= 16 else bisect_left(days, d)
    if i >= len(days):
        return None
    return i, days[i] != d or (t.hour, t.minute) < (9, 30)


def scan(items: list[dict], sym: str, days: list[str]) -> tuple[list[tuple], list[tuple]]:
    """A stock's pressure headlines [(session index, themes, headline)] and settling ones
    [(session index, kind, known before the open, headline)], oldest first."""
    hits, ends = [], []
    for n in items:
        tickers = n.get("symbols") or []
        h = n.get("headline") or ""
        if sym not in tickers or len(tickers) > 3 or not n.get("created_at"):
            continue
        th, kind = themes_of(h), settle_kind(h)
        if not th and not kind:
            continue
        r = reaction(n["created_at"], days)
        if r is None:
            continue
        if th:
            hits.append((r[0], th, h))
        if kind:
            ends.append((r[0], kind, r[1], h))
    return sorted(hits, key=lambda x: x[0]), sorted(ends, key=lambda x: x[0])


def find_chains(hits: list[tuple], ends: list[tuple], days: list[str], sym: str = "") -> list[dict]:
    """Chains for one stock (see the module docstring)."""
    n = len(days)
    pref = [0] * (n + 1)
    per = [0] * n
    for i, _, _ in hits:
        per[i] += 1
    for i in range(n):
        pref[i + 1] = pref[i] + per[i]
    roll = lambda t: pref[t + 1] - pref[max(t - WINDOW + 1, 0)]
    out, t = [], WINDOW + WARMUP
    while t < n:
        lo = max(t - WINDOW - BASE + 1, 0)
        usual = (pref[t - WINDOW + 1] - pref[lo]) / (t - WINDOW + 1 - lo) * WINDOW
        need = max(MIN_HITS, MULT * usual)
        win = [h for h in hits if t - WINDOW < h[0] <= t] if roll(t) >= need else []
        themes = sorted({k for h in win for k in h[1]})
        if not win or len({h[0] for h in win}) < 2 or len(themes) < 2:
            t += 1
            continue
        floor, e = max(2, need / 2), t
        while e + 1 < n and roll(e + 1) >= floor:
            e += 1
        body = [h for h in hits if win[0][0] <= h[0] <= e]
        legal = bool(LEGAL_THEMES & {k for h in body for k in h[1]})
        end = next((s for s in ends if t <= s[0] <= min(body[-1][0] + GAP, n - 1) and (s[1] == "人走了" or legal)), None)
        out.append({"symbol": sym, "start": days[win[0][0]], "confirmed": days[t], "last": days[body[-1][0]],
                    "open": e == n - 1 and end is None, "hits": len(body), "at_confirm": len(win), "usual": round(usual, 2),
                    "themes": sorted({k for h in body for k in h[1]}),
                    "settled": days[end[0]] if end else None, "settle_kind": end[1] if end else None,
                    "settle_before_open": end[2] if end else None, "settle_headline": end[3] if end else None,
                    "headlines": [h[2] for h in win[:3]], "latest": body[-1][2]})
        t = e + 1
    return out


def measure(c: dict, bars: dict[str, tuple[float, float]], spy: dict[str, tuple[float, float]], days: list[str]) -> dict:
    """Stock moves for one chain; bars[day] = (open, close), adjusted. `days` are the stock's own sessions."""
    ix = {d: i for i, d in enumerate(days)}
    i0, ic = ix[c["start"]], ix[c["confirmed"]]
    if i0 == 0:
        return c
    both = lambda a, b: all(d in s for d in (a, b) for s in (bars, spy))
    cc = lambda a, b, s: s[b][1] / s[a][1] - 1
    pre = days[i0 - 1]
    if not both(pre, c["confirmed"]):
        return c
    c["pre_move"] = round(cc(pre, c["confirmed"], bars), 4)
    c["dir"] = 1 if c["pre_move"] > 0 else -1
    for k in HOLDS:
        if ic + k < len(days) and both(c["confirmed"], days[ic + k]):
            c[f"f{k}"] = round(cc(c["confirmed"], days[ic + k], bars), 4)
            c[f"f{k}_spy"] = round(cc(c["confirmed"], days[ic + k], spy), 4)
    if c.get("settled"):
        j, sd = ix[c["settled"]], c["settled"]
        o, cl = bars[sd]
        c["to_settle"] = round(cl / bars[c["confirmed"]][1] - 1, 4)
        c["settle_gap"] = round(o / bars[days[j - 1]][1] - 1, 4)
        c["settle_oc"] = round(cl / o - 1, 4)
        for k in HOLDS:
            if j + k < len(days) and both(sd, days[j + k]):
                c[f"s{k}"] = round(cc(sd, days[j + k], bars), 4)
                c[f"s{k}_spy"] = round(cc(sd, days[j + k], spy), 4)
    return c


def stock_trades(chains: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """(day, return per $1) per idea: along the chain's direction after it is confirmed, against it once settled."""
    out: dict[str, list[tuple[str, float]]] = {}
    add = lambda name, day, r: out.setdefault(name, []).append((day, r))
    for c in chains:
        s = c.get("dir")
        if not s:
            continue
        for k in HOLDS:
            if c.get(f"f{k}") is not None:
                add(f"正股·确认后顺着方向拿 {k} 天", c["confirmed"], s * c[f"f{k}"])
                add(f"正股·确认后顺着方向拿 {k} 天、扣掉 SPY", c["confirmed"], s * (c[f"f{k}"] - c[f"f{k}_spy"]))
        if c.get("settle_oc") is not None and c.get("settle_before_open"):
            add("正股·落地消息在开盘前：开盘反着做、收盘出", c["settled"], -s * c["settle_oc"])
        for k in HOLDS:
            if c.get(f"s{k}") is not None:
                add(f"正股·落地日收盘反着做拿 {k} 天", c["settled"], -s * c[f"s{k}"])
                add(f"正股·落地日收盘反着做拿 {k} 天、扣掉 SPY", c["settled"], -s * (c[f"s{k}"] - c[f"s{k}_spy"]))
    return out


# ---- (B) long options ------------------------------------------------------------------------------------------

def monthlies(day: str, lo: int = DTE[0], hi: int = DTE[1]) -> list[str]:
    """The third Fridays between `lo` and `hi` calendar days after `day`, nearest first."""
    d0 = date.fromisoformat(day)
    a, b = d0 + timedelta(days=lo), d0 + timedelta(days=hi)
    out, y, m = [], a.year, a.month
    for _ in range(4):
        first = date(y, m, 1)
        third = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        if a <= third <= b:
            out.append(third.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def option_path(entry: float, marks: dict[str, float], sessions: list[str], expiry: str, strike: float, kind: str,
                stock: dict[str, float]) -> tuple[list[float], list[int], bool]:
    """The option's close on each later session (the last trade carried forward when it did not trade; what it
    is worth at expiry when it did not trade that day), how many sessions old each price is, and whether the
    path is complete (expired, or OPT_HOLD sessions)."""
    out, ages, last, age = [], [], entry, 0
    for d in sessions[:OPT_HOLD]:
        if d > expiry:
            return out, ages, True
        px = marks.get(d)
        if px is None and d == expiry and d in stock:
            px = max((stock[d] - strike) if kind == "call" else (strike - stock[d]), 0.0)
        last, age = (last, age + 1) if px is None else (px, 0)
        out.append(last)
        ages.append(age)
        if d == expiry:
            return out, ages, True
    return out, ages, len(out) >= OPT_HOLD


def option_exits(cost: float, path: list[float], done: bool, settle_k: int | None = None, slip: float = SLIP) -> dict:
    """Per $1 paid. Time exits at the 21st / 42nd session close (the last close when the option ends sooner);
    the settled day's close (`settle_k` sessions after entry) or else the 42nd; a limit at 1.5x / 2x / 3x the cost
    within 42 sessions, filled at the limit on the first close at or above it; a 40% giveback after 2x.
    Also the best close within 42 sessions (as a multiple of the cost, before the selling cost) and its session."""
    sell = lambda c: round(max(c * (1 - slip), 0.0) / cost - 1, 4)
    res: dict = {}
    if not path:
        return res
    for k in (21, 42):
        if len(path) >= k:
            res[f"d{k}"] = sell(path[k - 1])
        elif done:
            res[f"d{k}"] = sell(path[-1])
    seg = path[:42]
    if "d42" not in res:
        return res                      # the 42 sessions are not over yet: nothing below can be scored
    res["settle42"] = sell(path[settle_k - 1]) if settle_k and 1 <= settle_k <= len(seg) else res["d42"]
    for name, mult in (("take15", 1.5), ("take2", 2.0), ("take3", 3.0)):
        res[name] = round(mult * (1 - slip) - 1, 4) if any(c >= mult * cost for c in seg) else res["d42"]
    best, armed, res["trail"] = 0.0, False, res["d42"]
    for c in seg:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail"] = sell(c)
            break
    res["peak"] = round(max(seg) / cost, 3)
    res["peak_day"] = seg.index(max(seg)) + 1
    return res


def contracts(client, und: str, exp: str, kind: str, pace, misses: dict, cache: dict) -> list[dict]:
    """Every `kind` on the monthly expiry `exp` (or the Thursday before it), cached."""
    key = (und, exp, kind)
    if key in cache:
        return cache[key]
    status = "inactive" if exp < date.today().isoformat() else "active"
    cs: list[dict] = []
    try:
        cs = client.option_contracts(und, status=status, type=kind,
                                     expiration_date_gte=(date.fromisoformat(exp) - timedelta(days=1)).isoformat(),
                                     expiration_date_lte=exp)
    except Exception as e:
        misses[f"contracts:{type(e).__name__}"] = misses.get(f"contracts:{type(e).__name__}", 0) + 1
    pace.wait()
    if not cs:
        misses["no_contracts"] = misses.get("no_contracts", 0) + 1
    cache[key] = cs
    return cs


def long_leg(client, und: str, day: str, kind: str, days: list[str], raw: dict[str, float], factor: dict[str, float],
             settle_day: str | None, pace, misses: dict, cache: dict) -> dict | None:
    """The at-the-money `kind` bought at `day`'s close on the nearest monthly expiry 75-130 days out that traded
    that day. None when there is no such contract, or a split falls between the entry and the expiry."""
    miss = lambda k: misses.__setitem__(k, misses.get(k, 0) + 1)
    i = bisect_left(days, day)
    spot, sessions = raw.get(day), days[i + 1:i + 1 + OPT_HOLD]
    if not spot:
        return miss("no_spot")
    for exp in monthlies(day):
        last = min(exp, days[-1])
        f0, f1 = factor.get(day), factor.get(days[bisect_right(days, last) - 1])
        if not f0 or not f1 or abs(f1 / f0 - 1) > 0.2:
            return miss("split_before_expiry")
        ks = [(float(c["strike_price"]), c["symbol"], c["expiration_date"]) for c in contracts(client, und, exp, kind, pace, misses, cache)]
        if not ks:
            continue
        k = min(ks, key=lambda x: abs(x[0] - spot))
        if abs(k[0] / spot - 1) > 0.05:
            miss("strike_far")
            continue
        bars = cache.setdefault("bars", {})
        if k[1] not in bars:
            got: dict = {}
            try:
                got = client.option_bars([k[1]], day, f"{min(k[2], sessions[-1] if sessions else day)}T23:00:00Z", timeframe="1Day")
            except Exception as e:
                miss(f"bars:{type(e).__name__}")
            pace.wait()
            bars[k[1]] = {b["t"][:10]: float(b["c"]) for b in got.get(k[1], [])}
        marks = bars[k[1]]
        if not marks.get(day):
            continue
        path, ages, done = option_path(marks[day], marks, sessions, k[2], k[0], kind, raw)
        settle_k = (bisect_left(days, settle_day) - i) if settle_day and settle_day > day else None
        res = option_exits(marks[day] * (1 + SLIP), path, done, settle_k)
        return {"option": k[1], "kind": kind, "strike": k[0], "expiry": k[2], "entry": marks[day], "done": done,
                "dte": (date.fromisoformat(k[2]) - date.fromisoformat(day)).days, "res": res,
                "stale21": ages[20] if len(ages) >= 21 else None, "stale42": ages[41] if len(ages) >= 42 else None}
    return miss("no_entry_bar")


def option_trades(chains: list[dict]) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    names = {"along": "期权·确认日买、顺着方向", "against": "期权·确认日买、反方向",
             "settle_fade": "期权·落地日买、反着之前的方向", "settle_along": "期权·落地日买、顺着之前的方向"}
    for c in chains:
        for leg, name in names.items():
            L = (c.get("legs") or {}).get(leg)
            if not L:
                continue
            day = c["settled"] if leg.startswith("settle") else c["confirmed"]
            for ex, label in OPT_EXITS.items():
                if ex == "settle42" and leg != "along":
                    continue
                if leg.startswith("settle") and ex not in ("d21", "d42", "take2"):
                    continue
                if L["res"].get(ex) is not None:
                    out.setdefault(f"{name} · {label}", []).append((day, L["res"][ex]))
    return out


def peaks(chains: list[dict], leg: str) -> dict | None:
    """Where the options of one kind peaked within 42 sessions: for choosing a take-profit level."""
    ps = [(L["res"]["peak"], L["res"]["peak_day"]) for c in chains for L in [(c.get("legs") or {}).get(leg)]
          if L and L["res"].get("peak") is not None]
    if not ps:
        return None
    m = sorted(p for p, _ in ps)
    q = lambda f: m[min(int(len(m) * f), len(m) - 1)]
    return {"n": len(ps), "median_peak": q(0.5), "q75": q(0.75), "q90": q(0.9),
            "reach15": sum(p >= 1.5 for p in m) / len(m), "reach2": sum(p >= 2 for p in m) / len(m),
            "reach3": sum(p >= 3 for p in m) / len(m), "median_peak_day": statistics.median(d for _, d in ps)}


def run(client, root: Path, budget_min: float = BUDGET_MIN) -> dict:
    from hero import universe
    from hero.research_event_turn import Pace
    t0 = time.monotonic()
    pool = universe.load(root / "data" / "universe.json")["stocks"][:POOL]
    today = date.today().isoformat()
    adj: dict[str, dict[str, tuple[float, float]]] = {}
    raw: dict[str, dict[str, float]] = {}
    for i in range(0, len(pool), 50):
        syms = pool[i:i + 50] + (["SPY"] if i == 0 else [])
        for s, bs in client.daily_bars(syms, START, adjustment="all").items():
            adj[s] = {b["t"][:10]: (float(b["o"]), float(b["c"])) for b in bs}
        for s, bs in client.daily_bars(syms, START, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    spy = adj.get("SPY", {})
    chains, scanned, pressure, sdays = [], 0, 0, {}
    for sym in pool:
        bars = adj.get(sym)
        if not bars:
            continue
        try:
            items = list(client.news_range([sym], f"{START}T00:00:00Z", f"{today}T23:59:59Z"))
        except Exception:
            continue
        scanned += len(items)
        sdays[sym] = sorted(bars)
        hits, ends = scan(items, sym, sdays[sym])
        pressure += len(hits)
        chains += [measure(c, bars, spy, sdays[sym]) for c in find_chains(hits, ends, sdays[sym], sym)]
    news_min = round((time.monotonic() - t0) / 60, 1)
    pace, misses, cache, priced, partial = Pace(client), {}, {}, 0, False
    for c in sorted(chains, key=lambda c: c["confirmed"], reverse=True):     # newest first: a short budget keeps the recent ones
        if not c.get("dir"):
            continue
        if (time.monotonic() - t0) / 60 > budget_min:
            partial = True
            break
        sym, days = c["symbol"], sdays[c["symbol"]]
        factor = {d: raw[sym][d] / adj[sym][d][1] for d in days if d in raw.get(sym, {}) and adj[sym][d][1]}
        along, against = ("call", "put") if c["dir"] > 0 else ("put", "call")
        legs = {}
        try:
            legs["along"] = long_leg(client, sym, c["confirmed"], along, days, raw[sym], factor, c.get("settled"), pace, misses, cache)
            legs["against"] = long_leg(client, sym, c["confirmed"], against, days, raw[sym], factor, None, pace, misses, cache)
            if c.get("settled"):
                legs["settle_fade"] = long_leg(client, sym, c["settled"], against, days, raw[sym], factor, None, pace, misses, cache)
                legs["settle_along"] = long_leg(client, sym, c["settled"], along, days, raw[sym], factor, None, pace, misses, cache)
        except Exception as e:
            misses[f"leg:{type(e).__name__}"] = misses.get(f"leg:{type(e).__name__}", 0) + 1
        c["legs"] = {k: v for k, v in legs.items() if v}
        priced += 1
    table = {k: robust.summary(v, end=today, extra_cost=STOCK_COST) for k, v in stock_trades(chains).items()}
    table.update({k: robust.summary(v, end=today) for k, v in option_trades(chains).items()})
    return {"generated": today, "start": START, "pool": len(pool), "headlines": scanned, "pressure": pressure,
            "chains": len(chains), "settled": sum(1 for c in chains if c.get("settled")), "priced": priced,
            "partial": partial, "misses": misses, "news_minutes": news_min,
            "minutes": round((time.monotonic() - t0) / 60, 1), "table": table,
            "peaks": {leg: peaks(chains, leg) for leg in ("along", "against", "settle_fade", "settle_along")},
            "chain_list": chains}


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def markdown(rep: dict) -> str:
    cs = [c for c in rep["chain_list"] if c.get("dir")]
    cut = lambda s, n=80: (s or "")[:n].replace("|", "/")
    lines = [f"# 消息链：坏消息一条接一条的时候和落地以后 {rep['generated']}", "",
             f"{rep['start']} 起 {rep['pool']} 只股票的 Alpaca（Benzinga）标题 {rep['headlines']:,} 条，其中压力标题 {rep['pressure']:,} 条；"
             f"找到 {rep['chains']} 条链，{rep['settled']} 条有「落地」的标题。链的定义：5 个交易日里至少 {MIN_HITS} 条压力标题、"
             f"是这只股票平时的 {MULT:.0f} 倍以上、分布在两天以上、两类以上。方向 = 链开始前一天收盘到确认那天收盘的涨跌。"
             "只研究，不改任何交易规则。", ""]
    for k in HOLDS:
        xs = [c for c in cs if c.get(f"f{k}") is not None]
        keep = sum(1 for c in xs if (c[f"f{k}"] > 0) == (c["dir"] > 0))
        lines.append(f"- 确认后 {k} 天接着往同一方向走：{keep} / {len(xs)}" + (f"（{keep / len(xs):.0%}）" if xs else ""))
    down = [c for c in cs if c["dir"] < 0]
    lines.append(f"- 方向向下的链 {len(down)} 条、向上的 {len(cs) - len(down)} 条")
    sett = [c for c in cs if c.get("settle_oc") is not None]
    turn = sum(1 for c in sett if (c["settle_oc"] > 0) != (c["dir"] > 0))
    lines += [f"- 落地那天开盘到收盘和之前方向相反：{turn} / {len(sett)}" + (f"（{turn / len(sett):.0%}）" if sett else ""), "",
              robust.table(rep["table"], title="稳健性检查（每 $1）"),
              f"正股各行的「多付成本」按每边 {STOCK_COST:.1%} 算，期权各行按每边 5%（表头写的是期权的口径）。期权已经按买贵 5%、卖便宜 5% 算过一遍。", "",
              "## 期权最高到过几倍（买入后 42 个交易日内的最高收盘价 ÷ 成本，用来定止盈）", "",
              "| 买法 | 笔数 | 中位数最高 | 最好的 1/4 至少到 | 最好的 1/10 至少到 | 到过 1.5 倍 | 到过 2 倍 | 到过 3 倍 | 最高点在第几天（中位数） |",
              "|---|---|---|---|---|---|---|---|---|"]
    label = {"along": "确认日买、顺着方向", "against": "确认日买、反方向", "settle_fade": "落地日买、反着之前的方向",
             "settle_along": "落地日买、顺着之前的方向"}
    for leg, p in rep["peaks"].items():
        if p:
            lines.append(f"| {label[leg]} | {p['n']} | {p['median_peak']:.2f} 倍 | {p['q75']:.2f} 倍 | {p['q90']:.2f} 倍 | "
                         f"{p['reach15']:.0%} | {p['reach2']:.0%} | {p['reach3']:.0%} | 第 {p['median_peak_day']:.0f} 天 |")
    stale = [L[k] for c in cs for L in (c.get("legs") or {}).values() for k in ("stale21", "stale42") if L.get(k) is not None]
    lines += ["", f"期权定价了 {rep['priced']} 条链" + ("（时间不够，只算了最近的一部分）" if rep["partial"] else "") +
              f"；没找到的原因：{rep['misses'] or '无'}。"
              + (f"卖出那天期权没有成交、用了 3 天以前旧价格的占 {sum(a > 3 for a in stale) / len(stale):.0%}。" if stale else ""), "",
              "## 现在还在发酵的链（还没有落地的标题）", ""]
    now = sorted([c for c in rep["chain_list"] if c.get("open")], key=lambda c: c["last"], reverse=True)
    for c in now[:20]:
        lines.append(f"- **{c['symbol']}** {c['start']} 起 {c['hits']} 条（{'、'.join(c['themes'])}；平时 5 天 {c['usual']} 条），"
                     f"确认 {c['confirmed']}，到确认时 {pct(c.get('pre_move'))}。最新：{cut(c['latest'], 100)}")
    lines += [] if now else ["- 没有"]
    recent = sorted([c for c in cs if c.get("settled")], key=lambda c: c["settled"], reverse=True)
    lines += ["", "## 落地过的链（最近 30 条）", "",
              "| 股票 | 开始 | 确认 | 落地 | 条数 | 类别 | 到确认 | 确认到落地 | 落地日跳空 | 落地日开到收 | 落地后 21 天 | 落地标题 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in recent[:30]:
        lines.append(f"| {c['symbol']} | {c['start']} | {c['confirmed']} | {c['settled']} | {c['hits']} | {'、'.join(c['themes'])} | "
                     f"{pct(c.get('pre_move'))} | {pct(c.get('to_settle'))} | {pct(c.get('settle_gap'))} | {pct(c.get('settle_oc'))} | "
                     f"{pct(c.get('s21'))} | {c['settle_kind']}：{cut(c['settle_headline'], 60)} |")
    big = sorted(cs, key=lambda c: -c["hits"])[:20]
    lines += ["", "## 最长的 20 条链（对一下是不是我们记得的那些事）", "",
              "| 股票 | 开始 | 确认 | 最后一条 | 条数 | 类别 | 到确认 | 之后 21 天 | 之后 42 天 | 开头的标题 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for c in big:
        lines.append(f"| {c['symbol']} | {c['start']} | {c['confirmed']} | {c['last']} | {c['hits']} | {'、'.join(c['themes'])} | "
                     f"{pct(c.get('pre_move'))} | {pct(c.get('f21'))} | {pct(c.get('f42'))} | {cut(c['headlines'][0], 70)} |")
    lines += ["", "提醒：标题按关键词挑，会混进无关的「调查」「听证」；名单是今天的 100 只（活下来的公司）；"
              "「落地」只看标题字面，盘中才出的落地消息当天开盘做不到，所以落地后的买卖一律从当天收盘算；"
              "期权用每天的收盘成交价，不成交的日子沿用上一次的价格。第一版，结论只记录。", ""]
    return "\n".join(lines)


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_ideas import save
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    save("news-chains", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
