"""Many small accounts inside one paper account: each direction gets its own $500 and $1,000 ledger.

The plan (docs/sleeves-plan.md): every direction runs as if it were a real small account. A sleeve has
its own cash; it bets a fraction (half) of that cash per entry, never the account's; whole contracts only.
When the fraction cannot buy one contract (or one whole share) but what is left of the ledger can, it buys
one (owner, 10-22: a ledger below the price of two contracts but above the zero line must not sit out for
good -- QQQ 0DTE had not traded since 10-14). Below 10% of its start the round is over (a zero);
at $10,000 it is a hero; either way it starts again at its start from the shared pool, and the round
count is kept. Every broker order carries a client_order_id naming the sleeve and the ticket, and
every fill is booked from the broker's own fill price, so the ledgers add up to the real account.

The ledger lives in the journal, which a cycle commits at its end; if that commit is lost, the broker still
has the orders. So a buy's ticket id starts with its slot (sl-<sleeve>-<slot>-<random>-b1), and each cycle
first reads the day's orders from the broker: a buy no ledger knows is booked from the broker's fills
(recover), and a slot the broker already has a buy for is never bought again.

Live entries (all of a cycle's buys at once): a limit buy_pad above the ask; after fill_wait_s, the
unfilled are cancelled and re-quoted once at the new ask + buy_pad (never above chase_cap x the first
ask); still unfilled -> cancelled, no trade for that slot. A take, when the rule has one, rests at the
broker as a sell limit. Exits (time, trail, a strategy's own signal) cancel that ticket's take first,
then sell that ticket's own quantity -- never the whole position, which other sleeves may share.
This module holds the ledger and the order handling; hero.sleeve_rules decides what to buy.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {"enabled": False, "fraction": 0.5, "zero_line": 0.1, "target": 10_000.0, "buy_pad": 0.01,
            "fill_wait_s": 60, "chase_cap": 1.3, "cash_buffer": 1_000.0}
LAST_CYCLE = "15:50"   # the day's last trading cycle (the engine only runs while the market is open)


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in cfg.get("sleeves", {}).items() if k != "list"}}


def tick(x: float) -> float:
    step = 0.05 if x >= 3 else 0.01
    return round(round(x / step) * step, 2)


def multiplier(t: dict) -> int:
    return 100 if t["asset"] == "option" else 1


def units(each: float, left: float, price: float) -> tuple[int, bool]:
    """Whole units (contracts at `price` x 100, or whole shares at `price`) for one pick: as many as its share
    `each` of the ledger buys; when that is not even one but the ledger's `left` cash covers one, one (all in).
    Returns (units, all_in)."""
    if price <= 0:
        return 0, False
    q = int(each // price)
    if q >= 1:
        return q, False
    return (1, True) if price <= left else (0, False)


class Book:
    """journal/sleeves.json: {"sleeves": {id: ledger}, "topups": dollars drawn from the pool}."""

    def __init__(self, path: Path, specs: list[dict], carry: dict[str, float] | None = None):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {"sleeves": {}, "topups": 0.0}
        for s in specs:
            if s["id"] not in self.d["sleeves"]:
                start = float(s["start"])
                cash = float((carry or {}).get(s["id"], start))
                self.d["sleeves"][s["id"]] = {"start": start, "cash": round(cash, 2), "round": 1, "heroes": 0, "zeros": 0,
                                               "open": [], "closed": [], "slots": {}, "best": cash, "created": None}

    def save(self) -> None:
        for s in self.d["sleeves"].values():
            s["closed"] = s["closed"][-int(s.get("keep", 200)):]   # a sample ledger keeps more (hero.sleeve_rules.FadeLotto)
            s["slots"] = dict(sorted(s["slots"].items())[-20:])
        self.path.write_text(json.dumps(self.d, indent=1, ensure_ascii=False) + "\n")

    def s(self, sid: str) -> dict:
        return self.d["sleeves"][sid]

    def open_ticket(self, sid: str, t: dict) -> None:
        s = self.s(sid)
        s["cash"] = round(s["cash"] - t["paid"], 2)
        s["open"].append(t)

    def close_ticket(self, sid: str, t: dict, price: float, how: str, when: str) -> dict:
        s = self.s(sid)
        proceeds = round(price * t["qty"] * multiplier(t), 2)
        s["cash"] = round(s["cash"] + proceeds, 2)
        s["best"] = max(s["best"], s["cash"])
        s["open"] = [x for x in s["open"] if x["tid"] != t["tid"]]
        done = {**t, "state": "closed", "exit_price": price, "proceeds": proceeds, "how": how, "closed": when,
                "ret": round(proceeds / t["paid"] - 1, 3) if t["paid"] else 0.0}
        s["closed"].append(done)
        return done

    def round_check(self, sid: str, p: dict) -> str | None:
        """Only when flat: below the zero line or at the target, the round ends and restarts at the start."""
        s = self.s(sid)
        if s["open"]:
            return None
        cash = s["cash"]
        if cash < s["start"] * p["zero_line"] or cash >= p["target"]:
            hero = cash >= p["target"]
            s["heroes" if hero else "zeros"] += 1
            self.d["topups"] = round(self.d["topups"] + s["start"] - cash, 2)  # a hero hands its winnings back
            msg = (f"{sid} 第 {s['round']} 轮" + (f"到 ${p['target']:,.0f}，成功" if hero else f"低于 ${s['start'] * p['zero_line']:,.0f}，归零")
                   + f"（${cash:,.2f}），从 ${s['start']:,.0f} 重开第 {s['round'] + 1} 轮")
            s["round"] += 1
            s["cash"] = s["start"]
            s["best"] = s["start"]
            return msg
        return None

    def summary(self) -> list[dict]:
        out = []
        for sid, s in self.d["sleeves"].items():
            held = sum(t["paid"] for t in s["open"])
            out.append({"id": sid, "start": s["start"], "cash": s["cash"], "in_trades": round(held, 2), "round": s["round"],
                        "heroes": s["heroes"], "zeros": s["zeros"], "trades": len(s["closed"]),
                        "wins": sum(t["ret"] > 0 for t in s["closed"])})
        return out


def new_tid() -> str:
    return uuid.uuid4().hex[:10]


def cid(sid: str, tid: str, tag: str) -> str:
    return f"sl-{sid}-{tid}-{tag}"


def slot_key(slot: str) -> str:
    """A slot as it appears in a buy's ticket id: letters and digits only (2026-W42 -> 2026W42)."""
    return "".join(ch for ch in slot if ch.isalnum())


SLOT_TID = re.compile(r"[A-Za-z0-9]+-[0-9a-f]{6}")


def entry_tid(slot: str) -> str:
    return f"{slot_key(slot)}-{uuid.uuid4().hex[:6]}"


def finished_entries(s: dict) -> tuple[int, float]:
    """(entries whose tickets are all closed, their net dollars). A runner half ("...r") and a recovered sold part
    ("...s") belong to their buy's ticket id; an entry with a ticket still open is not finished."""
    base = lambda tid: str(tid).rstrip("rs")
    still = {base(t["tid"]) for t in s["open"]}
    net: dict[str, float] = {}
    for t in s["closed"]:
        b = base(t["tid"])
        if b not in still:
            net[b] = net.get(b, 0.0) + float(t.get("proceeds") or 0) - float(t.get("paid") or 0)
    return len(net), round(sum(net.values()), 2)


def occ_expiry(sym: str) -> str | None:
    """SPY261014P00768000 -> 2026-10-14."""
    if len(sym) <= 15 or not sym[-15:-9].isdigit():
        return None
    d = sym[-15:-9]
    return f"20{d[:2]}-{d[2:4]}-{d[4:]}"


class Runner:
    """One cycle of every sleeve against the broker. `rules` maps a kind to a hero.sleeve_rules function."""

    def __init__(self, client, journal, cfg: dict, specs: list[dict], book: Book, rules: dict, now: datetime,
                 today: date, sleep=time.sleep):
        self.c, self.j, self.cfg, self.specs, self.book, self.rules = client, journal, cfg, specs, book, rules
        self.p = settings(cfg)
        self.now, self.today, self.sleep = now, today, sleep
        self.hhmm = now.strftime("%H:%M")

    # ---- earnings guard (owner, 10-19: earnings only in shares or 2x funds; option IV collapses after the report) ----
    def report_inside(self, und: str | None, until: str | None) -> dict | None:
        """The underlying's report whose reaction falls after today and on or before `until` (the day an option
        ticket would be sold), from data/earnings.json; None when there is none (or no calendar)."""
        if not und or not until:
            return None
        if "_cal" not in self.__dict__:
            try:
                self._cal = (json.loads((ROOT / "data" / "earnings.json").read_text()).get("reports") or {})
            except Exception:
                self._cal = {}
        for r in self._cal.get(und, []):
            d = date.fromisoformat(r["date"])
            react = d
            if "post" in (r.get("time") or ""):
                react = d + timedelta(days=1)
                while react.weekday() >= 5:
                    react += timedelta(days=1)
            if self.today < react and react.isoformat() <= until:
                return r
        return None

    # ---- broker helpers ----
    def broker_orders(self) -> list[dict] | None:
        """The broker's orders (any status) since the start of the previous weekday -- a cycle late in the day whose
        commit was lost is caught the next morning -- read once per cycle; None if they could not be read."""
        if "_orders" not in self.__dict__:
            start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
            if start.tzinfo is None:
                start = start.replace(tzinfo=ZoneInfo("America/New_York"))
            start -= timedelta(days=1)
            while start.weekday() >= 5:
                start -= timedelta(days=1)
            try:
                self._orders = list(self.c.orders_since(start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) or [])
            except Exception as e:
                self.j.event("alert_error", source="sleeves:orders", error=str(e)[:200])
                self._orders = None
        return self._orders

    def bought_at_broker(self, sid: str, slot: str) -> bool:
        """The broker already has a buy for this sleeve and slot (sent by a cycle whose ledger was not saved)."""
        lead = f"sl-{sid}-{slot_key(slot)}-"
        return any((o.get("client_order_id") or "").startswith(lead) and o.get("side") == "buy" for o in self.broker_orders() or [])

    def order(self, client_order_id: str) -> dict:
        try:
            return self.c.order_by_client_id(client_order_id) or {}
        except Exception:
            return {}

    def cancel(self, client_order_id: str) -> dict:
        o = self.order(client_order_id)
        if o.get("id") and o.get("status") not in ("filled", "canceled", "expired", "rejected"):
            try:
                self.c.cancel_order(o["id"])
            except Exception:
                pass
            o = self.order(client_order_id)
        return o

    def submit(self, **order) -> bool:
        try:
            self.c.submit_order(**order)
            return True
        except Exception as e:
            self.j.event("order_error", reason="sleeve", error=str(e)[:300], symbol=order.get("symbol"))
            return False

    def bids(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        opts = [s for s in symbols if len(s) > 10]
        out = {}
        if opts:
            for s, v in (self.c.option_snapshots(opts) or {}).items():
                q = v.get("latestQuote") or {}
                out[s] = (float(q.get("bp") or 0), float(q.get("ap") or 0))
        stocks = [s for s in symbols if len(s) <= 10]
        if stocks:
            for s, v in (self.c.stock_snapshots(stocks) or {}).items():
                q, px = v.get("latestQuote") or {}, float((v.get("latestTrade") or {}).get("p") or 0)
                out[s] = (float(q.get("bp") or px), float(q.get("ap") or px))
        return out

    # ---- the cycle ----
    def run(self) -> None:
        self.recover()
        self.manage()
        for spec in self.specs:
            msg = self.book.round_check(spec["id"], {**self.p, **{k: spec[k] for k in ("zero_line", "target") if k in spec}})
            if msg:
                self.j.event("sleeve_round", dry_run=False, sleeve=spec["id"], why=msg)
        self.enter()

    def recover(self) -> None:
        """Book the buys the broker has (since the previous weekday) that no ledger knows: a cycle sent them and filled them, but its
        journal commit was lost. Open orders of such a ticket are cancelled; what was sold is booked as closed, what is
        still held as an open ticket on the trail, out at 15:30 on its expiry (stock: the rule's own exit)."""
        orders = self.broker_orders()
        if not orders:
            return
        known = [t["tid"] for s in self.book.d["sleeves"].values() for t in s["open"] + s["closed"]]
        specs = sorted(self.specs, key=lambda x: -len(x["id"]))
        parsed = []
        for o in orders:
            c = o.get("client_order_id") or ""
            spec = next((x for x in specs if c.startswith(f"sl-{x['id']}-")), None)
            if spec and "-" in c[len(spec["id"]) + 4:]:
                tid, _, tag = c[len(spec["id"]) + 4:].rpartition("-")
                parsed.append((spec, tid, tag, o))
        lost = {(spec["id"], tid): spec for spec, tid, tag, o in parsed  # only slot-keyed ids (older ids are left alone)
                if tag.startswith("b") and o.get("side") == "buy" and SLOT_TID.fullmatch(tid) and not any(k.startswith(tid) for k in known)}
        for (sid, tid), spec in lost.items():
            family = [(tag, o) for sp, t2, tag, o in parsed if sp["id"] == sid and t2.startswith(tid)]
            for tag, o in family:
                if o.get("status") in ("new", "accepted", "pending_new", "partially_filled", "held"):
                    o.update(self.cancel(o["client_order_id"]) or {})
            buys = [o for tag, o in family if o.get("side") == "buy"]
            sells = [o for tag, o in family if o.get("side") == "sell"]
            bought = sum(float(o.get("filled_qty") or 0) for o in buys)
            if not bought:
                continue
            cost = sum(float(o.get("filled_qty") or 0) * float(o.get("filled_avg_price") or 0) for o in buys) / bought
            sold = sum(float(o.get("filled_qty") or 0) for o in sells)
            got = sum(float(o.get("filled_qty") or 0) * float(o.get("filled_avg_price") or 0) for o in sells)
            sym = buys[0]["symbol"]
            option = len(sym) > 10
            mult = 100 if option else 1
            exp = occ_expiry(sym) if option else None
            base = {"kind": spec["kind"], "sym": sym, "asset": "option" if option else "stock", "und": sym[:-15] if option else sym,
                    "cost": round(cost, 4), "opened": f"{self.today.isoformat()} {self.hhmm}", "expiry": exp,
                    "exit_day": exp or (self.today.isoformat() if option else None), "exit_at": "15:30" if option else "15:50", "trail": option, "best_bid": 0.0,
                    "why": "从券商找回：这笔买入的账本没保存上（上一轮提交失败）"}
            if sold:
                t = {**base, "tid": tid + "s", "qty": sold, "paid": round(cost * sold * mult, 2)}
                self.book.open_ticket(sid, t)
                self.closed(sid, t, round(got / sold, 4), "找回：已在券商卖出")
            if bought - sold > 0:
                qty = bought - sold
                self.book.open_ticket(sid, {**base, "tid": tid, "qty": int(qty) if option else qty, "paid": round(cost * qty * mult, 2)})
            self.j.event("sleeve_recover", dry_run=False, sleeve=sid, symbol=sym,
                         why=f"{sid}：券商有一笔账本里没有的买入（{bought:g} × ${cost:.2f}，上一轮的账本没保存上），已补记"
                             + (f"；其中 {sold:g} 已卖出" if sold else "") + (f"；剩下 {bought - sold:g} 按翻倍后回撤卖、到期日 15:30 卖" if bought - sold > 0 and option else ""))

    def manage(self) -> None:
        """Open tickets: book a filled take or sell, then decide exits."""
        tickets = [(sid, t) for sid, s in self.book.d["sleeves"].items() for t in list(s["open"])]
        if not tickets:
            return
        quotes = self.bids(sorted({t["sym"] for _, t in tickets}))
        held = {p["symbol"]: float(p["qty"]) for p in self.c.positions()}
        to_exit = []
        for sid, t in tickets:
            bid = quotes.get(t["sym"], (0.0, 0.0))[0]
            if t.get("sell_cid"):
                o = self.order(t["sell_cid"])
                if o.get("status") == "filled":
                    self.closed(sid, t, float(o.get("filled_avg_price") or bid), t.get("sell_why", "卖出"))
                elif o.get("status") in ("canceled", "expired", "rejected", None) and t["sym"] in held:
                    t.pop("sell_cid")  # the sell did not go through: try again below
                    to_exit.append((sid, t, t.get("sell_why", "重新卖出")))
                elif t["sym"] not in held and o.get("status") in (None, "expired", "canceled", "rejected", "done_for_day"):
                    part = float(o.get("filled_qty") or 0) * float(o.get("filled_avg_price") or 0)  # a $0.01 sell may fill in part
                    self.closed(sid, t, round(part / t["qty"], 4), "券商已无持仓（到期作废）")
                continue
            if t.get("take_cid"):
                o = self.order(t["take_cid"])
                if o.get("status") == "filled":
                    self.closed(sid, t, float(o.get("filled_avg_price") or t["take"]), f"{t['take_x']:g} 倍止盈（券商挂单成交）")
                    continue
                if o.get("status") in ("expired", "canceled", "done_for_day"):
                    self.place_take(sid, t)  # option orders are day orders: a multi-day take is placed again each day
            elif t.get("take") and not t.get("sell_cid"):
                self.place_take(sid, t)
            t["best_bid"] = max(t.get("best_bid", 0.0), bid)
            why = self.exit_reason(sid, t, bid)
            if why:
                to_exit.append((sid, t, why))
        for sid, t, why in to_exit:
            self.sell(sid, t, why, quotes.get(t["sym"], (0.0, 0.0))[0])

    def exit_reason(self, sid: str, t: dict, bid: float) -> str | None:
        day = self.today.isoformat()
        if t.get("exit_day") and (day > t["exit_day"] or (day == t["exit_day"] and self.hhmm >= t["exit_at"])):
            return f"{t['exit_day']} {t['exit_at']} 到时卖出"
        if t.get("expiry") == day and self.hhmm >= (t.get("expiry_out") or "15:30"):
            return f"到期日 {t.get('expiry_out') or '15:30'} 卖出，不留到收盘"
        if t.get("trail") and t["best_bid"] >= 2 * t["cost"] and bid <= 0.6 * t["best_bid"]:
            return f"翻倍后从最高 ${t['best_bid']:.2f} 回撤 40%"
        rule = self.rules.get(t["kind"])
        hook = getattr(rule, "should_exit", None) if rule else None
        if hook:
            return hook(self, sid, t)
        return None

    def sell(self, sid: str, t: dict, why: str, bid: float | None = None) -> None:
        if t.get("take_cid"):
            o = self.cancel(t["take_cid"])
            if o.get("status") == "filled":
                self.closed(sid, t, float(o.get("filled_avg_price") or t["take"]), f"{t['take_x']:g} 倍止盈（券商挂单成交）")
                return
            t.pop("take_cid")
            for _ in range(5):  # the cancel is asynchronous: the contracts must be free before selling them
                if not any(o.get("symbol") == t["sym"] and o.get("side") == "sell" and o.get("client_order_id", "").startswith(f"sl-{sid}-{t['tid']}")
                           for o in self.c.open_orders()):
                    break
                self.sleep(1)
        t["sell_cid"] = cid(sid, t["tid"], "x" + new_tid()[:4])
        t["sell_why"] = why
        qty = t["qty"]
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)   # "8", not "8.0": a fund that is not fractionable takes whole shares only
        if t["asset"] == "option" and bid is not None and bid <= 0:
            # no bid: the broker rejects a market sell ("no available quote ... please reenter with a limit"), so ask $0.01
            ok = self.submit(symbol=t["sym"], qty=str(qty), side="sell", type="limit", limit_price="0.01", time_in_force="day",
                             client_order_id=t["sell_cid"])
            why += "（没有买价，挂 $0.01 限价卖）"
        else:
            ok = self.submit(symbol=t["sym"], qty=str(qty), side="sell", type="market", time_in_force="day",
                             client_order_id=t["sell_cid"])
        self.j.event("sleeve_exit", dry_run=False, sleeve=sid, symbol=t["sym"], bid=bid,  # bid: for hero.research_fills
                     why=f"{sid}：{t['sym']} {why}，卖 {qty}")
        if ok:
            for _ in range(5):
                o = self.order(t["sell_cid"])
                if o.get("status") == "filled":
                    self.closed(sid, t, float(o.get("filled_avg_price") or 0), why)
                    return
                self.sleep(2)

    def closed(self, sid: str, t: dict, price: float, how: str) -> None:
        done = self.book.close_ticket(sid, t, price, how, f"{self.today.isoformat()} {self.hhmm}")
        s = self.book.s(sid)
        self.j.event("sleeve_result", dry_run=False, sleeve=sid, symbol=t["sym"], ret=done["ret"],
                     why=f"{sid}：{t['sym']} {how}，花 ${t['paid']:,.2f}、收回 ${done['proceeds']:,.2f}（{done['ret']:+.0%}），"
                         f"账本 ${s['cash']:,.2f}")

    # ---- plan only (a rehearsal on live quotes; nothing is sent) ----
    # Owner, 10-21: a stopped rule must still show what it would have made. So each plan pick with a size is booked
    # in the plan book (journal-s500/sleeves_plan.json) as bought at the ask, and every cycle follows it with the
    # live exits on live quotes: the take once the bid reaches it (checked each cycle, so a brief spike that a resting
    # order would catch can be missed), the trail and the time exits, sold at the bid. A plan ledger's cash moves
    # with its results and starts a new round like a real one.
    def plan(self) -> None:
        self.plan_manage()
        for spec in self.specs:
            msg = self.book.round_check(spec["id"], {**self.p, **{k: spec[k] for k in ("zero_line", "target") if k in spec}})
            if msg:
                self.j.event("sleeve_round", dry_run=True, sleeve=spec["id"], why=f"{msg}（演算）")
        for spec in self.specs:
            rule = self.rules.get(spec["kind"])
            s = self.book.s(spec["id"])
            if not rule:
                continue
            try:
                got = rule.entries(self, spec, s)
            except Exception as e:
                self.j.event("alert_error", source=f"sleeve_plan:{spec['id']}", error=str(e)[:200])
                continue
            if not got:
                continue
            slot, picks = got
            s["slots"][slot] = self.now.isoformat(timespec="minutes")
            if not picks:
                self.j.event("sleeve_plan", dry_run=True, sleeve=spec["id"], why=f"{spec['id']}（演算）：{slot} 没有要买的")
                continue
            each = spec.get("fraction", self.p["fraction"]) * s["cash"] / len(picks)
            for pk in picks:
                if pk["asset"] == "stock":
                    notional = math.floor(each * 100) / 100
                    ask = float(pk.get("ask") or 0)
                    if pk.get("whole"):
                        qty, allin = units(notional, s["cash"], ask)
                    else:
                        qty, allin = (round(notional / ask, 6) if ask > 0 else 0), False
                    size = f"一半 ${each:,.2f} 买不起一股，押上账本买 1 股" if allin else f"买 ${each:,.2f}"
                else:
                    limit = tick(pk["ask"] + self.p["buy_pad"])
                    qty, allin = units(each, s["cash"], limit * 100)
                    if spec.get("qty"):   # a sample ledger: a fixed number of contracts a find (owner, 10-21)
                        qty, allin = (int(spec["qty"]) if limit * 100 * int(spec["qty"]) <= s["cash"] else 0), False
                    size = ((f"{qty} 张 × ${limit:.2f}" + (f"（一半 ${each:,.2f} 买不起一张，押上账本）" if allin else "")) if qty
                            else f"一张 ${limit * 100:,.2f} 超过账本剩下的 ${s['cash']:,.2f}，买不起")
                self.j.event("sleeve_plan", dry_run=True, sleeve=spec["id"], symbol=pk["sym"],
                             why=f"{spec['id']}（演算，不下单）：{pk['why']}；{size}")
                if qty and qty > 0:
                    self.plan_open(spec, pk, qty, slot)

    def plan_open(self, spec: dict, pk: dict, qty, slot: str) -> None:
        """Book a plan pick as filled at the ask (the live limit is the ask + buy_pad; our fills land at or under it)."""
        option = pk["asset"] == "option"
        mult, price = (100 if option else 1), float(pk["ask"])
        t = {"tid": entry_tid(slot), "kind": spec["kind"], "sym": pk["sym"], "asset": pk["asset"], "und": pk.get("und") or pk["sym"],
             "qty": qty, "cost": price, "paid": round(price * qty * mult, 2), "opened": f"{self.today.isoformat()} {self.hhmm}",
             "why": pk["why"], "best_bid": 0.0, "sim": True}
        if option:
            t.update({"expiry": pk.get("expiry"), "exit_day": pk.get("exit_day"), "exit_at": pk.get("exit_at", "15:30"),
                      "trail": pk.get("trail", False), "expiry_out": pk.get("expiry_out")})
        else:
            t.update({"exit_at": pk.get("exit_at") or "15:50", **({"exit_day": pk["exit_day"]} if pk.get("exit_day") else {})})
        runner = None
        if option and pk.get("take") and pk.get("half") and qty >= 2:   # the same split as a live fill
            runner = {**t, "tid": t["tid"] + "r", "qty": qty - qty // 2, "trail": True}
            runner["paid"] = round(price * runner["qty"] * 100, 2)
            t["qty"] = qty // 2
            t["paid"] = round(t["paid"] - runner["paid"], 2)
        if option and pk.get("take"):
            t["take_x"] = pk["take"]
            t["take"] = tick(price * pk["take"])
        self.book.open_ticket(spec["id"], t)
        if runner:
            self.book.open_ticket(spec["id"], runner)
        self.j.event("sleeve_plan_fill", dry_run=True, sleeve=spec["id"], symbol=pk["sym"], qty=qty, price=price,
                     why=f"{spec['id']}（演算）：按卖价 ${price:.2f} 记作买进 {qty:g} {'张' if option else '股'}，"
                         f"花 ${price * qty * mult:,.2f}"
                         + (f"；{t['qty']} 张 {pk['take']:g} 倍止盈 ${t['take']:.2f}" if t.get("take") else "")
                         + (f"；另 {runner['qty']} 张翻倍后回撤 40% 卖" if runner else ""))

    def plan_manage(self) -> None:
        """The plan book's open tickets: the take when the bid reaches it, else the live exit rules, sold at the bid."""
        tickets = [(sid, t) for sid, s in self.book.d["sleeves"].items() for t in list(s["open"])]
        if not tickets:
            return
        quotes = self.bids(sorted({t["sym"] for _, t in tickets}))
        for sid, t in tickets:
            if t["sym"] not in quotes:   # no quote this cycle: wait (a missing quote is not a $0 sale), unless it has expired
                if t.get("expiry") and t["expiry"] < self.today.isoformat():
                    self.plan_closed(sid, t, 0.0, "到期作废")
                continue
            bid = quotes[t["sym"]][0]
            if t.get("take") and bid >= t["take"]:
                self.plan_closed(sid, t, t["take"], f"{t['take_x']:g} 倍止盈（买价到了止盈价）")
                continue
            t["best_bid"] = max(t.get("best_bid", 0.0), bid)
            why = self.exit_reason(sid, t, bid)
            if not why and t.get("expiry") == self.today.isoformat() and self.hhmm >= LAST_CYCLE:
                why = "到期日最后一轮（15:50 以后没有交易轮）卖出"   # one shot's 15:55 never comes: no cycle runs after 15:50
            if why:
                self.plan_closed(sid, t, bid, why)

    def plan_closed(self, sid: str, t: dict, price: float, how: str) -> None:
        done = self.book.close_ticket(sid, t, round(price, 4), how, f"{self.today.isoformat()} {self.hhmm}")
        s = self.book.s(sid)
        self.j.event("sleeve_plan_result", dry_run=True, sleeve=sid, symbol=t["sym"], ret=done["ret"],
                     why=f"{sid}（演算）：{t['sym']} {how}，花 ${t['paid']:,.2f}、收回 ${done['proceeds']:,.2f}（{done['ret']:+.0%}），"
                         f"演算账本 ${s['cash']:,.2f}")

    # ---- entries ----
    def losing_stop(self, spec: dict, s: dict) -> bool:
        """Owner, 10-21 (the sniper: 3 picks, 3 losses): `stop_if_losing` {"after": N} -- once N entries have finished,
        a sleeve that is down on them in total opens no new tickets; its plan twin keeps recording what it would do.
        Once stopped it stays stopped (no new entries, so the total cannot recover) until the config changes."""
        rule = spec.get("stop_if_losing")
        if not rule:
            return False
        after = int(rule.get("after", 10))
        n, net = finished_entries(s)
        if n < after or net >= 0:
            return False
        day = self.today.isoformat()
        if s.get("stopped_day") != day:   # once a day in the journal
            s["stopped_day"] = day
            self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"],
                         why=f"{spec['id']}：已做完 {n} 次，合计亏 ${-net:,.2f}；按用户 10-21 定的线（满 {after} 次还是亏就停），"
                             "改成只记录、不下单" + (f"（{rule['twin']} 照常演算）" if rule.get("twin") else ""))
        return True

    def enter(self) -> None:
        intents = []
        for spec in self.specs:
            rule = self.rules.get(spec["kind"])
            s = self.book.s(spec["id"])
            if not rule or spec.get("stop_entries") or self.losing_stop(spec, s):  # no new tickets; open ones still run to their exits
                continue
            try:  # one rule's bad data must not stop the other sleeves
                got = rule.entries(self, spec, s)
            except Exception as e:
                self.j.event("alert_error", source=f"sleeve:{spec['id']}", error=str(e)[:200])
                continue
            if not got:
                continue
            slot, picks = got
            s["slots"][slot] = self.now.isoformat(timespec="minutes")
            if not picks:
                continue
            if self.bought_at_broker(spec["id"], slot):
                self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=picks[0]["sym"],
                             why=f"{spec['id']}：券商已有 {slot} 这个时段的买单（上一轮的账本没保存上），不重复买")
                continue
            fraction = spec.get("fraction", self.p["fraction"])
            each = fraction * s["cash"] / len(picks)
            for pk in picks:
                r = self.report_inside(pk.get("und"), pk.get("exit_day") or pk.get("expiry")) if pk["asset"] == "option" else None
                if r:
                    self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=pk["sym"],
                                 why=f"{spec['id']}：{pk.get('und')} {r['date']}"
                                     f"{'盘后' if 'post' in r.get('time', '') else '盘前' if 'pre' in r.get('time', '') else ''}"
                                     f"发财报，这张期权会拿过财报（隐含波动财报后掉得太快），财报前不拿期权，不买")
                    continue
                intents.append((spec, pk, each, slot))
        if not intents:
            return
        try:
            cash = float(self.c.account().get("cash") or 0)
        except Exception:
            cash = 0.0
        room = cash - self.p["cash_buffer"]
        opts, stocks = [], []
        left = {}   # what each ledger still has this cycle: one pick may go all in when its half cannot buy one unit
        for spec, pk, each, slot in intents:
            sid = spec["id"]
            left.setdefault(sid, self.book.s(sid)["cash"])
            if pk["asset"] == "stock":
                notional = math.floor(min(each, room) * 100) / 100
                ask = float(pk.get("ask") or 0)
                if pk.get("whole") and ask and int(notional // ask) < 1 and ask <= min(left[sid], room):
                    notional = ask   # one whole share with what is left of the ledger
                    pk = {**pk, "why": f"{pk['why']}；一半 ${each:,.2f} 买不起一股，押上账本买 1 股"}
                if notional >= 1:
                    stocks.append((spec, pk, notional, slot))
                    room -= notional
                    left[sid] -= notional
                continue
            limit = tick(pk["ask"] + self.p["buy_pad"])
            qty, allin = units(each, left[sid], limit * 100)
            if qty < 1:
                self.j.event("sleeve_skip", dry_run=False, sleeve=sid, symbol=pk["sym"],
                             why=f"{sid}：{pk['sym']} 一张 ${limit * 100:,.2f}，超过这笔可用的 ${each:,.2f}"
                                 f"（账本剩下 ${left[sid]:,.2f} 也不够），不买")
                continue
            if qty * limit * 100 > room:
                self.j.event("sleeve_skip", dry_run=False, sleeve=sid, symbol=pk["sym"], why=f"{sid}：账户现金不够，不买")
                continue
            if allin:
                pk = {**pk, "why": f"{pk['why']}；一半 ${each:,.2f} 买不起一张，押上账本买 1 张"}
            room -= qty * limit * 100
            opts.append({"spec": spec, "pk": pk, "limit": limit, "qty": qty, "each": each, "cap": left[sid],
                         "tid": entry_tid(slot), "first_ask": pk["ask"]})
            left[sid] -= qty * limit * 100
        self.buy_options(opts)
        self.buy_stocks(stocks)

    def buy_options(self, orders: list[dict]) -> None:
        for attempt in (1, 2):
            live = []
            for o in orders:
                o["cid"] = cid(o["spec"]["id"], o["tid"], f"b{attempt}")
                self.j.event("sleeve_buy", dry_run=False, sleeve=o["spec"]["id"], symbol=o["pk"]["sym"], attempt=attempt,
                             ask=o["pk"].get("ask") if attempt == 1 else o.get("ask"), limit=o["limit"],  # for hero.research_fills
                             why=f"{o['spec']['id']}：{o['pk']['why']}；{o['qty']} 张 × ${o['limit']:.2f}（第 {attempt} 次挂单）")
                if self.submit(symbol=o["pk"]["sym"], qty=str(o["qty"]), side="buy", type="limit",
                               limit_price=f"{o['limit']:.2f}", time_in_force="day", client_order_id=o["cid"]):
                    live.append(o)
            if not live:
                return
            deadline = time.monotonic() + self.p["fill_wait_s"]
            pending = list(live)
            while pending and time.monotonic() < deadline:
                self.sleep(5)
                pending = [o for o in pending if self.order(o["cid"]).get("status") != "filled"]
            retry = []
            for o in live:
                st = self.cancel(o["cid"]) if o in pending else self.order(o["cid"])  # a fill can land during the cancel
                filled = int(float(st.get("filled_qty") or 0))
                if filled:
                    self.filled(o, filled, float(st.get("filled_avg_price") or o["limit"]))
                elif attempt == 1:
                    retry.append(o)
            if attempt == 2 or not retry:
                return
            quotes = self.bids([o["pk"]["sym"] for o in retry])
            orders = []
            for o in retry:
                ask = quotes.get(o["pk"]["sym"], (0.0, 0.0))[1]
                limit = tick(ask + self.p["buy_pad"]) if ask else 0.0
                qty = units(o["each"], o.get("cap", 0.0), limit * 100)[0] if limit else 0
                if not ask or limit > tick(o["first_ask"] * self.p["chase_cap"]) or qty < 1:
                    self.j.event("sleeve_skip", dry_run=False, sleeve=o["spec"]["id"], symbol=o["pk"]["sym"],
                                 why=f"{o['spec']['id']}：{o['pk']['sym']} 没成交、已撤单；新卖价 "
                                     + (f"${ask:.2f}，超过追价上限" if ask else "取不到") + "，这次不做")
                    continue
                orders.append({**o, "limit": limit, "qty": qty, "ask": ask})

    def place_take(self, sid: str, t: dict) -> None:
        """The take as a resting sell limit for this ticket's own quantity (a day order, as options require)."""
        t["take_cid"] = cid(sid, t["tid"], "t" + new_tid()[:4])
        if not self.submit(symbol=t["sym"], qty=str(t["qty"]), side="sell", type="limit", limit_price=f"{t['take']:.2f}",
                           time_in_force="day", client_order_id=t["take_cid"]):
            t.pop("take_cid")

    def filled(self, o: dict, qty: int, price: float) -> None:
        spec, pk = o["spec"], o["pk"]
        t = {"tid": o["tid"], "kind": spec["kind"], "sym": pk["sym"], "asset": "option", "und": pk.get("und"),
             "qty": qty, "cost": price, "paid": round(price * qty * 100, 2), "opened": f"{self.today.isoformat()} {self.hhmm}",
             "why": pk["why"], "expiry": pk.get("expiry"), "exit_day": pk.get("exit_day"), "exit_at": pk.get("exit_at", "15:30"),
             "trail": pk.get("trail", False), "best_bid": 0.0, "expiry_out": pk.get("expiry_out")}
        runner = None
        if pk.get("take") and pk.get("half") and qty >= 2:
            # half rides the take, the other half is a runner: out after 2x on a 40% giveback, else at the time exit
            runner = {**t, "tid": t["tid"] + "r", "qty": qty - qty // 2, "trail": True}  # its orders share the buy's prefix
            runner["paid"] = round(price * runner["qty"] * 100, 2)
            t["qty"] = qty // 2
            t["paid"] = round(t["paid"] - runner["paid"], 2)
        if pk.get("take"):
            t["take_x"] = pk["take"]
            t["take"] = tick(price * pk["take"])
            self.place_take(spec["id"], t)
        self.book.open_ticket(spec["id"], t)
        if runner:
            self.book.open_ticket(spec["id"], runner)
        self.j.event("sleeve_fill", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], qty=qty, price=price,
                     why=f"{spec['id']}：{pk['sym']} 成交 {qty} 张 × ${price:.2f}，花 ${price * qty * 100:,.2f}"
                         + (f"；{t['qty']} 张 {pk['take']:g} 倍止盈 ${t['take']:.2f} 挂在券商" if t.get("take_cid") else "")
                         + (f"；另 {runner['qty']} 张翻倍后回撤 40% 卖，否则 {t['exit_at']} 卖" if runner else ""))

    def buy_stocks(self, orders: list[tuple]) -> None:
        for spec, pk, notional, slot in orders:
            tid = entry_tid(slot)
            c = cid(spec["id"], tid, "b")
            if pk.get("whole"):   # not fractionable at the broker (some 2x funds): whole shares only
                qty = int(notional // (pk.get("ask") or 0)) if pk.get("ask") else 0
                if qty < 1:
                    self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=pk["sym"],
                                 why=f"{spec['id']}：{pk['sym']} 不能买零股，一股约 ${pk.get('ask') or 0:,.2f}，超过这笔可用的 ${notional:,.2f}，不买")
                    continue
                size, size_txt = {"qty": str(qty)}, f"买 {qty} 股（约 ${qty * pk['ask']:,.2f}）"
            else:
                size, size_txt = {"notional": f"{notional:.2f}"}, f"买 ${notional:,.2f}"
            self.j.event("sleeve_buy", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], why=f"{spec['id']}：{pk['why']}；{size_txt}")
            if not self.submit(symbol=pk["sym"], **size, side="buy", type="market", time_in_force="day", client_order_id=c):
                continue
            st = {}
            for _ in range(10):
                st = self.order(c)
                if st.get("status") == "filled":
                    break
                self.sleep(2)
            qty = float(st.get("filled_qty") or 0)
            if not qty:
                self.cancel(c)
                continue
            price = float(st.get("filled_avg_price") or 0)
            t = {"tid": tid, "kind": spec["kind"], "sym": pk["sym"], "asset": "stock", "und": pk.get("und") or pk["sym"], "qty": qty,
                 "cost": price, "paid": round(price * qty, 2), "opened": f"{self.today.isoformat()} {self.hhmm}", "why": pk["why"],
                 "exit_at": pk.get("exit_at") or "15:50", "best_bid": 0.0,
                 **({"exit_day": pk["exit_day"]} if pk.get("exit_day") else {})}   # earnings shares: sold at the next 09:35
            self.book.open_ticket(spec["id"], t)
            self.j.event("sleeve_fill", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], qty=qty, price=price,
                         why=f"{spec['id']}：{pk['sym']} 买入 {qty:g} 股 × ${price:.2f}")
