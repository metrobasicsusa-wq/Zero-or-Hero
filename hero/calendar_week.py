"""一周日历: the known events of one week on one page, every date from a fixed table with its source (11-23: the
weekend routine wrote next week's calendar by hand from web searches and kept getting dates wrong or late -- it found
only on 11-23 that Friday 11-27 closes at 13:00, which the trading code does not handle).

What is in it, and where each part comes from (all checked 2026-11-23; times are US Eastern):

  market hours  full closures: hero.weekbook.holidays (NYSE's usual rules). Early closes at 13:00, NYSE's rules
                (https://www.nyse.com/markets/hours-calendars, footnotes: "Each market will close early at 1:00 p.m.
                (1:15 p.m. for eligible options) on Friday, November 27, 2026 ... (the day after Thanksgiving)", "...
                on Thursday, December 24, 2026", "... on Monday, July 3, 2028"):
                  - the day after Thanksgiving, every year;
                  - Christmas Eve when it is a session (Monday to Thursday; on a Friday it is the observed Christmas
                    closure, as on 2027-12-24);
                  - July 3 when it is a session (Monday to Thursday; on a Friday it is the observed Independence Day,
                    as on 2026-07-03, so 2026-07-02 is a full day).
                One-off closures and early closes (a national day of mourning) are not in it.
  options       the monthly expiry: the third Friday, the Thursday before when that Friday is closed (Good Friday).
                In March, June, September and December it is also the quarterly expiry (stock index futures, index
                and stock options on one day). End-of-quarter index options (SPX, XSP...) expire at the close of the
                quarter's last session (Cboe expiration calendar,
                https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf).
  the Fed       FOMC meetings 2026 and 2027 (FOMC below), from
                https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm (page "Last Update: November 18,
                2026"). The statement at 14:00 on the second day; the minutes three weeks after it at 14:00 --
                the published dates where the page shows them ("Released ..."), otherwise marked as worked out.
  US data       CPI, the jobs report, PCE (Personal Income and Outlays), GDP and retail sales released 2026-11 to
                2027-06 (DATA below), each from the agency's own schedule. Nothing guessed: a date not on the
                agency's schedule is not in the table.
  earnings      the names we care about -- data/universe.json (the stock pool), what journal/ and journal-s500/
                snapshot.json hold (an option's underlying), research/earnings-forecast/extra_watch.json -- with
                the dates and before / after the open from data/earnings.json, and how sure that file is of each
                (confirmed / not confirmed / never checked; Yahoo's other date).

When the week holds an early close the page opens with a warning that names the times the trading code
(hero/sleeves.py, hero/sleeve_rules.py) hard-codes after it, read from the code itself. Nothing here changes them.
Record only: nothing here places or changes an order.
Run: python -m hero.calendar_week [YYYY-MM-DD]   (the week holding that day -- by default today -- or, on a
weekend, the next week); writes research/calendar/<monday>.md and .json.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.weekbook import easter, holidays, nth, observed, target

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "calendar"
ET = ZoneInfo("America/New_York")
WEEKDAY = "一二三四五六日"
OPEN, CLOSE, EARLY, EARLY_OPTIONS = "09:30", "16:00", "13:00", "13:15"
CHECKED = "2026-11-23"
MINUTES_LAG = 21                   # the minutes come out three weeks after the decision
STALE_DAYS = 3                     # data/earnings.json older than this is called out (hero/earnings.py MAX_AGE_DAYS)
CODE = ("hero/sleeves.py", "hero/sleeve_rules.py")

NYSE = "https://www.nyse.com/markets/hours-calendars"
CBOE = "https://cdn.cboe.com/resources/options/Cboe2026OPTIONSCalendar.pdf"
FED = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# (first day, decision day, with the Summary of Economic Projections, minutes "Released ..." on the Fed's page, note).
# SEP None: the page has no asterisks on 2027 yet. Each meeting date is tentative until confirmed at the meeting
# before it (the page's note).
FOMC = (
    ("2026-01-27", "2026-01-28", False, "2026-02-18", ""),
    ("2026-03-17", "2026-03-18", True, "2026-04-08", ""),
    ("2026-04-28", "2026-04-29", False, "2026-05-20", ""),
    ("2026-06-16", "2026-06-17", True, "2026-07-08", ""),
    ("2026-07-28", "2026-07-29", False, "2026-08-19", ""),
    ("2026-09-15", "2026-09-16", True, "2026-10-07", ""),
    ("2026-10-27", "2026-10-28", False, "2026-11-18", ""),
    ("2026-12-08", "2026-12-09", True, None, ""),
    ("2027-01-26", "2027-01-27", None, None, ""),
    ("2027-03-16", "2027-03-17", None, None, ""),
    ("2027-04-27", "2027-04-28", None, None, ""),
    ("2027-06-08", "2027-06-09", None, None, ""),
    ("2027-07-27", "2027-07-28", None, None, ""),
    ("2027-09-14", "2027-09-15", None, None, ""),
    ("2027-10-26", "2027-10-27", None, None, "官网这一行写的是 Monetary Policy Strategy Meeting（货币政策框架会议），没写有没有利率决定"),
    ("2027-11-16", "2027-11-17", None, None, ""),
)
FOMC_NEXT = "2028-01-26"           # the page's note: "A two-day meeting is scheduled for January 25-26, 2028"

# Release date, the period it covers, a note. All 08:30 ET. Range: released 2026-11-01 to 2027-06-30.
DATA = {
    "cpi": {"name": "CPI（消费者物价）", "agency": "BLS", "time": "08:30",
            "source": "https://www.bls.gov/schedule/news_release/cpi.htm",
            "how": "BLS 的 CPI 发布表（直接下载被拒 403，用网页抓取工具读了两遍，一致；12 月的月度发布表 12-10 也对得上）",
            "rows": (("2026-11-10", "2026-10", ""), ("2026-12-10", "2026-11", ""), ("2027-01-13", "2026-12", ""),
                     ("2027-02-11", "2027-01", ""), ("2027-03-10", "2027-02", ""), ("2027-04-13", "2027-03", ""),
                     ("2027-05-12", "2027-04", ""), ("2027-06-10", "2027-05", ""))},
    "jobs": {"name": "非农就业报告（Employment Situation）", "agency": "BLS", "time": "08:30",
             "source": "https://www.bls.gov/schedule/news_release/empsit.htm",
             "how": "BLS 的就业报告发布表（同上用网页抓取工具读；12 月的月度发布表 12-04 也对得上）",
             "rows": (("2026-11-06", "2026-10", ""), ("2026-12-04", "2026-11", ""), ("2027-01-08", "2026-12", ""),
                      ("2027-02-05", "2027-01", ""), ("2027-03-05", "2027-02", ""), ("2027-04-02", "2027-03", ""),
                      ("2027-05-07", "2027-04", ""), ("2027-06-04", "2027-05", ""))},
    "pce": {"name": "PCE（个人收入和支出）", "agency": "BEA", "time": "08:30",
            "source": "https://www.bea.gov/news/schedule",
            "how": "BEA 的发布表（2026 全年页和 2027 页），和 https://apps.bea.gov/API/signup/release_dates.json 一致",
            "rows": (("2026-11-25", "2026-10", ""), ("2026-12-23", "2026-11", ""), ("2027-01-28", "2026-12", ""),
                     ("2027-02-25", "2027-01", ""), ("2027-03-31", "2027-02", ""), ("2027-04-29", "2027-03", ""),
                     ("2027-05-27", "2027-04", ""), ("2027-06-25", "2027-05", ""))},
    "gdp": {"name": "GDP", "agency": "BEA", "time": "08:30",
            "source": "https://www.bea.gov/news/schedule",
            "how": "同 PCE（BEA 发布表和 release_dates.json 一致）",
            "rows": (("2026-11-25", "2026Q3", "第二次估计"), ("2026-12-23", "2026Q3", "第三次估计"),
                     ("2027-01-28", "2026Q4", "初值"), ("2027-02-25", "2026Q4", "第二次估计"),
                     ("2027-03-31", "2026Q4", "第三次估计"), ("2027-04-29", "2027Q1", "初值"),
                     ("2027-05-27", "2027Q1", "第二次估计"), ("2027-06-25", "2027Q1", "第三次估计"))},
    "retail": {"name": "零售销售（Advance Monthly Retail Trade）", "agency": "Census", "time": "08:30",
               "source": "https://www.census.gov/retail/release_schedule.html",
               "how": "人口普查局的零售发布表；2027 年的和 https://www.census.gov/economic-indicators/calendar-listview.html 一致",
               "rows": (("2026-11-17", "2026-10", ""), ("2026-12-16", "2026-11", ""), ("2027-01-15", "2026-12", ""),
                        ("2027-02-17", "2027-01", ""), ("2027-03-16", "2027-02", ""), ("2027-04-16", "2027-03", ""),
                        ("2027-05-14", "2027-04", ""), ("2027-06-16", "2027-05", ""))},
}
DATA_FROM, DATA_TO = "2026-11-01", "2027-06-30"
NOT_IN = "PPI、ISM、JOLTS、每周初请失业金、密歇根信心、褐皮书、美联储官员讲话、国债拍卖"


# ---------- market hours ----------
def holiday_names(y: int) -> dict[date, str]:
    """hero.weekbook.holidays(y) with names."""
    named = ((observed(date(y, 1, 1)), "元旦"), (nth(y, 1, 0, 3), "马丁·路德·金纪念日"), (nth(y, 2, 0, 3), "总统日"),
             (easter(y) - timedelta(days=2), "耶稣受难日"), (nth(y, 5, 0, -1), "阵亡将士纪念日"),
             (observed(date(y, 6, 19)), "六月节"), (observed(date(y, 7, 4)), "独立日"), (nth(y, 9, 0, 1), "劳动节"),
             (nth(y, 11, 3, 4), "感恩节"), (observed(date(y, 12, 25)), "圣诞节"))
    hs = holidays(y)
    return {d: n for d, n in named if d in hs}


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def early_close(day: date) -> str | None:
    """"13:00" when NYSE closes early that day (the docstring's three rules), else None."""
    if not is_session(day):
        return None
    if day == nth(day.year, 11, 3, 4) + timedelta(days=1) or (day.month, day.day) in ((12, 24), (7, 3)):
        return EARLY
    return None


def monthly_expiry(y: int, m: int) -> date:
    d = nth(y, m, 4, 3)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def last_session(y: int, m: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def expiries(day: date) -> list[str]:
    out = []
    if day == monthly_expiry(day.year, day.month):
        s = "月度期权到期"
        if day.month % 3 == 0:
            s += "；也是季度到期（股指期货、指数期权、个股期权同一天到期）"
        if day.weekday() != 4:
            s += "（第三个周五休市，提前到这一天）"
        out.append(s)
    if day.month % 3 == 0 and day == last_session(day.year, day.month):
        out.append("季末指数期权到期（SPX、XSP 等，收盘时）")
    fri = day + timedelta(days=4 - day.weekday())
    if day.weekday() < 4 and not is_session(fri) and all(not is_session(day + timedelta(days=k)) for k in range(1, 4 - day.weekday() + 1)):
        out.append("周五休市，这一周的周期权提前到这一天到期")
    if early_close(day):
        out.append(f"今天到期的期权 {EARLY} 停止交易（可延长交易的 {EARLY_OPTIONS}）")
    return out


# ---------- the Fed and the data ----------
def fomc_events() -> list[dict]:
    out = []
    for first, decide, sep, minutes, note in FOMC:
        d = date.fromisoformat(decide)
        tag = f"{first[5:]}–{decide[8:]}"
        out.append({"date": first, "kind": "fomc", "time": "", "text": "FOMC 会议第一天"})
        text = "14:00 FOMC 利率决定（14:30 主席记者会）" + ("，附经济预测（点阵图）" if sep else "")
        out.append({"date": decide, "kind": "fomc", "time": "14:00", "text": text + (f"；{note}" if note else "")})
        if minutes:
            out.append({"date": minutes, "kind": "fomc_minutes", "time": "14:00", "text": f"14:00 FOMC 会议纪要（{tag} 那次）", "published": True})
        elif not note:
            out.append({"date": (d + timedelta(days=MINUTES_LAG)).isoformat(), "kind": "fomc_minutes", "time": "14:00",
                        "text": f"14:00 FOMC 会议纪要（{tag} 那次；推算：会后三周，官网还没列）", "published": False})
    return out


def period(p: str, released: str) -> str:
    if "Q" in p:
        return f"{'' if p[:4] == released[:4] else p[:4] + ' 年'}{'一二三四'[int(p[-1]) - 1]}季度"
    return f"{'' if p[:4] == released[:4] else p[:4] + ' 年 '}{int(p[5:])} 月"


def data_events() -> list[dict]:
    out = []
    for key, d in DATA.items():
        for day, p, note in d["rows"]:
            what = period(p, day) + (f"，{note}" if note else "")
            out.append({"date": day, "kind": key, "time": d["time"], "text": f"{d['time']} {d['name']}：{what}"})
    return out


# ---------- our names' earnings ----------
OCC = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")


def underlying(sym: str) -> str:
    m = OCC.match(sym)
    return m.group(1) if m else sym


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def held(root: Path) -> dict[str, list[dict]]:
    """Account name -> positions, with the underlying and (options) the expiry."""
    out = {}
    for folder, name in (("journal", "主账户"), ("journal-s500", "多账本")):
        rows = []
        for p in load(root / folder / "snapshot.json").get("positions") or []:
            sym = str(p.get("symbol") or "")
            m = OCC.match(sym)
            rows.append({"symbol": sym, "und": underlying(sym),
                         **({"expiry": f"20{m.group(2)[:2]}-{m.group(2)[2:4]}-{m.group(2)[4:]}", "right": m.group(3),
                             "strike": int(m.group(4)) / 1000} if m else {})})
        out[name] = rows
    return out


def watch(root: Path) -> tuple[dict[str, list[str]], set[str]]:
    """Symbol -> why we care (stock pool / held by an account / extra watch list), and the pool's ETFs."""
    uni = load(root / "data" / "universe.json")
    why: dict[str, list[str]] = {}
    for s in uni.get("stocks") or []:
        why.setdefault(s, []).append("股票池")
    for name, rows in held(root).items():
        for r in rows:
            if f"{name}持有" not in why.setdefault(r["und"], []):
                why[r["und"]].append(f"{name}持有")
    for s in load(root / "research" / "earnings-forecast" / "extra_watch.json").get("symbols") or []:
        why.setdefault(str(s), []).append("关注名单")
    return why, set(uni.get("etfs") or [])


SLOT = {"pre-market": "盘前", "post-market": "盘后"}


def trust(r: dict) -> str:
    if r.get("confirmed") is True:
        s = "已确认"
    elif r.get("confirmed") is False:
        s = "**未确认**"
    else:
        s = "**没核对**（只有 Alpha Vantage 的日期）"
    if r.get("yahoo_date"):
        s += f"；Yahoo 说 {r['yahoo_date']}"
    if r.get("disputed"):
        s += "；Nasdaq 那几天都没列"
    return s


def earnings(root: Path, mon: date, today: date) -> dict:
    """Our names' reports in the week (and rows whose other source's date falls in it), plus what the file can't see."""
    cal = load(root / "data" / "earnings.json")
    why, etfs = watch(root)
    days = {(mon + timedelta(days=k)).isoformat() for k in range(5)}
    rows = []
    for sym, rs in (cal.get("reports") or {}).items():
        if sym not in why:
            continue
        for r in rs:
            alt = r.get("yahoo_date")
            if r["date"] not in days and alt not in days:
                continue
            day = r["date"] if r["date"] in days else alt
            deep = root / "research" / "earnings-forecast" / "deep" / f"{sym}-{r['date']}.md"
            rows.append({"date": day, "symbol": sym, "slot": SLOT.get(r.get("time") or "", "时间未定"), "why": why[sym],
                         "calendar_date": r["date"], "other_source": day != r["date"], "confirmed": r.get("confirmed"),
                         "trust": trust(r), "deep": deep.relative_to(root).as_posix() if deep.exists() else None})
    rows.sort(key=lambda x: (x["date"], {"盘前": 0, "时间未定": 1, "盘后": 2}[x["slot"]], x["symbol"]))
    listed = set(cal.get("symbols") or [])
    blind = sorted(s for s in why if s not in listed and s not in etfs)
    fetched = cal.get("fetched_on")
    age = (today - date.fromisoformat(fetched)).days if fetched else None
    gap = None
    if not fetched:
        gap = "没读到 data/earnings.json"
    elif (mon + timedelta(days=4)).isoformat() < fetched:
        gap = f"data/earnings.json 只列 {fetched} 以后要发的，这一周在那之前"
    elif mon > date.fromisoformat(fetched) + timedelta(days=int(cal.get("horizon_days") or 0)):
        gap = f"这一周超出了 data/earnings.json 的范围（{fetched} 往后 {cal.get('horizon_days')} 天）"
    return {"rows": rows, "not_in_calendar": blind, "fetched_at": cal.get("fetched_at"), "age_days": age, "gap": gap, "names": len(why)}


# ---------- the times the trading code hard-codes ----------
def scopes(tree: ast.AST, prefix: str = "") -> list[tuple[int, int, str]]:
    """(first line, last line, Class.method) of every function in the tree."""
    out = []
    for n in ast.iter_child_nodes(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = prefix + n.name
            if not isinstance(n, ast.ClassDef):
                out.append((n.lineno, n.end_lineno or n.lineno, name))
            out += scopes(n, name + ".")
    return out


def hardcoded(after: str = EARLY, root: Path = ROOT) -> list[dict]:
    """Every "HH:MM" string literal later than `after` in the trading code, with the function holding it and what it
    is: a sell time, an entry window or the last cycle. Read from the code, so the list follows the code."""
    out = []
    for rel in CODE:
        path = root / rel
        try:
            src = path.read_text()
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        lines = src.splitlines()
        funcs = sorted(scopes(tree), key=lambda x: x[1] - x[0])                  # innermost first
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"\d\d:\d\d", n.value) and n.value > after):
                continue
            line = lines[n.lineno - 1]
            func = next((name for a, b, name in funcs if a <= n.lineno <= b), "")
            if line.lstrip().startswith("LAST_CYCLE"):
                kind = "最后一轮交易"
            elif "hhmm" in line and not re.search(r"exit|expiry_out|LAST_CYCLE", line + func):
                kind = "进场窗口"
            else:
                kind = "卖出时间"
            out.append({"file": rel, "line": n.lineno, "time": n.value, "func": func, "kind": kind})
    out.sort(key=lambda x: (x["file"], x["line"], x["time"]))
    return out


def summary(hc: list[dict]) -> str:
    by: dict[str, dict[str, int]] = {}
    for h in hc:
        by.setdefault(h["kind"], {}).setdefault(h["time"], 0)
        by[h["kind"]][h["time"]] += 1
    return "；".join(f"{k} " + "、".join(f"{t}（{n} 处）" for t, n in sorted(v.items())) for k, v in by.items())


# ---------- the week ----------
def label(d: date) -> str:
    return f"{d:%m-%d} 周{WEEKDAY[d.weekday()]}"


def option_name(r: dict) -> str:
    return f"{r['und']} {r['strike']:g}{r['right']}"


def week(mon: date, root: Path = ROOT, today: date | None = None) -> dict:
    today = today or datetime.now(ET).date()
    days = [mon + timedelta(days=k) for k in range(5)]
    iso = {d.isoformat() for d in days}
    events = [e for e in fomc_events() + data_events() if e["date"] in iso]
    earn = earnings(root, mon, today)
    hold = held(root)
    out = []
    for d in days:
        name = holiday_names(d.year).get(d)
        ec = early_close(d)
        expiring = {acct: [option_name(r) for r in rows if r.get("expiry") == d.isoformat()] for acct, rows in hold.items()}
        out.append({"date": d.isoformat(), "weekday": f"周{WEEKDAY[d.weekday()]}", "open": is_session(d),
                    "holiday": name, "close": None if not is_session(d) else (ec or CLOSE), "early_close": ec,
                    "data": [e["text"] for e in events if e["date"] == d.isoformat() and e["kind"] in DATA],
                    "fed": [e["text"] for e in events if e["date"] == d.isoformat() and e["kind"].startswith("fomc")],
                    "earnings": [r for r in earn["rows"] if r["date"] == d.isoformat()],
                    "expiry": expiries(d) if is_session(d) else [],
                    "held_expiring": {a: xs for a, xs in expiring.items() if xs}})
    early = [x for x in out if x["early_close"]]
    hc = hardcoded() if early else []
    friday = days[-1].isoformat()
    nxt = {}
    for kind, name in (("fomc", "下一次 FOMC 利率决定"), ("cpi", "下一次 CPI"), ("jobs", "下一次非农"), ("pce", "下一次 PCE"),
                       ("gdp", "下一次 GDP"), ("retail", "下一次零售销售")):
        pool = [e for e in fomc_events() + data_events() if e["kind"] == kind and e["date"] > friday and e["time"]]
        nxt[name] = min(pool, key=lambda e: e["date"])["date"] if pool else None
    d, tries = days[-1] + timedelta(days=1), 0
    while not (early_close(d) or (d.weekday() < 5 and not is_session(d))) and tries < 400:
        d, tries = d + timedelta(days=1), tries + 1
    nxt["下一个休市或提前收盘"] = d.isoformat() if tries < 400 else None
    y, m = days[-1].year, days[-1].month
    x = monthly_expiry(y, m)
    if x.isoformat() <= friday:
        x = monthly_expiry(y + (m == 12), m % 12 + 1)
    nxt["下一个月度期权到期"] = x.isoformat()
    gaps = []
    if not (DATA_FROM <= mon.isoformat() and friday <= DATA_TO):
        gaps.append(f"这一周不全在数据表的范围里（{DATA_FROM} 到 {DATA_TO} 发布的），表外的数据发布没写，不是没有")
    if not (FOMC[0][0] <= mon.isoformat() and friday < FOMC_NEXT):
        gaps.append(f"这一周不在美联储表的范围里（{FOMC[0][0]} 到 {FOMC_NEXT} 之前），表外的会议没写，不是没有")
    return {"week": mon.isoformat(), "sessions": sum(x["open"] for x in out), "days": out, "early_closes": [x["date"] for x in early],
            "hardcoded": hc, "earnings": earn, "next": nxt, "gaps": gaps,
            "coverage": {"data": [DATA_FROM, DATA_TO], "fomc_to": FOMC[-1][1], "fomc_next_listed": FOMC_NEXT},
            "sources": {"market": NYSE, "options": CBOE, "fed": FED, **{k: v["source"] for k, v in DATA.items()}}}


def page(w: dict) -> str:
    mon = date.fromisoformat(w["week"])
    early, halves = w["early_closes"], len(w["early_closes"])
    head = f"# 一周日历：{w['week']} 这一周（{w['sessions']} 个交易日" + (f"，{halves} 个半天" if halves else "") + "）"
    out = [head, ""]
    if early:
        hc = w["hardcoded"]
        exp = "；".join(f"{a}：{'、'.join(xs)}" for x in w["days"] if x["early_close"] for a, xs in x["held_expiring"].items())
        when = "、".join(label(date.fromisoformat(d)) for d in early)
        out += [f"> **注意：{when} {EARLY} 提前收盘（可延长交易的期权 {EARLY_OPTIONS}）。** {EARLY} 以后引擎不再跑交易轮（先问券商开没开盘），"
                f"交易代码里写死在 {EARLY} 以后的时间那天都等不到：{summary(hc) or '没读到'}。"
                f"后果：那天到期的期权，我们自己的 15:30 卖出不会发生，{EARLY} 收盘就到期；「当天 15:50 卖」的票拖到下一个交易日第一轮才卖；"
                f"15:40 / 15:45 才开始的进场那天不进场。" + (f"那天到期的持仓——{exp}。" if exp else "")
                + "明细见文末「写死的时间」。只提醒，不改代码。", ""]
    out += ["模拟盘研究，只记录，不是投资建议。日期都来自写死的表（来源和核对的日子见文末），没核对过的不写。时间都是美东。", ""]
    out += ["| 日期 | 交易时间 | 数据 | 美联储 | 我们在意的财报 | 期权到期 |", "|---|---|---|---|---|---|"]
    for x in w["days"]:
        d = date.fromisoformat(x["date"])
        if not x["open"]:
            hours = f"**休市**（{x['holiday'] or '休市'}）"
        elif x["early_close"]:
            hours = f"{OPEN}–{x['early_close']} **提前收盘**"
        else:
            hours = f"{OPEN}–{CLOSE}"
        er = "、".join(f"{r['symbol']} {r['slot']}" + ("（另一来源的日期）" if r["other_source"] else
                                                       {True: "", False: "（未确认）"}.get(r["confirmed"], "（没核对）"))
                       for r in x["earnings"])
        ex = x["expiry"] + [f"持仓到期——{a}：{'、'.join(xs)}" for a, xs in x["held_expiring"].items()]
        cells = (label(d), hours, "；".join(x["data"]), "；".join(x["fed"]), er, "；".join(ex))
        out.append("| " + " | ".join(c or "—" for c in cells) + " |")
    out += [f"\n**{g}。**" for g in w["gaps"]]
    e = w["earnings"]
    out += ["", "## 我们在意的财报", "",
            f"名单 {e['names']} 只：股票池、两个账户持有的（期权按标的）、关注名单（extra_watch.json）。日期和盘前盘后来自 data/earnings.json"
            f"（{e['fetched_at'] or '没读到'} 取的" + (f"，**已经 {e['age_days']} 天没更新**" if e["age_days"] is not None and e["age_days"] > STALE_DAYS else "") + "）。"]
    if e["gap"]:
        out += ["", f"**{e['gap']}：没列出的不等于没有财报。**"]
    if e["rows"]:
        out += ["", "| 日期 | 公司 | 时间 | 为什么在意 | 日期可信吗 | 研究页 |", "|---|---|---|---|---|---|"]
        for r in e["rows"]:
            when = label(date.fromisoformat(r["date"])) + (f"（日历写 {r['calendar_date']}）" if r["other_source"] else "")
            deep = f"[{r['deep'].split('/')[-1]}](../../{r['deep']})" if r["deep"] else "—"
            out.append(f"| {when} | {r['symbol']} | {r['slot']} | {'、'.join(r['why'])} | {r['trust']} | {deep} |")
    else:
        out += ["", "这一周没有我们在意的财报（以 data/earnings.json 为准）。"]
    if e["not_in_calendar"]:
        out += ["", f"data/earnings.json 不查的（日期要另找，公司公告为准）：{'、'.join(e['not_in_calendar'])}。"]
    out += ["", "## 往后看", ""]
    out += [f"- {k}：{v}（周{WEEKDAY[date.fromisoformat(v).weekday()]}）" if v else f"- {k}：表里没有" for k, v in w["next"].items()]
    out += ["", "## 来源", "",
            f"都是 {CHECKED} 核对的。数据表只写了 {DATA_FROM[:7]} 到 {DATA_TO[:7]} 发布的；美联储写到 {FOMC[-1][1][:7]}（官网下一次是 {FOMC_NEXT}）。"
            f"没写进来的：{NOT_IN}。", "",
            f"- 交易时间和提前收盘：纽交所 {NYSE}（休市沿用 hero/weekbook.py 的规则）",
            f"- 期权到期：第三个周五（休市就提前一天）；季末指数期权：Cboe {CBOE}",
            f"- 美联储：{FED}（官网「Last Update: November 18, 2026」；2026 年的纪要日期是官网写的，之后的按会后三周推算，标了「推算」）"]
    out += [f"- {v['name']}：{v['source']}——{v['how']}" for v in DATA.values()]
    if early:
        out += ["", f"## 写死的时间（{EARLY} 以后，从代码里读的）", "",
                "| 文件 | 函数 | 种类 | 时间 | 行 |", "|---|---|---|---|---|"]
        groups: dict[tuple, list[dict]] = {}
        for h in w["hardcoded"]:
            groups.setdefault((h["file"], h["func"] or "（模块）", h["kind"]), []).append(h)
        for (f, fn, kind), hs in groups.items():
            lines = "、".join(str(n) for n in sorted({h["line"] for h in hs}))
            out.append(f"| {f} | {fn} | {kind} | {'、'.join(sorted({h['time'] for h in hs}))} | {lines} |")
    return "\n".join(out) + "\n"


def run(day: date | None = None, root: Path = ROOT, out_dir: Path = OUT, write: bool = True, today: date | None = None) -> dict:
    today = today or datetime.now(ET).date()
    mon = target(day or today)
    w = week(mon, root, today)
    w["written"] = datetime.now(ET).isoformat(timespec="minutes")
    text = page(w)
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{w['week']}.md").write_text(text)
        (out_dir / f"{w['week']}.json").write_text(json.dumps(w, ensure_ascii=False, indent=1) + "\n")
    return {**w, "page": text}


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(day)["page"])


if __name__ == "__main__":
    sys.exit(main())
