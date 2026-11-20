"""目标 (the goal book): a plan ledger that is given a target and switches methods by itself to chase it.

The user, 2026-11-20: "如果我让你到达一个目标 它会自己切换吗" / "比如每天赚500" / "你自研下". Nothing here places an
order or changes config: it reads the multi-ledger journals, picks methods by a fixed rule from what was already
on record, and writes down what that would have made, beside the target and beside not choosing at all.

The lines below were written on the design day and are not tuned after seeing results; a change is a new
version recorded next to this one.

- Target: $500 a trading day on average, on a $100,000 book.
- Stake: at most $500 of premium a day (so the target asks for +100% per $1 on average).
- Loss line: once the month is down $5,000 the book is flat until the next month; the day's stake is never more
  than what is left before that line, so the month cannot lose more than $5,000.
- What it may switch between: the multi-ledger methods (real and plan ledgers of the same rule count as one
  method), or nothing. No leverage, no larger stake to catch up.
- When: once a week, from trades closed before that week's Monday. A method is eligible with at least 3 weeks
  and 10 closed trades on record, a profit overall, and still a profit without its best week. The three with
  the highest result per $1 without their best week share the day's stake equally; none eligible = flat.

A day's result for a method is (what its trades closed that day brought back - what they cost) / what they cost.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero import robust
from hero.feature_pages import TITLE

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "goal"
ET = ZoneInfo("America/New_York")

BOOK = 100_000.0
TARGET_DAY = 500.0
STAKE_DAY = 500.0
MONTH_STOP = 5_000.0
MIN_WEEKS, MIN_TRADES, TOP = 3, 10, 3
VARIANT = {"old": "旧规则", "r2": "第 2 名", "r3": "第 3 名", "opt": "买期权", "off": "不下注的日子"}


def method(ledger: str) -> str:
    """The rule a ledger follows: its id without the size and without the plan mark ("sniper-r2-plan-500" -> "sniper-r2")."""
    return re.sub(r"-plan$", "", re.sub(r"-(500|1000|2000)$", "", ledger))


def title(m: str) -> str:
    base = re.sub(r"-(old|opt|off|r\d+)$", "", m)
    extra, name = m[len(base):].strip("-"), TITLE.get(base, base)
    return f"{name}（{VARIANT.get(extra, extra)}）" if extra else name


def load(root: Path = ROOT / "journal-s500") -> list[dict]:
    """Every closed trade of the multi-ledger account, real and plan: the method, the day it closed, cost and proceeds."""
    out = []
    for name in ("sleeves.json", "sleeves_plan.json"):
        path = root / name
        if not path.exists():
            continue
        for sid, s in json.loads(path.read_text()).get("sleeves", {}).items():
            for c in s.get("closed", []):
                paid = float(c.get("paid") or 0)
                if paid > 0 and c.get("closed"):
                    out.append({"method": method(sid), "day": str(c["closed"])[:10], "paid": paid, "got": float(c.get("proceeds") or 0),
                                "plan": name != "sleeves.json" or bool(c.get("sim"))})
    return sorted(out, key=lambda t: (t["day"], t["method"]))


def monday(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def standing(trades: list[dict], before: str) -> dict[str, dict]:
    """Each method's record from the trades closed before `before`: in all, and without its best week."""
    rec: dict[str, dict] = {}
    for t in trades:
        if t["day"] >= before:
            continue
        m = rec.setdefault(t["method"], {"n": 0, "weeks": {}, "days": set()})
        m["n"] += 1
        m["days"].add(t["day"])
        w = m["weeks"].setdefault(monday(t["day"]), [0.0, 0.0])
        w[0] += t["got"] - t["paid"]
        w[1] += t["paid"]
    out = {}
    for name, m in rec.items():
        pnl, paid = sum(w[0] for w in m["weeks"].values()), sum(w[1] for w in m["weeks"].values())
        best = max(m["weeks"].values(), key=lambda w: w[0])
        rest_pnl, rest_paid = pnl - best[0], paid - best[1]
        out[name] = {"n": m["n"], "weeks": len(m["weeks"]), "days": len(m["days"]), "pnl": pnl, "paid": paid, "per": pnl / paid,
                     "rest_pnl": rest_pnl, "rest": rest_pnl / rest_paid if rest_paid > 0 else None,
                     "worst": min(w[0] / w[1] for w in m["weeks"].values())}
    return out


def eligible(s: dict) -> bool:
    return s["weeks"] >= MIN_WEEKS and s["n"] >= MIN_TRADES and s["pnl"] > 0 and s["rest_pnl"] > 0


def pick(trades: list[dict], before: str) -> list[str]:
    """The methods the book uses in the week that starts on `before`, from what closed earlier."""
    ok = sorted(((s["rest"], name) for name, s in standing(trades, before).items() if eligible(s)), reverse=True)
    return [name for _, name in ok[:TOP]]


def by_day(trades: list[dict]) -> dict[str, dict[str, list[float]]]:
    """day -> method -> [profit, cost] of the trades closed that day."""
    out: dict[str, dict[str, list[float]]] = {}
    for t in trades:
        x = out.setdefault(t["day"], {}).setdefault(t["method"], [0.0, 0.0])
        x[0] += t["got"] - t["paid"]
        x[1] += t["paid"]
    return out


def walk(trades: list[dict], days: list[str], decided: dict[str, list[str]] | None = None) -> list[dict]:
    """Day by day: the week's methods (the recorded decision when there is one), the stake the loss line allows,
    and what that stake made on the methods that closed trades that day. Beside it: the same stake spread over
    every method that closed a trade that day, without choosing and without the loss line."""
    daily, picks, month, out = by_day(trades), {}, {}, []
    for d in days:
        wk = monday(d)
        if wk not in picks:
            picks[wk] = (decided or {}).get(wk) or pick(trades, wk)
        room = MONTH_STOP + month.get(d[:7], 0.0)
        stake = max(0.0, min(STAKE_DAY, room))
        today = daily.get(d, {})
        used = [m for m in picks[wk] if m in today]
        result = sum(stake / len(used) * today[m][0] / today[m][1] for m in used) if used and stake else 0.0
        month[d[:7]] = month.get(d[:7], 0.0) + result
        every = statistics.mean(x[0] / x[1] for x in today.values()) * STAKE_DAY if today else 0.0
        out.append({"day": d, "week": wk, "picks": picks[wk], "used": used, "stake": stake if used else 0.0, "result": result,
                    "month": month[d[:7]], "stopped": room <= 0, "every": every})
    return out


def weeks_of(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        w = out.setdefault(r["week"], {"week": r["week"], "last": r["day"], "picks": r["picks"], "days": 0, "staked": 0.0, "result": 0.0,
                                       "every": 0.0, "stopped": False})
        w["last"], w["days"] = r["day"], w["days"] + 1
        w["staked"] += r["stake"]
        w["result"] += r["result"]
        w["every"] += r["every"]
        w["month"] = r["month"]
        w["stopped"] = w["stopped"] or r["stopped"]
    return list(out.values())


def account(path: Path) -> dict[str, tuple[float, float | None]]:
    """day -> (equity, benchmark) from an account's equity.csv; the last row of a day wins."""
    out = {}
    if path.exists():
        for r in csv.DictReader(path.read_text().splitlines()):
            try:
                out[r["date"]] = (float(r["equity"]), float(r["benchmark"]) if r.get("benchmark") else None)
            except (ValueError, KeyError):
                continue
    return out


def week_change(series: dict[str, float], week: str, last: str) -> tuple[float, float] | None:
    """A series' value before the week began and at `last`."""
    before = [d for d in series if d < week]
    return (series[max(before)], series[last]) if before and last in series else None


def needs(trades: list[dict], before: str, days: int) -> list[dict]:
    """For each method with enough trades: what a day's $500 target would ask of it."""
    out = []
    for name, s in standing(trades, before).items():
        if s["n"] < MIN_TRADES:
            continue
        share = s["days"] / days if days else 0.0
        out.append({"method": name, **s, "share": share, "eligible": eligible(s),
                    "stake": TARGET_DAY / (s["per"] * share) if s["per"] > 0 and share else None})
    return sorted(out, key=lambda r: -r["per"])


def usd(x: float, sign: bool = True) -> str:
    return f"{'+' if sign and x > 0 else '−' if x < 0 else ''}${abs(x):,.0f}"


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}".replace("-", "−")


def names(ms: list[str]) -> str:
    return "、".join(title(m) for m in ms) if ms else "空仓（没有够格的）"


def page(day: str, rows: list[dict], table: list[dict], spy: dict, main: dict, multi: dict, nxt: tuple[str, list[str], bool],
         checks: str, open_cost: dict[str, float]) -> str:
    wks = weeks_of(rows)
    total, n = sum(r["result"] for r in rows), len(rows)
    live = [r for r in rows if r["picks"]]
    out = [f"# 目标：{day}", "",
           "一个演算账本：给它一个目标，它每周按写死的规则在我们已经记分的做法之间换（`hero/goal.py`）。只记录，不下单，不改配置，不是投资建议。", "",
           "## 一、线（设计当天写死）", "",
           f"- 目标：平均每个交易日 {usd(TARGET_DAY, False)}，本金按 {usd(BOOK, False)} 算。",
           f"- 每天最多押 {usd(STAKE_DAY, False)}（所以目标等于要求平均每押 $1 赚 $1）。",
           f"- 亏损线：一个月亏到 {usd(MONTH_STOP, False)} 就空仓到月底；每天押的钱不超过离这条线还剩的数。",
           "- 能换的：多账本的各种做法（同一条规则的真实账本和演算账本算一种），或者空仓。不加杠杆，不为了追目标加大单笔。",
           f"- 怎么换：每周一次，只看这周一以前已经卖出的成交。够格 = 至少 {MIN_WEEKS} 周、{MIN_TRADES} 笔，总数是赚的，去掉最好的一周还是赚的。"
           f"按「去掉最好一周后每 $1 的结果」取前 {TOP} 名，平分当天的钱。", "",
           "## 二、如果从头就这么换", "",
           f"到 {rows[-1]['day']}，{n} 个交易日合计 {usd(total)}，平均每天 {usd(total / n)}；目标是每天 {usd(TARGET_DAY)}。"
           f"其中有做法可选的只有 {len(live)} 天。" if rows else "还没有记录。", "",
           "| 周（周一） | 选了什么 | 押了多少 | 结果 | 对目标 | 当月累计 | 不挑、每天平分 | 标普（$10 万） | 主账户规则（按 $10 万） | 多账本实际 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for w in wks:
        s, m, a = (week_change(x, w["week"], w["last"]) for x in (spy, main, multi))
        out.append(f"| {w['week'][5:]} | {names(w['picks'])}{'（碰了亏损线）' if w['stopped'] else ''} | {usd(w['staked'], False)} | {usd(w['result'])} | "
                   f"{usd(w['result'] - TARGET_DAY * w['days'])} | {usd(w['month'])} | {usd(w['every'])} | "
                   f"{usd((s[1] / s[0] - 1) * BOOK) if s else '—'} | {usd((m[1] / m[0] - 1) * BOOK) if m else '—'} | {usd(a[1] - a[0]) if a else '—'} |")
    out += ["", "「对目标」= 这一周的结果减去每天 $500；「不挑、每天平分」= 同样每天 $500，平分给当天有成交的所有做法，不设亏损线。"
            "规则是 11-20 才写的，上面这些周是回头走的，不是事先记的；从下一周起的选择事先记在 `ledger.json`。"]
    if open_cost:
        out += ["", "还开着、没算进上表的仓（按花的钱）：" + "；".join(f"{title(m)} {usd(c, False)}" for m, c in sorted(open_cost.items(), key=lambda kv: -kv[1])) + "。"]
    wk, ms, fixed = nxt
    out += ["", "## 三、下一周", "",
            f"{wk[5:]} 起的一周：**{names(ms)}**。" + ("（已记进账本）" if fixed else "（暂定：这一周还没走完，周六用到周五为止的成交再定、再记）"), "",
            "## 四、要到目标，每种做法得押多少", "",
            "每种做法到现在的记录（10 笔以上的）。「每天要押」= 想靠它平均每天赚 $500，按它到现在每 $1 的结果和出手的天数，平均每个交易日要押的钱。", "",
            "| 做法 | 笔数 | 周数 | 每 $1 | 去掉最好一周 | 最差的一周 | 出手的天数 | 每天要押 | 够格吗 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in table:
        out.append(f"| {title(r['method'])} | {r['n']} | {r['weeks']} | {pct(r['per'])} | {pct(r['rest'])} | {pct(r['worst'])} | {r['share']:.0%} | "
                   f"{usd(r['stake'], False) if r['stake'] else '赚不到'} | {'够' if r['eligible'] else '不够'} |")
    out += ["", checks, "",
            "## 说明", "",
            "- 每 $1 的结果是按花出去的钱加权的：账本越押越大的那些天占的分量更重。",
            "- 演算账本按卖价记买进，没有真的成交；真实账本的成交价 11-19 查出过有几笔按前一天收盘的报价成交（SPY 当天到期），那几笔的结果不可靠。",
            "- 主账户的规则和标普只列在旁边对照，没放进可以换的范围：它们用的是本金，不是每天 $500 的权利金，一个月能亏的不止 $5,000。侦察的三种做法要等有了按周估的现值再放进来。",
            "- 周数很少。这一页只算线索，不是结论。"]
    return "\n".join(out) + "\n"


def run(today: str | None = None, write: bool = True, root: Path = ROOT) -> dict:
    now = datetime.now(ET)
    today = today or now.date().isoformat()
    trades = [t for t in load(root / "journal-s500") if t["day"] <= today]
    main, multi = account(root / "journal" / "equity.csv"), account(root / "journal-s500" / "equity.csv")
    first = min((t["day"] for t in trades), default=today)
    days = sorted({d for d in list(main) + list(multi) if first <= d <= today} | {t["day"] for t in trades})
    lpath = OUT / "ledger.json"
    ledger = json.loads(lpath.read_text()) if lpath.exists() else {"decisions": []}
    decided = {x["week"]: x["picks"] for x in ledger["decisions"]}
    weekend = date.fromisoformat(today).weekday() >= 5
    nxt = (date.fromisoformat(monday(today)) + timedelta(days=7)).isoformat()
    if weekend and nxt not in decided:
        ledger["decisions"].append({"week": nxt, "decided": today, "picks": pick(trades, nxt)})
        decided[nxt] = ledger["decisions"][-1]["picks"]
    rows = walk(trades, days, decided)
    for r in rows:
        r["forward"] = r["week"] in decided
    took = [(t["day"], (t["got"] - t["paid"]) / t["paid"]) for t in trades for r in rows if r["day"] == t["day"] and t["method"] in r["used"]]
    since = min((r["day"] for r in rows if r["picks"]), default=today)
    everything = [(t["day"], (t["got"] - t["paid"]) / t["paid"]) for t in trades if t["day"] >= since]
    checks = robust.table({"它选中的成交": robust.summary(took, end=today), "同一段时间全部做法的成交": robust.summary(everything, end=today)},
                          title="稳健性检查（每笔成交每 $1 的结果）")
    open_cost: dict[str, float] = {}
    for name in ("sleeves.json", "sleeves_plan.json"):
        path = root / "journal-s500" / name
        for sid, s in (json.loads(path.read_text()).get("sleeves", {}) if path.exists() else {}).items():
            for c in s.get("open", []):
                if method(sid) in (rows[-1]["picks"] if rows else []):
                    open_cost[method(sid)] = open_cost.get(method(sid), 0.0) + float(c.get("paid") or 0)
    text = page(today, rows, needs(trades, (date.fromisoformat(today) + timedelta(days=1)).isoformat(), len(days)),
                {d: v[1] for d, v in main.items() if v[1]}, {d: v[0] for d, v in main.items()}, {d: v[0] for d, v in multi.items()},
                (nxt, decided.get(nxt) or pick(trades, nxt), nxt in decided), checks, open_cost)
    rep = {"day": today, "target_day": TARGET_DAY, "stake_day": STAKE_DAY, "month_stop": MONTH_STOP, "days": len(rows),
           "total": round(sum(r["result"] for r in rows), 2), "weeks": [{**w, "result": round(w["result"], 2), "every": round(w["every"], 2),
                                                                       "month": round(w["month"], 2)} for w in weeks_of(rows)],
           "next": {"week": nxt, "picks": decided.get(nxt) or pick(trades, nxt), "recorded": nxt in decided}}
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        lpath.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
        (OUT / f"{today}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
        (OUT / f"{today}.md").write_text(text)
    return {**rep, "page": text}


def main() -> None:
    got = run(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None, write="--dry" not in sys.argv)
    print(got["page"])


if __name__ == "__main__":
    main()
