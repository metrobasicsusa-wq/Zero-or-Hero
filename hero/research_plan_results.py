"""What would the plan-only rules have made? (owner, 10-21: a stopped rule must still show what it would have made.)

From SIM_FROM the engine follows every plan-only pick itself (hero.sleeves Runner.plan: booked at the ask, followed
on live quotes by the live exits; journal-s500/sleeves_plan.json, sleeve_plan_result events). This fills the days
before that: every plan-only pick with a size logged since FROM (the live sleeves began 10-12; before that every
sleeve was a rehearsal) is rebuilt from its sleeve_plan event -- the contract, N contracts at the logged ask (the
plan's limit less $0.01); the take, the half split, the trail and the time exit from the sleeve's spec and the event's
own words -- and run through the same exit code (Runner.plan_manage) every 10 minutes on one-minute option trades:
the latest trade stands in for the bid (no spread on the way out, so a little better than real), and a take counts
once a 10-minute check sees it. A pick with no trade after it cannot be valued and is listed, not counted; one still
held when the data ends is valued at its last trade.
Then the record per rule (both sizes together): the backfill, the engine's own results from SIM_FROM, and, for the
rules that were stopped, what the live ledgers made before the stop. Each with hero.robust's checks.
Record only. Run: python -m hero.research_plan_results
"""

from __future__ import annotations

import bisect
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from hero import robust, sleeves
from hero.research_0dte import ET
from hero.sleeve_rules import RULES, next_business_day

ROOT = Path(__file__).resolve().parent.parent
FROM = "2026-10-12"        # the live sleeves began; before that every sleeve was a rehearsal
SIM_FROM = "2026-10-22"    # the engine follows plan picks itself from this day on
SIZE = re.compile(r"(\d+) 张 × \$([\d.]+)")
UNTIL = re.compile(r"(?:否则|拿到) (\d\d)-(\d\d) (\d\d:\d\d) 卖")
TAKE = re.compile(r"([\d.]+) 倍止盈")
STEPS = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60, 10) if "09:40" <= f"{h:02d}:{m:02d}" <= "15:50"] + ["15:55"]
# the stopped rules' plan twins -> (the live family, the day its rule changed or stopped; live entries before it count)
LIVE_OF = {"gap-plan": ("gap", "2026-10-17"), "flush-plan": ("flush", "2026-10-17"), "overnight-plan": ("overnight", "2026-10-17"),
           "spy0dte-old-plan": ("spy0dte", "2026-10-21"), "earn-opt-plan": ("earn", "2026-10-19"), "sniper-plan": ("sniper", None)}


def family(sid: str) -> str:
    return re.sub(r"-(500|1000)$", "", sid)


def rebuild(e: dict, spec: dict) -> tuple[dict, int, str, str] | None:
    """(pick, contracts, day, HH:MM in New York) from a sleeve_plan event; None when it had no size."""
    m = SIZE.search(e.get("why", ""))
    sym = e.get("symbol") or ""
    if not m or int(m.group(1)) == 0 or len(sym) <= 15:
        return None
    t = datetime.fromisoformat(e["ts"]).astimezone(ET)
    day, hhmm, why = t.date().isoformat(), t.strftime("%H:%M"), e["why"]
    exp = sleeves.occ_expiry(sym)
    pk = {"sym": sym, "asset": "option", "und": sym[:-15], "ask": round(float(m.group(2)) - 0.01, 2), "expiry": exp, "why": why}
    u = UNTIL.search(why)
    if u:
        pk.update(exit_day=f"{day[:4]}-{u.group(1)}-{u.group(2)}", exit_at=u.group(3))
    elif "到期日 15:30" in why:                       # the sniper
        pk.update(exit_day=exp, exit_at="15:30")
    elif "第二天 09:35" in why:                       # the old earnings call
        pk.update(exit_day=next_business_day(t.date()).isoformat(), exit_at="09:35")
    elif "拿到 15:55" in why:                         # one shot
        pk.update(exit_day=day, exit_at="15:55", expiry_out="15:55")
    else:
        at = re.search(r"最晚 (\d\d:\d\d)", why) or re.search(r"(\d\d:\d\d) 平仓", why)
        pk.update(exit_day=day, exit_at=at.group(1) if at else "15:30")
    tk = TAKE.search(why)
    if tk and "不止盈" not in why:
        pk["take"] = float(tk.group(1))
        pk["half"] = bool(spec.get("half_take")) or "一半" in why
    pk["trail"] = "翻倍后回撤 40%" in why and "一半" not in why
    return pk, int(m.group(1)), day, hhmm


class Tape:
    """A stand-in broker for Runner.plan_manage: each contract's latest one-minute trade at or before `at`."""

    def __init__(self, bars: dict[str, list[dict]]):
        self.px: dict[str, list[tuple[str, float]]] = {}
        for sym, bs in bars.items():
            xs = []
            for b in bs:
                t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                xs.append((t.strftime("%Y-%m-%d %H:%M"), float(b["c"])))
            self.px[sym] = sorted(xs)
        self.at = ""

    def last(self, sym: str, at: str | None = None) -> tuple[str, float] | None:
        xs = self.px.get(sym) or []
        i = bisect.bisect_right(xs, (at or self.at, float("inf"))) - 1
        return xs[i] if i >= 0 else None

    def traded_after(self, sym: str, after: str) -> bool:
        xs = self.px.get(sym) or []
        return bisect.bisect_right(xs, (after, float("inf"))) < len(xs)

    def option_snapshots(self, syms: list[str]) -> dict:
        out = {}
        for s in syms:
            got = self.last(s)
            px = got[1] if got else 0.0
            out[s] = {"latestQuote": {"bp": px, "ap": px}}
        return out

    def stock_snapshots(self, syms: list[str]) -> dict:
        return {}


def weekdays(a: str, b: str) -> list[str]:
    d, out = date.fromisoformat(a), []
    while d.isoformat() <= b:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def replay(picks: list[tuple[dict, dict, int, str, str, dict]], tape: Tape, last_day: str) -> list[dict]:
    """Run each pick through the live exit code, 10 minutes at a time, on the tape. picks: (event, pick, qty, day,
    HH:MM, spec), oldest first. Returns one row per pick."""
    specs = list({p[5]["id"]: p[5] for p in picks}.values())
    with tempfile.TemporaryDirectory() as tmp:
        book = sleeves.Book(Path(tmp) / "plan.json", specs)
        r = sleeves.Runner(tape, SimpleNamespace(event=lambda kind, **k: None), {"sleeves": {}}, specs, book, RULES,
                           datetime(2026, 1, 1, 9, 30), date(2026, 1, 1), sleep=lambda s: None)
        todo = sorted(range(len(picks)), key=lambda i: (picks[i][3], picks[i][4]))
        days = weekdays(min(p[3] for p in picks), last_day) if picks else []
        for d in days:
            for hhmm in STEPS:
                r.today, r.hhmm = date.fromisoformat(d), hhmm
                r.now = datetime.fromisoformat(f"{d}T{hhmm}")
                tape.at = f"{d} {hhmm}"
                r.plan_manage()              # as in a cycle: the open tickets first, then the new picks
                while todo and (picks[todo[0]][3], picks[todo[0]][4]) <= (d, hhmm):
                    i = todo.pop(0)
                    e, pk, qty, day, at, spec = picks[i]
                    s = book.s(spec["id"])
                    before = {t["tid"] for t in s["open"]}
                    r.plan_open(spec, pk, qty, day)
                    for t in s["open"]:
                        if t["tid"] not in before:
                            t["src"] = i
        rows = []
        for i, (e, pk, qty, day, at, spec) in enumerate(picks):
            mine = [t for s in book.d["sleeves"].values() for t in s["open"] + s["closed"] if t.get("src") == i]
            paid = round(sum(t["paid"] for t in mine), 2)
            got, hows, held = 0.0, [], False
            for t in mine:
                if t.get("state") == "closed":
                    got += t["proceeds"]
                    hows.append(f"{t['qty']} 张 {t['how']}（{t['closed'][5:]}）")
                else:
                    last = tape.last(t["sym"], f"{last_day} 16:00")
                    got += (last[1] if last else 0.0) * t["qty"] * 100
                    hows.append(f"{t['qty']} 张还拿着（按 {last[0][5:] if last else '—'} 最后成交估值）")
                    held = True
            ok = tape.traded_after(pk["sym"], f"{day} {at}")
            rows.append({"day": day, "at": at, "sleeve": spec["id"], "family": family(spec["id"]), "sym": pk["sym"],
                         "qty": qty, "cost": pk["ask"], "paid": paid, "got": round(got, 2), "pnl": round(got - paid, 2),
                         "ret": round(got / paid - 1, 3) if paid else None, "how": "；".join(hows), "held": held,
                         "valued": ok, "src": "backfill"})
        return rows


def engine_rows(root: Path) -> list[dict]:
    """The engine's own plan results (journal-s500/sleeves_plan.json, tickets marked sim), one row per entry."""
    p = root / "journal-s500" / "sleeves_plan.json"
    if not p.exists():
        return []
    out: dict[tuple[str, str], dict] = {}
    for sid, s in json.loads(p.read_text())["sleeves"].items():
        for t in s["closed"] + s["open"]:
            if not t.get("sim"):
                continue
            k = (sid, str(t["tid"]).rstrip("rs"))
            row = out.setdefault(k, {"day": t["opened"][:10], "at": t["opened"][11:], "sleeve": sid, "family": family(sid),
                                     "sym": t["sym"], "qty": 0, "cost": t["cost"], "paid": 0.0, "got": 0.0, "how": [],
                                     "held": False, "valued": True, "src": "engine"})
            row["qty"] += t["qty"]
            row["paid"] += t["paid"]
            if t.get("state") == "closed":
                row["got"] += t["proceeds"]
                row["how"].append(f"{t['qty']} 张 {t['how']}（{t['closed'][5:]}）")
            else:
                row["held"] = True
                row["got"] += t["paid"]          # held: at cost until it closes
                row["how"].append(f"{t['qty']} 张还拿着（按成本计）")
    rows = []
    for row in out.values():
        row["paid"], row["got"] = round(row["paid"], 2), round(row["got"], 2)
        row.update(pnl=round(row["got"] - row["paid"], 2), ret=round(row["got"] / row["paid"] - 1, 3) if row["paid"] else None,
                   how="；".join(row["how"]))
        rows.append(row)
    return sorted(rows, key=lambda r: (r["day"], r["at"], r["sleeve"]))


def live_rows(root: Path) -> list[dict]:
    """What the stopped rules' live ledgers made before the stop (closed tickets, one row per entry)."""
    p = root / "journal-s500" / "sleeves.json"
    if not p.exists():
        return []
    want = {fam: cut for fam, cut in LIVE_OF.values()}
    out: dict[tuple[str, str], dict] = {}
    for sid, s in json.loads(p.read_text())["sleeves"].items():
        fam = family(sid)
        if fam not in want:
            continue
        cut = want[fam]
        still = {str(t["tid"]).rstrip("rs") for t in s["open"]}
        for t in s["closed"]:
            base = str(t["tid"]).rstrip("rs")
            if base in still or (cut and t["opened"][:10] >= cut):
                continue
            row = out.setdefault((sid, base), {"day": t["opened"][:10], "sleeve": sid, "family": fam, "sym": t["sym"],
                                               "paid": 0.0, "got": 0.0})
            row["paid"] += t["paid"]
            row["got"] += t["proceeds"]
    rows = []
    for row in out.values():
        row.update(paid=round(row["paid"], 2), got=round(row["got"], 2), pnl=round(row["got"] - row["paid"], 2),
                   ret=round(row["got"] / row["paid"] - 1, 3) if row["paid"] else None)
        rows.append(row)
    return rows


def tally(rows: list[dict], end: str) -> dict[str, dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("valued", True) and r.get("ret") is not None:
            by.setdefault(r["family"], []).append(r)
    out = {}
    for fam, rs in sorted(by.items()):
        paid, got = sum(r["paid"] for r in rs), sum(r["got"] for r in rs)
        out[fam] = {"n": len(rs), "paid": round(paid, 2), "got": round(got, 2), "net": round(got - paid, 2),
                    "per_dollar": round(got / paid - 1, 3) if paid else None, "wins": sum(r["pnl"] > 0 for r in rs),
                    "held": sum(bool(r.get("held")) for r in rs),
                    "robust": robust.summary([(r["day"], r["ret"]) for r in rs], end)}
    return out


def collect(root: Path) -> tuple[list[tuple], list[str]]:
    """The plan-only picks to backfill (FROM <= day < SIM_FROM, sleeves plan-only in today's config)."""
    specs = {s["id"]: s for s in json.loads((root / "config" / "s500.json").read_text())["sleeves"]["list"]}
    events = [json.loads(l) for l in (root / "journal-s500" / "trades.jsonl").read_text().splitlines() if l.strip()]
    picks, unsized = [], []
    for e in events:
        if e.get("kind") != "sleeve_plan" or not e.get("symbol"):
            continue
        spec = specs.get(e.get("sleeve"))
        if not spec or not spec.get("plan_only"):
            continue
        got = rebuild(e, spec)
        if not got:
            unsized.append(e["sleeve"])
            continue
        pk, qty, day, at = got
        if FROM <= day < SIM_FROM:
            picks.append((e, pk, qty, day, at, spec))
    return picks, unsized


def money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def markdown(rows: list[dict], eng: list[dict], live: list[dict], last_day: str, unsized: int) -> str:
    end = max([r["day"] for r in rows + eng] or [last_day])
    both = tally(rows + eng, end)
    lv = tally(live, end)
    pct = lambda v: "—" if v is None else f"{v:+.0%}"
    md = [f"# 停掉 / 只演算的规则：会赚多少 {date.today().isoformat()}", "",
          f"用户 10-21：停掉的规则也要记「会赚多少」。{FROM} 到 {SIM_FROM} 前是补算：每条只演算的选单按记录的张数、卖价买进，"
          "再用和实盘同一段卖出代码（止盈、一半止盈一半回撤、翻倍后回撤 40%、到时卖）每 10 分钟走一次分钟成交价，"
          f"成交数据到 {last_day}。用成交价当买价、没扣价差，比真实略好；止盈要 10 分钟检查时看到才算。"
          f"{SIM_FROM} 起由交易程序自己按实时报价记（按卖价买、按买价卖）。还拿着的按最后成交价（引擎记的按成本）估值。"
          f"买不起一张的选单不算（{unsized} 条）。只研究，不改交易。", "",
          "## 每条规则（两个账本合计）", "",
          "| 规则 | 笔数 | 花费 | 收回 | 盈亏 | 每 $1 | 赚钱笔数 | 还拿着 | 中位数 | 去掉最好 3 笔 | 提醒 | 停之前实盘（笔数 / 盈亏 / 每 $1） |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    live_of = {k: v[0] for k, v in LIVE_OF.items()}
    for fam, t in both.items():
        s = t["robust"] or {}
        l = lv.get(live_of.get(fam, ""), None)
        md.append(f"| {fam} | {t['n']} | ${t['paid']:,.2f} | ${t['got']:,.2f} | {money(t['net'])} | {pct(t['per_dollar'])} | "
                  f"{t['wins']} | {t['held']} | {pct(s.get('median'))} | {pct(s.get('trim'))} | {'；'.join(s.get('flags') or []) or '—'} | "
                  + (f"{l['n']} / {money(l['net'])} / {pct(l['per_dollar'])}" if l else "—") + " |")
    if not both:
        md.append("| （还没有） |  |  |  |  |  |  |  |  |  |  |  |")
    md += ["", "停之前实盘：gap / flush / 隔夜动量 10-16（含）以前开的单，SPY 0.6% 10-21 前、财报彩票看涨 10-19 前，狙击是全部已平的单。", "",
           "## 每一笔", "", "| 日期 | 时间 | 账本 | 合约 | 张数 × 价格 | 花费 | 收回 | 盈亏 | 怎么出 | 来源 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows + eng, key=lambda r: (r["day"], r["at"], r["sleeve"])):
        how = r["how"] if r.get("valued", True) else "买进后没有成交，估不了值（不计入）"
        md.append(f"| {r['day']} | {r['at']} | {r['sleeve']} | {r['sym']} | {r['qty']} × ${r['cost']:.2f} | ${r['paid']:,.2f} | "
                  f"${r['got']:,.2f} | {money(r['pnl'])} | {how} | {'补算' if r['src'] == 'backfill' else '程序实时'} |")
    return "\n".join(md) + "\n"


def last_full_day(today: date) -> str:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_ideas import save
    last_day = last_full_day(datetime.now(ET).date())     # the day's option bars are only out once the day is over
    picks, unsized = collect(ROOT)
    picks = [p for p in picks if p[3] <= last_day]
    syms = sorted({p[1]["sym"] for p in picks})
    bars = {}
    if syms:
        bars = Alpaca().option_bars(syms, f"{FROM}T13:00:00Z", f"{last_day}T21:00:00Z", timeframe="1Min")
    rows = replay(picks, Tape(bars), last_day) if picks else []
    eng, live = engine_rows(ROOT), live_rows(ROOT)
    md = markdown(rows, eng, live, last_day, len(unsized))
    save("plan-results", {"from": FROM, "sim_from": SIM_FROM, "last_day": last_day, "rows": rows, "engine": eng, "live": live}, md)


if __name__ == "__main__":
    sys.exit(main())
