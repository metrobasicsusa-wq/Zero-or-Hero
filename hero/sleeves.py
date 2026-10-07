"""Many small accounts inside one paper account: each direction gets its own $500 and $1,000 ledger.

The plan (docs/sleeves-plan.md): every direction runs as if it were a real small account. A sleeve has
its own cash; it bets a fraction (half) of that cash per entry, never the account's; whole contracts only,
so a small sleeve sometimes cannot afford a trade. Below 10% of its start the round is over (a zero);
at $10,000 it is a hero; either way it starts again at its start from the shared pool, and the round
count is kept. Every broker order carries a client_order_id naming the sleeve and the ticket, and
every fill is booked from the broker's own fill price, so the ledgers add up to the real account.

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
import time
import uuid
from datetime import date, datetime
from pathlib import Path

DEFAULTS = {"enabled": False, "fraction": 0.5, "zero_line": 0.1, "target": 10_000.0, "buy_pad": 0.01,
            "fill_wait_s": 60, "chase_cap": 1.3, "cash_buffer": 1_000.0}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in cfg.get("sleeves", {}).items() if k != "list"}}


def tick(x: float) -> float:
    step = 0.05 if x >= 3 else 0.01
    return round(round(x / step) * step, 2)


def multiplier(t: dict) -> int:
    return 100 if t["asset"] == "option" else 1


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
            s["closed"] = s["closed"][-200:]
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


class Runner:
    """One cycle of every sleeve against the broker. `rules` maps a kind to a hero.sleeve_rules function."""

    def __init__(self, client, journal, cfg: dict, specs: list[dict], book: Book, rules: dict, now: datetime,
                 today: date, sleep=time.sleep):
        self.c, self.j, self.cfg, self.specs, self.book, self.rules = client, journal, cfg, specs, book, rules
        self.p = settings(cfg)
        self.now, self.today, self.sleep = now, today, sleep
        self.hhmm = now.strftime("%H:%M")

    # ---- broker helpers ----
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
        self.manage()
        for spec in self.specs:
            msg = self.book.round_check(spec["id"], self.p)
            if msg:
                self.j.event("sleeve_round", dry_run=False, sleeve=spec["id"], why=msg)
        self.enter()

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
                elif t["sym"] not in held and o.get("status") is None:
                    self.closed(sid, t, 0.0, "券商已无持仓（到期作废）")
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
            self.sell(sid, t, why)

    def exit_reason(self, sid: str, t: dict, bid: float) -> str | None:
        day = self.today.isoformat()
        if t.get("exit_day") and (day > t["exit_day"] or (day == t["exit_day"] and self.hhmm >= t["exit_at"])):
            return f"{t['exit_day']} {t['exit_at']} 到时卖出"
        if t.get("expiry") == day and self.hhmm >= "15:30":
            return "到期日 15:30 卖出，不留到收盘"
        if t.get("trail") and t["best_bid"] >= 2 * t["cost"] and bid <= 0.6 * t["best_bid"]:
            return f"翻倍后从最高 ${t['best_bid']:.2f} 回撤 40%"
        rule = self.rules.get(t["kind"])
        hook = getattr(rule, "should_exit", None) if rule else None
        if hook:
            return hook(self, sid, t)
        return None

    def sell(self, sid: str, t: dict, why: str) -> None:
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
        ok = self.submit(symbol=t["sym"], qty=str(qty), side="sell", type="market", time_in_force="day",
                         client_order_id=t["sell_cid"])
        self.j.event("sleeve_exit", dry_run=False, sleeve=sid, symbol=t["sym"], why=f"{sid}：{t['sym']} {why}，卖 {qty}")
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

    # ---- plan only (a rehearsal on live quotes: what each sleeve would buy now; nothing is sent) ----
    def plan(self) -> None:
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
                    size = f"买 ${each:,.2f}"
                else:
                    limit = tick(pk["ask"] + self.p["buy_pad"])
                    qty = int(each // (limit * 100))
                    size = f"{qty} 张 × ${limit:.2f}" if qty else f"一张 ${limit * 100:,.2f} 超过 ${each:,.2f}，买不起"
                self.j.event("sleeve_plan", dry_run=True, sleeve=spec["id"], symbol=pk["sym"],
                             why=f"{spec['id']}（演算，不下单）：{pk['why']}；{size}")

    # ---- entries ----
    def enter(self) -> None:
        intents = []
        for spec in self.specs:
            rule = self.rules.get(spec["kind"])
            s = self.book.s(spec["id"])
            if not rule:
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
            fraction = spec.get("fraction", self.p["fraction"])
            each = fraction * s["cash"] / len(picks)
            for pk in picks:
                intents.append((spec, pk, each))
        if not intents:
            return
        try:
            cash = float(self.c.account().get("cash") or 0)
        except Exception:
            cash = 0.0
        room = cash - self.p["cash_buffer"]
        opts, stocks = [], []
        for spec, pk, each in intents:
            if pk["asset"] == "stock":
                notional = math.floor(min(each, room) * 100) / 100
                if notional >= 1:
                    stocks.append((spec, pk, notional))
                    room -= notional
                continue
            limit = tick(pk["ask"] + self.p["buy_pad"])
            qty = int(each // (limit * 100))
            if qty < 1:
                self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=pk["sym"],
                             why=f"{spec['id']}：{pk['sym']} 一张 ${limit * 100:,.2f}，超过这笔可用的 ${each:,.2f}，不买")
                continue
            if qty * limit * 100 > room:
                self.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], why=f"{spec['id']}：账户现金不够，不买")
                continue
            room -= qty * limit * 100
            opts.append({"spec": spec, "pk": pk, "limit": limit, "qty": qty, "each": each, "tid": new_tid(), "first_ask": pk["ask"]})
        self.buy_options(opts)
        self.buy_stocks(stocks)

    def buy_options(self, orders: list[dict]) -> None:
        for attempt in (1, 2):
            live = []
            for o in orders:
                o["cid"] = cid(o["spec"]["id"], o["tid"], f"b{attempt}")
                self.j.event("sleeve_buy", dry_run=False, sleeve=o["spec"]["id"], symbol=o["pk"]["sym"], attempt=attempt,
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
                qty = int(o["each"] // (limit * 100)) if limit else 0
                if not ask or limit > tick(o["first_ask"] * self.p["chase_cap"]) or qty < 1:
                    self.j.event("sleeve_skip", dry_run=False, sleeve=o["spec"]["id"], symbol=o["pk"]["sym"],
                                 why=f"{o['spec']['id']}：{o['pk']['sym']} 没成交、已撤单；新卖价 "
                                     + (f"${ask:.2f}，超过追价上限" if ask else "取不到") + "，这次不做")
                    continue
                orders.append({**o, "limit": limit, "qty": qty})

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
             "trail": pk.get("trail", False), "best_bid": 0.0}
        if pk.get("take"):
            t["take_x"] = pk["take"]
            t["take"] = tick(price * pk["take"])
            self.place_take(spec["id"], t)
        self.book.open_ticket(spec["id"], t)
        self.j.event("sleeve_fill", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], qty=qty, price=price,
                     why=f"{spec['id']}：{pk['sym']} 成交 {qty} 张 × ${price:.2f}，花 ${t['paid']:,.2f}"
                         + (f"；{pk['take']:g} 倍止盈 ${t['take']:.2f} 挂在券商" if t.get("take_cid") else ""))

    def buy_stocks(self, orders: list[tuple]) -> None:
        for spec, pk, notional in orders:
            tid = new_tid()
            c = cid(spec["id"], tid, "b")
            self.j.event("sleeve_buy", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], why=f"{spec['id']}：{pk['why']}；买 ${notional:,.2f}")
            if not self.submit(symbol=pk["sym"], notional=f"{notional:.2f}", side="buy", type="market", time_in_force="day",
                               client_order_id=c):
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
            t = {"tid": tid, "kind": spec["kind"], "sym": pk["sym"], "asset": "stock", "und": pk["sym"], "qty": qty,
                 "cost": price, "paid": round(price * qty, 2), "opened": f"{self.today.isoformat()} {self.hhmm}", "why": pk["why"],
                 "exit_at": "15:50", "best_bid": 0.0}
            self.book.open_ticket(spec["id"], t)
            self.j.event("sleeve_fill", dry_run=False, sleeve=spec["id"], symbol=pk["sym"], qty=qty, price=price,
                         why=f"{spec['id']}：{pk['sym']} 买入 {qty:g} 股 × ${price:.2f}")
