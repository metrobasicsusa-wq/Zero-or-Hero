"""One trading cycle: exits, stock rebalance, option entries. Safe to run repeatedly."""

from __future__ import annotations

import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hero import alerts
from hero import earnings as earn
from hero import lottery as lot
from hero import zero_dte as zd
from hero import macro
from hero import net as netlib
from hero import stops as stoplib
from hero.alpaca import AlpacaError
from hero.journal import Journal, execution_key
from hero.strategies import momentum
from hero.strategies import options as opt

HISTORY_DAYS = 420


def closes_from_bars(bars: dict[str, list[dict]]) -> dict[str, list[float]]:
    return {s: [float(b["c"]) for b in bs] for s, bs in bars.items() if bs}


EXIT_PREFIX = "hero-exit-"


def option_underlying(symbol: str) -> str:
    return symbol[:-15]


def round_option_price(x: float) -> float:
    tick = 0.05 if x >= 3 else 0.01
    return round(round(x / tick) * tick, 2)


class Engine:
    def __init__(self, client, cfg: dict, journal: Journal, dry_run: bool = False, earnings: dict | None = None):
        self.c, self.cfg, self.j, self.dry = client, cfg, journal, dry_run
        self.earnings = earnings  # calendar from hero.earnings; None = unavailable

    def _order(self, reason: str, evidence: dict | None = None, why: str = "", cid_prefix: str = "",
               **order) -> None:
        # The decision and its evidence (the quote it was based on) are journaled before the
        # broker call, so a timeout or crash cannot lose them. Evidence is never sent to the broker.
        order.setdefault("client_order_id", cid_prefix + uuid.uuid4().hex)
        intent = execution_key(order["client_order_id"])
        logged = {k: v for k, v in order.items() if k != "client_order_id"}
        self.j.event("order", reason=reason, why=why, dry_run=self.dry, evidence=evidence or {}, intent_key=intent,
                     **logged)
        if not self.dry:
            self._submit(intent, lambda: self.c.submit_order(**order))

    def _close(self, symbol: str, reason: str, evidence: dict | None = None, why: str = "") -> None:
        intent = execution_key(uuid.uuid4().hex)
        self.j.event("close", symbol=symbol, reason=reason, why=why, dry_run=self.dry, evidence=evidence or {},
                     intent_key=intent)
        if not self.dry:
            self._submit(intent, lambda: self.c.close_position(symbol))

    def _submit(self, intent: str, call) -> None:
        """Run a broker call and journal its outcome; the order_key (hashed broker order id)
        links the intent to its fills in fills.jsonl."""
        try:
            placed = call() or {}
        except AlpacaError as e:
            # A broker rejection (e.g. pattern-day-trader limit, insufficient buying power) is
            # journaled and the cycle carries on with its other decisions.
            self.j.event("order_error", intent_key=intent, error=str(e)[:300])
            return
        except Exception as e:
            self.j.event("order_error", intent_key=intent, error=str(e)[:300])
            raise
        self.j.event("order_ack", intent_key=intent, status=placed.get("status"),
                     order_key=execution_key(placed["id"]) if placed.get("id") else None)

    def run(self, today: date | None = None, force: bool = False, now: datetime | None = None) -> dict:
        today = today or date.today()
        self.now = now or datetime.now(ZoneInfo("America/New_York"))
        self.today = today
        if not force and not self.c.clock().get("is_open"):
            return {"status": "market_closed"}
        if not self.dry and not self._launch_allowed(today):
            self.dry = True  # rehearsal: decisions are journaled, nothing is sent

        acct = self.c.account()
        equity, last = float(acct["equity"]), float(acct["last_equity"])
        state = self.j.state()
        attempt = self.cfg.get("attempt")
        if attempt and self._attempt_over(attempt, equity, state, today):
            return {"status": "attempt_ended", "equity": equity}
        day_pl = equity / last - 1 if last else 0.0
        halted = day_pl <= self.cfg["risk"]["daily_loss_halt"]
        if halted:
            self.j.event("halt", day_pl=round(day_pl, 4))

        positions = self.c.positions()
        open_orders = self.c.open_orders()
        # Our resting protective stops are not "pending trades": they must not block the rebalance.
        self.stops: dict[str, list[dict]] = {}
        for o in open_orders:
            if stoplib.is_protective_stop(o):
                self.stops.setdefault(o["symbol"], []).append(o)
        busy = {o["symbol"] for o in open_orders if not stoplib.is_protective_stop(o)}
        self.touched: set[str] = set()
        # Only manage what this strategy owns: stocks in its universe and options on them. Anything
        # else in the account (e.g. another experiment's positions) is left alone.
        # An experiment with its own account (owns_account) manages every position in it, so a name
        # that left a dynamic universe is still sold; otherwise only its universe is ours.
        held_stocks = {p["symbol"] for p in positions if p.get("asset_class") == "us_equity"}
        held_unds = {option_underlying(p["symbol"]) for p in positions if p.get("asset_class") == "us_option"}
        mine = set(self.cfg["universe"]) | ((held_stocks | held_unds) if self.cfg.get("owns_account") else set())
        stocks = {p["symbol"]: p for p in positions if p.get("asset_class") == "us_equity" and p["symbol"] in mine}
        options = {p["symbol"]: p for p in positions
                   if p.get("asset_class") == "us_option" and option_underlying(p["symbol"]) in mine}

        universe = sorted(set(self.cfg["universe"]) | (set(stocks) | {option_underlying(o) for o in options}))
        start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        macro_syms = [s for s in macro.SYMBOLS.values() if s not in universe]
        bars = self.c.daily_bars(universe + macro_syms, start)
        closes = closes_from_bars({s: b for s, b in bars.items() if s in universe})
        # Only the configured universe competes for a place; held names outside it can only be sold.
        ranked_pool = set(self.cfg["universe"]) | {self.cfg["regime_symbol"]}
        self.rank_closes = {s: xs for s, xs in closes.items() if s in ranked_pool}
        self.macro_closes = closes_from_bars({s: bars.get(s, []) for s in macro.SYMBOLS.values()})
        self._macro_hourly()
        self._intraday(stocks, today)
        if self.cfg.get("net_shadow", {}).get("enabled"):
            try:  # a simulation: it must never get in the way of the real decisions below
                self._net_shadow(closes, today)
            except Exception as e:
                self.j.event("alert_error", source="net_shadow", error=str(e)[:200])
        if self.cfg.get("zdte_shadow", {}).get("enabled"):
            try:  # quotes only, never an order
                self._zdte_shadow(today)
            except Exception as e:
                self.j.event("alert_error", source="zdte_shadow", error=str(e)[:200])

        # Many small accounts in one (hero.sleeves): when on, they are the account's only trading.
        if self.cfg.get("sleeves", {}).get("enabled"):
            self._sleeves(today)
            state["last_run"] = today.isoformat()
            if not self.dry:
                self.j.save_state(state)
            bench = closes.get(self.cfg["regime_symbol"], [None])[-1]
            self.j.equity(today.isoformat(), equity, float(acct["cash"]), self.cfg["generation"], bench)
            return {"status": "ok", "equity": equity, "day_pl": day_pl, "halted": halted}

        if self.cfg.get("sleeves", {}).get("plan_only"):
            try:  # what every sleeve would buy, on live quotes; nothing is sent
                self._sleeves_plan(today)
            except Exception as e:
                self.j.event("alert_error", source="sleeves_plan", error=str(e)[:200])

        # Zero-or-hero phase 1: while it runs, the account does nothing but the daily 0DTE trade.
        zp = zd.settings(self.cfg) if self.cfg.get("zero_dte", {}).get("enabled") else None
        book = zd.Book(self.j.root / "zdte.json", float(self.cfg.get("attempt", {}).get("start_capital", equity))) \
            if zp else None
        if book and book.active:
            self._zero_dte(book, zp, equity, today)
            book.save()
        if not (book and book.active):
            # The lottery sleeve's call is managed only by the sleeve: the normal option rules (stops,
            # the earnings guard) would sell it before the very report it is meant to hold through.
            ledger = lot.Ledger(self.j.root / "lottery.json", lot.settings(self.cfg)) \
                if self.cfg.get("lottery", {}).get("enabled") else None
            if ledger and ledger.d["open"]:
                options = {s: p for s, p in options.items() if s != ledger.d["open"]["symbol"]}
            self._option_exits(options, busy, today)
            if ledger:
                self._lottery(ledger, closes, today)
            core_equity = equity - (ledger.reserve() if ledger else 0.0)
            if state.get("last_rebalance") != today.isoformat():
                self._rebalance(stocks, busy, closes, core_equity, halted)
                state["last_rebalance"] = today.isoformat()
            if self.cfg["options"]["enabled"] and not halted and not self.breaker:
                self._option_entries(options, busy, closes, core_equity, today, stocks)
            self._reconcile_stops(stocks, busy, closes)

        state["last_run"] = today.isoformat()
        # A dry run must not mark the day as rebalanced, or the real run would skip it.
        if not self.dry:
            self.j.save_state(state)
        bench = closes.get(self.cfg["regime_symbol"], [None])[-1]
        self.j.equity(today.isoformat(), equity, float(acct["cash"]), self.cfg["generation"], bench)
        return {"status": "ok", "equity": equity, "day_pl": day_pl, "halted": halted}

    def _intraday(self, stocks: dict, today: date) -> None:
        """Price circuit breaker and news alerts for this cycle. Sets self.breaker (why the market
        breaker is on, or None) and self.cautious ({symbol: why}). Both only make the bot more
        careful: no new buys or options, tighter stops; neither sells anything by itself.

        The alert bookkeeping (alerts.json) is saved even in a dry run: it only de-duplicates the
        journal and never touches state.json."""
        c = alerts.settings(self.cfg)
        self.breaker, self.cautious, self.circuit = None, {}, c
        day = today.isoformat()
        saved = self.j.alerts()
        book = saved if saved.get("date") == day else {"date": day}
        try:
            snaps = self.c.stock_snapshots(sorted(set(stocks) | {c["index"], c["fear"]}))
        except Exception as e:
            snaps = {}
            self.j.event("alert_error", source="snapshots", error=str(e)[:200])

        self.breaker = alerts.market_breaker(snaps, c)
        if self.breaker and not book.get("breaker"):
            self.j.event("circuit", dry_run=self.dry, why=f"市场熔断：{self.breaker}。今天停止买入和开期权，所有止损收紧到现价下方 "
                                                        f"{c['tight_stop_pct']:.0%}（只收紧不放松）；不主动卖出")
        book["breaker"] = book.get("breaker") or self.breaker

        for sym, move in alerts.stock_drops(snaps, set(stocks), c).items():
            why = f"盘中 {move:+.1%}（线 {c['stock_drop']:+.0%}）"
            self.cautious[sym] = why
            if sym not in book.setdefault("drops", {}):
                book["drops"][sym] = why
                self.j.event("stock_drop_alert", symbol=sym, dry_run=self.dry,
                             why=f"{sym} {why}：今天不加仓、不买它的期权，止损收紧到现价下方 {c['tight_stop_pct']:.0%}")
        for sym, why in book.get("drops", {}).items():
            self.cautious.setdefault(sym, why)

        now = datetime.now(timezone.utc)
        since = saved.get("news_checked") or (now - timedelta(hours=18)).isoformat(timespec="seconds")
        try:
            items = self.c.news(self.cfg["universe"], since)
        except Exception as e:
            items = []
            self.j.event("alert_error", source="news", error=str(e)[:200])
        for sym, hits in alerts.news_alerts(items, set(self.cfg["universe"])).items():
            for h in hits:
                if (h["id"], sym) in {tuple(x) for x in book.get("news_seen", [])}:
                    continue
                book.setdefault("news_seen", []).append([h["id"], sym])
                book.setdefault("news", {}).setdefault(sym, []).append("、".join(h["matched"]))
                self.j.event("news_alert", symbol=sym, dry_run=self.dry, matched=h["matched"], headline=h["headline"],
                             source=h["source"], url=h["url"], published=h["created_at"],
                             why=f"新闻警报（{'、'.join(h['matched'])}）：{h['headline']}。今天不加仓、不买它的期权，止损收紧；不因新闻直接卖出")
        for sym, kinds in book.get("news", {}).items():
            self.cautious.setdefault(sym, "新闻警报：" + "、".join(sorted(set(kinds))))
        book["news_checked"] = now.isoformat(timespec="seconds")
        self.j.save_alerts(book)

    def _zero_dte(self, book: "zd.Book", p: dict, equity: float, today: date) -> None:
        """One same-day-expiry trade a day; see hero.zero_dte."""
        day, now = today.isoformat(), zd.hhmm(self.now)
        sim = self.dry
        if not sim and book.d.get("mode") != "live":
            book.go_live(day)  # the first live cycle starts a fresh book; the rehearsal is kept inside it
        old = book.d.get("today")
        if old and old.get("status") == "open" and old["date"] < day:
            # An earlier day's ticket never booked: with no bid the 15:30 sell cannot fill, and the
            # contract expired. Book it now (at the bid it was sent at, $0 when there was none), or the
            # loss would be lost when today's ticket replaces it and the book would bet money it lost.
            bought = old.get("filled") or old.get("exit_sent")
            done = book.close(old.get("exit_bid", 0.0) * 100 * old["qty"] if bought else old["paid"],
                              "到期作废（15:30 卖单没有买家，按卖出时买价记）" if bought else "买单没有成交", now)
            self.j.event("zdte_result", dry_run=sim, symbol=old["symbol"], ret=done["ret"],
                         why=f"末日补记 {old['date']}：{old['symbol']} 收盘前没卖出，已到期，按 ${done['proceeds']:,.2f} 入账，"
                             f"回报 {done['ret']:+.0%}")
            if old["symbol"] in {x["symbol"] for x in self.c.positions()}:
                self.j.event("alert_error", source="zdte", error=f"{old['symbol']} 到期后仍在持仓里，请人工查看")
        t = book.trade_today(day)
        if t and t["status"] == "open":
            try:
                q = (self.c.option_snapshots([t["symbol"]]).get(t["symbol"]) or {}).get("latestQuote") or {}
            except Exception:
                q = {}
            bid = float(q.get("bp") or 0)
            if sim and bid >= t["take_price"]:
                done = book.close(t["take_price"] * 100 * t["qty"], f"止盈 {p['take']:g} 倍", now)
                self.j.event("zdte_result", dry_run=True, symbol=t["symbol"], ret=done["ret"],
                             why=f"末日止盈：{t['symbol']} 碰到 ${t['take_price']:.2f}（{p['take']:g} 倍），"
                                 f"卖得 ${done['proceeds']:,.2f}，模拟余额 ${book.d['sim_cash']:,.2f}")
            elif not sim and t.get("take_order") and not t.get("exit_sent") \
                    and t["symbol"] not in {x["symbol"] for x in self.c.positions()}:
                done = book.close(t["take_price"] * 100 * t["qty"], f"止盈 {p['take']:g} 倍（券商挂单成交）", now)
                self.j.event("zdte_result", dry_run=False, symbol=t["symbol"], ret=done["ret"],
                             why=f"末日止盈成交：{t['symbol']} @ ${t['take_price']:.2f}（{p['take']:g} 倍）")
            elif not sim and not t.get("take_order"):
                held = {x["symbol"]: x for x in self.c.positions()}
                if t["symbol"] in held:
                    t["filled"] = True
                    t["qty"] = int(float(held[t["symbol"]]["qty"]))  # a partial fill: the take covers what we hold
                    self._order("zdte_take", {"cost": t["price"]}, f"末日止盈单：{t['take_price']:.2f}（{p['take']:g} 倍）挂在券商",
                                symbol=t["symbol"], qty=held[t["symbol"]]["qty"], side="sell", type="limit",
                                limit_price=f"{t['take_price']:.2f}", time_in_force="day")
                    t["take_order"] = True
            if t["status"] == "open" and now >= p["exit_at"]:
                if sim:
                    done = book.close(bid * 100 * t["qty"], "收盘前平仓", now)
                else:
                    done = self._zero_dte_exit(book, t, bid, now)
                    if done is None:
                        return  # exit sent; booked next cycle once the broker shows us flat
                self.j.event("zdte_result", dry_run=sim, symbol=t["symbol"], ret=done["ret"],
                             why=f"末日平仓：{t['symbol']} 卖得约 ${done['proceeds']:,.2f}，回报 {done['ret']:+.0%}"
                                 + (f"，模拟余额 ${book.d['sim_cash']:,.2f}" if sim else ""))
        elif not t and p["entry"] <= now < p["exit_at"]:
            und = p["underlying"]
            try:
                snap = self.c.stock_snapshots([und]).get(und) or {}
                day_open = float((snap.get("dailyBar") or {}).get("o") or 0)
                spot = float((snap.get("latestTrade") or {}).get("p") or 0)
            except Exception as e:
                day_open = spot = 0
                self.j.event("alert_error", source="zdte", error=str(e)[:200])
            if not day_open or not spot:
                return
            kind = zd.direction(p, day_open, spot)
            strike = zd.strike_for(spot, kind, p["offset"])
            sym = zd.occ(und, today, kind, strike)
            try:
                q = (self.c.option_snapshots([sym]).get(sym) or {}).get("latestQuote") or {}
            except Exception:
                q = {}
            ask = float(q.get("ap") or 0)
            cash = book.d["sim_cash"] if sim else book.live_cash()  # the book's own money, not the account's
            budget = p["fraction"] * min(cash, equity)
            limit = ask if sim else round_option_price(ask + p["buy_pad"])  # live: a little above the ask, to get filled
            qty = math.floor(budget / (limit * 100)) if ask > 0 else 0
            side = "看涨" if kind == "C" else "看跌"
            if qty < 1:
                book.d["today"] = {"date": day, "status": "skipped", "symbol": sym}
                self.j.event("zdte_skip", dry_run=sim, symbol=sym,
                             why=f"末日跳过：{sym} " + (f"一张 ${ask * 100:,.2f}，超过可用 ${budget:,.2f}" if ask else "没有报价（可能今天没有当天到期的合约）"))
                return
            take_price = round_option_price(ask * p["take"])
            why = (f"末日买入：{und} 开盘 ${day_open:,.2f}、现在 ${spot:,.2f}，{'顺势' if p['direction'] == 'trend' else '只买看涨'}买{side}；"
                   f"行权价 ${strike:,.0f}（离现价 {abs(strike / spot - 1):.1%}），今天到期，{qty} 张 × ${limit:.2f}"
                   + ("" if sim else f"（卖价 ${ask:.2f} 加 ${p['buy_pad']:.2f}）")
                   + f"；止盈 {p['take']:g} 倍，{p['exit_at']} 前没到就平仓")
            evidence = {"ask": ask, "bid": q.get("bp"), "spot": spot, "open": day_open}
            if sim:
                self._order("zdte", evidence, why, symbol=sym, qty=str(qty), side="buy", type="limit",
                            limit_price=f"{round_option_price(ask):.2f}", time_in_force="day")
                book.d["today"] = {"date": day, "status": "open", "symbol": sym, "qty": qty, "price": ask,
                                   "paid": round(ask * 100 * qty, 2), "take_price": take_price, "opened_at": now,
                                   "simulated": True}
                book.d["sim_cash"] = round(book.d["sim_cash"] - ask * 100 * qty, 2)
            else:
                got = self._zero_dte_buy(sym, limit, qty, ask, budget, p, why, evidence)
                if not got:
                    book.d["today"] = {"date": day, "status": "skipped", "symbol": sym}
                    return
                price = got["price"]
                book.d["today"] = {"date": day, "status": "open", "symbol": sym, "qty": got["qty"], "price": price,
                                   "paid": round(price * 100 * got["qty"], 2), "filled": True,
                                   "take_price": round_option_price(price * p["take"]), "opened_at": now,
                                   "simulated": False}
        # Phase bookkeeping on the book's own money (rehearsal: the simulated balance; live: its ledger).
        value = book.d["sim_cash"] if sim else book.live_cash()
        open_t = book.trade_today(day)
        if not (open_t and open_t["status"] == "open"):
            msg = book.maybe_switch(value, p, day)
            if msg:
                self.j.event("zdte_switch", dry_run=sim, why=msg)
            elif value < book.d["start"] * (1 - self.cfg.get("attempt", {}).get("end_loss", 0.9)):
                if sim:
                    self.j.event("zdte_attempt_end", dry_run=True,
                                 why=f"模拟余额 ${value:,.2f} 低于结束线，这一轮（演练）归零；模拟账户重置为 ${book.d['start']:,.0f}")
                    book.d["history"].append({"date": day, "attempt_end": value})
                    book.d["sim_cash"] = book.d["start"]
                else:
                    book.new_round(day, value)
                    self.j.event("zdte_attempt_end", dry_run=False,
                                 why=f"末日账本 ${value:,.2f} 低于结束线，这一轮归零；第 {book.d['rounds']} 轮从 ${book.d['start']:,.0f} 重新开始")

    def _await_fill(self, cid: str, wait: float) -> dict:
        """The broker's view of an order, polled until it is filled or `wait` seconds pass."""
        deadline = time.monotonic() + wait
        while True:
            try:
                o = self.c.order_by_client_id(cid)
            except Exception:
                o = {}
            if o.get("status") == "filled" or time.monotonic() >= deadline:
                return o
            time.sleep(5)

    def _zero_dte_buy(self, sym: str, limit: float, qty: int, ask: float, budget: float, p: dict, why: str,
                      evidence: dict) -> dict | None:
        """Live entry, all inside this cycle: the limit, a wait for the fill, then at most one re-quote at
        the new ask + buy_pad (never above the first ask x chase_cap), then give up for the day. A
        resting buy would otherwise fill only when the price comes back, i.e. mostly when the move
        went against us. Returns {"qty", "price"} of what was bought, or None."""
        cap = round_option_price(ask * p["chase_cap"])
        for attempt in (1, 2):
            cid = uuid.uuid4().hex
            self._order("zdte", evidence, why, symbol=sym, qty=str(qty), side="buy", type="limit",
                        limit_price=f"{limit:.2f}", time_in_force="day", client_order_id=cid)
            o = self._await_fill(cid, p["fill_wait_s"])
            if o.get("id") and o.get("status") != "filled":
                try:
                    self.c.cancel_order(o["id"])
                except AlpacaError:
                    pass
                try:
                    o = self.c.order_by_client_id(cid)  # the final state: a fill can land during the cancel
                except Exception:
                    pass
            filled = int(float(o.get("filled_qty") or 0))
            if filled:
                price = float(o.get("filled_avg_price") or limit)
                self.j.event("zdte_fill", dry_run=False, symbol=sym, qty=filled, price=price, attempt=attempt,
                             why=f"末日买单成交：{filled} 张 × ${price:.2f}（第 {attempt} 次挂单）")
                return {"qty": filled, "price": price}
            if attempt == 2:
                break
            try:
                q = (self.c.option_snapshots([sym]).get(sym) or {}).get("latestQuote") or {}
            except Exception:
                q = {}
            new_ask = float(q.get("ap") or 0)
            limit = round_option_price(new_ask + p["buy_pad"]) if new_ask else 0.0
            qty = math.floor(budget / (limit * 100)) if limit else 0
            if not new_ask or limit > cap or qty < 1:
                self.j.event("zdte_skip", dry_run=False, symbol=sym,
                             why=f"末日跳过：{sym} {p['fill_wait_s']:g} 秒没成交，已撤单；新卖价 "
                                 + (f"${new_ask:.2f}，加价后 ${limit:.2f} 超过上限 ${cap:.2f}（首个卖价 × {p['chase_cap']:g}）"
                                    if new_ask else "取不到") + "，今天不做")
                return None
            evidence = {**evidence, "ask": new_ask, "bid": q.get("bp"), "chase_from": ask}
            why = f"末日追价：第一次挂单没成交，按新卖价 ${new_ask:.2f} 加 ${p['buy_pad']:.2f} 重挂 {qty} 张 × ${limit:.2f}"
        self.j.event("zdte_skip", dry_run=False, symbol=sym, why=f"末日跳过：{sym} 两次挂单都没成交，已撤单，今天不做")
        return None

    def _zero_dte_exit(self, book: "zd.Book", t: dict, bid: float, now: str) -> dict | None:
        """Live time exit. Cancel the resting orders (the take, or a buy that never filled), and
        only then sell: a sell while the take still holds the contracts would be rejected. The
        trade is booked once the broker shows the position gone; until then each cycle retries."""
        for o in self.c.open_orders():
            if o["symbol"] == t["symbol"]:
                try:
                    self.c.cancel_order(o["id"])
                except AlpacaError:
                    pass
        held = {x["symbol"] for x in self.c.positions()}
        if t["symbol"] in held:
            for _ in range(5):  # cancels are asynchronous: wait until no order holds the contracts
                if not any(o["symbol"] == t["symbol"] for o in self.c.open_orders()):
                    break
                time.sleep(1)
            self._close(t["symbol"], "zdte_exit", {"bid": bid}, why="末日期权 15:30 前平仓，不留到收盘")
            t.update({"exit_sent": True, "exit_bid": bid})
            return None
        if t.get("filled") or t.get("exit_sent"):
            return book.close(t.get("exit_bid", bid) * 100 * t["qty"], "收盘前平仓（按买价估算，以成交为准）", now)
        return book.close(t["paid"], "买单没有成交，已撤单", now)

    def _macro_hourly(self) -> None:
        """Record the macro gauges once an hour in market hours (the last daily bar is today's,
        so they move intraday). Record only: whether they cut exposure is still decided at the
        daily rebalance by stocks.macro_scale."""
        mark = self.j.root / "macro_hour.txt"
        hour = self.now.strftime("%Y-%m-%d %H")
        if mark.exists() and mark.read_text().strip() == hour:
            return
        g = macro.gauges(self.macro_closes or {})
        if not g:
            return
        cut = self.cfg["stocks"].get("macro_scale", 1.0) < 1.0 and macro.risk_off(g)
        self.j.event("macro", macro=g, macro_risk_off=macro.risk_off(g), macro_applied=cut, why=macro.summary(g))
        mark.write_text(hour + "\n")

    def _sleeves(self, today: date) -> None:
        """Run every sleeve once (hero.sleeves, hero.sleeve_rules). Never in a rehearsal: sleeves send orders."""
        from hero import sleeve_rules, sleeves
        if self.dry:
            self.j.event("sleeve_skip", dry_run=True, why="演练模式：多账本不下单")
            return
        specs = [s for s in self.cfg["sleeves"]["list"] if not s.get("plan_only")]  # plan-only sleeves never send orders
        if len(specs) < len(self.cfg["sleeves"]["list"]):
            try:
                self._sleeves_plan(today, only_plan_only=True)
            except Exception as e:
                self.j.event("alert_error", source="sleeves_plan", error=str(e)[:200])
        carry = {}
        for spec in specs:  # a sleeve can continue an older book's money (the first 0DTE ledger)
            if spec.get("carry") == "zdte" and (self.j.root / "zdte.json").exists():
                carry[spec["id"]] = zd.Book(self.j.root / "zdte.json", float(spec["start"])).live_cash()
        book = sleeves.Book(self.j.root / "sleeves.json", specs, carry)
        try:
            sleeves.Runner(self.c, self.j, self.cfg, specs, book, sleeve_rules.RULES, self.now, today).run()
        finally:
            book.save()

    def _sleeves_plan(self, today: date, only_plan_only: bool = False) -> None:
        from hero import sleeve_rules, sleeves
        specs = [s for s in self.cfg["sleeves"]["list"] if s.get("plan_only") or not only_plan_only]
        carry = {s["id"]: zd.Book(self.j.root / "zdte.json", float(s["start"])).live_cash()
                 for s in specs if s.get("carry") == "zdte" and (self.j.root / "zdte.json").exists()}
        book = sleeves.Book(self.j.root / "sleeves_plan.json", specs, carry)  # its own file: the real ledgers stay untouched
        for sid, cash in carry.items():  # follow the live 0DTE ledger while the plan runs beside it
            book.s(sid)["cash"] = cash
        sleeves.Runner(self.c, self.j, self.cfg, specs, book, sleeve_rules.RULES, self.now, today).plan()
        book.save()

    def _zdte_shadow(self, today: date) -> None:
        """The same-day rule on single stocks with real quotes; see hero.zdte_shadow. Never sends an order."""
        from hero import zdte_shadow as zs
        p = zs.settings(self.cfg)
        book = zs.Book(self.j.root / "zdte_shadow.json")
        day, now = today.isoformat(), zd.hhmm(self.now)
        tickets = book.today(day)
        open_t = {s: t for s, t in tickets.items() if t.get("status") == "open"}
        if open_t:
            snaps = self.c.option_snapshots([t["contract"] for t in open_t.values()])
            for sym, t in open_t.items():
                q = (snaps.get(t["contract"]) or {}).get("latestQuote") or {}
                bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
                t["best_bid"] = max(t.get("best_bid", 0.0), bid)
                take = p["take"] * t["ask"]
                if bid >= take:
                    t.update({"status": "closed", "exit": "take", "exit_bid": bid, "exit_at": now, "ret": round(p["take"] - 1, 3)})
                elif now >= p["exit_at"]:
                    t.update({"status": "closed", "exit": "time", "exit_bid": bid, "exit_ask": ask, "exit_at": now,
                              "exit_spread": zs.spread(bid, ask), "ret": round(bid / t["ask"] - 1, 3)})
                else:
                    continue
                self.j.event("zdte_shadow_result", dry_run=True, symbol=t["contract"], ret=t["ret"],
                             why=f"个股末日影子单：{sym} {t['contract']} 按卖价 ${t['ask']:.2f} 买，"
                                 + (f"买价碰到 ${take:.2f}（{p['take']:g} 倍）止盈" if t["exit"] == "take"
                                    else f"{p['exit_at']} 按买价 ${bid:.2f} 卖出") + f"，{t['ret']:+.0%}（只记录，不下单）")
        if p["entry"] <= now < p["exit_at"]:
            todo = [s for s in p["symbols"] if s not in tickets]
            snaps = self.c.stock_snapshots(todo) if todo else {}
            for sym in todo:
                snap = snaps.get(sym) or {}
                day_open = float((snap.get("dailyBar") or {}).get("o") or 0)
                spot = float((snap.get("latestTrade") or {}).get("p") or 0)
                if not day_open or not spot:
                    continue
                kind = "C" if spot >= day_open else "P"
                lo, hi = spot * (1 - p["offset"] - 0.03), spot * (1 + p["offset"] + 0.03)
                cs = self.c.option_contracts(sym, status="active", type="call" if kind == "C" else "put",
                                             expiration_date=day, strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
                c = zs.pick([x for x in cs if x.get("expiration_date", day) == day], kind, spot, p["offset"])
                if not c:
                    tickets[sym] = {"status": "no_expiry"}  # no contract expiring today: nothing to log
                    continue
                q = (self.c.option_snapshots([c["symbol"]]).get(c["symbol"]) or {}).get("latestQuote") or {}
                bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
                if ask <= 0:
                    continue  # no quote yet: try again next cycle
                sp = zs.spread(bid, ask)
                tickets[sym] = {"status": "open", "contract": c["symbol"], "kind": kind, "strike": float(c["strike_price"]),
                                "spot": spot, "open": day_open, "bid": bid, "ask": ask, "spread": sp, "opened_at": now,
                                "best_bid": bid}
                self.j.event("zdte_shadow_open", dry_run=True, symbol=c["symbol"],
                             evidence={"bid": bid, "ask": ask, "spot": spot, "open": day_open},
                             why=f"个股末日影子单：{sym} {'涨' if kind == 'C' else '跌'}，看{'涨' if kind == 'C' else '跌'} "
                                 f"{float(c['strike_price']):g}，买价 ${bid:.2f} / 卖价 ${ask:.2f}"
                                 + (f"，价差占中间价 {sp:.0%}" if sp is not None else "") + "（只记录，不下单）")
        book.save()

    def _net_shadow(self, closes: dict, today: date) -> None:
        """Simulated net of cheap calls in wild weeks; see hero.net. Never sends an order."""
        from hero import research_net as rn
        from hero.research_universe import ETFS
        p = netlib.settings(self.cfg)
        book = netlib.Book(self.j.root / "net.json", float(p["start_capital"]))
        now, day = zd.hhmm(self.now), today.isoformat()
        # 1. open tickets: the take when the bid reaches it; expiry at intrinsic value.
        if book.d["open"]:
            snaps = self.c.option_snapshots([t["symbol"] for t in book.d["open"]])
            for t in list(book.d["open"]):
                q = (snaps.get(t["symbol"]) or {}).get("latestQuote") or {}
                bid = float(q.get("bp") or 0)
                take = p["take"] * t["price"]
                if bid >= take:
                    done = book.close(t, take, f"{p['take']:g} 倍止盈", day)
                elif day > t["expiry"]:
                    bars = self.c.daily_bars([t["underlying"]], t["expiry"], adjustment="raw").get(t["underlying"], [])
                    settle = next((float(b["c"]) for b in bars if b["t"][:10] == t["expiry"]), None)
                    if settle is None:
                        continue
                    done = book.close(t, max(settle - t["strike"], 0.0), f"到期（收盘 ${settle:,.2f}）", day)
                else:
                    continue
                self.j.event("net_result", dry_run=True, symbol=t["symbol"], ret=done["ret"],
                             why=f"撒网演练：{t['symbol']} {done['exit']}，花 ${t['paid']:,.2f}、收回 ${done['proceeds']:,.2f}"
                                 f"（{done['ret']:+.0%}），模拟余额 ${book.d['cash']:,.2f}")
            msg = book.round_check(p)
            if msg:
                self.j.event("net_round", dry_run=True, why=msg)
        # 2. once a week, after the entry time: is the market wild? if so, cast the net.
        wk = netlib.week_key(today)
        if book.d["checked"] != wk and now >= p["entry"]:
            book.d["checked"] = wk
            spy_bars = self.c.daily_bars(["SPY"], (today - timedelta(days=560)).isoformat(), adjustment="raw").get("SPY", [])
            spy = {b["t"][:10]: float(b["c"]) for b in spy_bars if b["t"][:10] < day}  # completed days only
            last = max(spy) if spy else None
            sig = rn.signals(spy, [last]) if last else {}
            fired = [k for k in ("rebound", "wild") if sig.get(k)]
            book.d["last_signal"] = {"week": wk, "as_of": last, "fired": fired}
            if not fired:
                self.j.event("net_skip", dry_run=True, why=f"撒网演练：{wk} 市场不够疯狂（截至 {last}），这周不出手")
            else:
                pool = [s for s in self.cfg["universe"] if s not in ETFS and len(closes.get(s, [])) >= 64]
                ranked = sorted(((netlib.vol(closes[s][-64:]), s) for s in pool), reverse=True)[:p["net"]]
                names = [s for _, s in ranked]
                spots = {s: float((v.get("latestTrade") or {}).get("p") or 0) for s, v in self.c.stock_snapshots(names).items()}
                picks = []
                for s in names:
                    spot = spots.get(s)
                    if not spot:
                        continue
                    cs = self.c.option_contracts(s, status="active", type="call",
                                                 expiration_date_gte=(today + timedelta(days=p["min_days"])).isoformat(),
                                                 expiration_date_lte=(today + timedelta(days=p["max_days"])).isoformat(),
                                                 strike_price_gte=f"{spot * (1 + p['otm']):.2f}",
                                                 strike_price_lte=f"{spot * (1 + p['otm'] + 0.2):.2f}")
                    if not cs:
                        continue
                    exp = min({c["expiration_date"] for c in cs}, key=lambda x: abs((date.fromisoformat(x) - today).days - 28))
                    c = min((c for c in cs if c["expiration_date"] == exp), key=lambda c: float(c["strike_price"]))
                    picks.append((s, spot, c))
                asks = self.c.option_snapshots([c["symbol"] for _, _, c in picks]) if picks else {}
                picks = [(s, spot, c, float(((asks.get(c["symbol"]) or {}).get("latestQuote") or {}).get("ap") or 0))
                         for s, spot, c in picks]
                picks = [x for x in picks if x[3] > 0 and x[2]["symbol"] not in {t["symbol"] for t in book.d["open"]}]
                if picks:
                    each = p["fraction"] * book.d["cash"] / len(picks)
                    for s, spot, c, ask in picks:
                        qty = round(each / (ask * 100), 4)  # fractional: the simulation spreads $500 over the net
                        t = {"symbol": c["symbol"], "underlying": s, "strike": float(c["strike_price"]),
                             "expiry": c["expiration_date"], "price": ask, "qty": qty, "paid": round(ask * 100 * qty, 2),
                             "opened": day, "spot": spot, "signal": fired}
                        book.d["open"].append(t)
                        book.d["cash"] = round(book.d["cash"] - t["paid"], 2)
                    self.j.event("net_buy", dry_run=True, why=(
                        f"撒网演练：{wk} 信号 {'、'.join('暴跌后反弹' if f == 'rebound' else '高波动' for f in fired)}，"
                        f"买 {len(picks)} 只最波动股票约 4 周后到期、价外 {p['otm']:.0%} 的看涨："
                        + "、".join(f"{s} ${float(c['strike_price']):g}@{ask:.2f}" for s, _, c, ask in picks)
                        + f"；共 ${sum(round(a * 100 * round(each / (a * 100), 4), 2) for *_, a in picks):,.2f}，"
                        f"模拟余额 ${book.d['cash']:,.2f}"))
                else:
                    self.j.event("net_skip", dry_run=True, why=f"撒网演练：{wk} 有信号但找不到合适的期权报价")
        book.save()

    def _lottery(self, ledger: "lot.Ledger", closes: dict, today: date) -> None:
        """One small earnings call a week until a 10x hit, a doubled pot or an empty budget."""
        p, d = ledger.p, ledger.d
        if not ledger.active:
            return
        bet = d["open"]
        if bet and today.isoformat() >= bet["exit"]:
            try:
                quote = (self.c.option_snapshots([bet["symbol"]]).get(bet["symbol"]) or {}).get("latestQuote") or {}
            except Exception:
                quote = {}
            bid = float(quote.get("bp") or 0)
            why = (f"彩票仓卖出：{bet['underlying']} 财报后开盘卖出（{bet['report']}）；"
                   f"买价 ${bet['cost']:,.2f}，按买价 ${bid:.2f} 估算卖得 ${bid * 100:,.2f}")
            if self.dry:
                self.j.event("close", symbol=bet["symbol"], reason="lottery_exit", why=why, dry_run=True,
                             evidence={"bid": quote.get("bp"), "ask": quote.get("ap")}, intent_key="dry")
            else:
                self._close(bet["symbol"], "lottery_exit", {"bid": quote.get("bp"), "ask": quote.get("ap")}, why=why)
            done = ledger.closed(bid * 100, today.isoformat())
            self.j.event("lottery_result", dry_run=self.dry, symbol=bet["symbol"], ret=done["ret"], pot=round(d["pot"], 2),
                         why=f"彩票仓结果：{bet['underlying']} 回报 {done['ret']:+.0%}，彩票资金 ${d['pot']:,.2f}"
                             + (f"；{d['done']}" if d["done"] else ""))
            ledger.save()
            return
        if bet:
            ledger.save()
            return
        pick = d.get("pick")
        week = "%d-W%02d" % today.isocalendar()[:2]
        if not pick or pick.get("week") != week:
            moms = {}
            for s in self.cfg["universe"]:
                xs = getattr(self, "rank_closes", closes).get(s)
                if xs and len(xs) > 127:
                    moms[s] = xs[-1] / xs[-127] - 1
            pick = lot.pick_for_week(self.earnings, moms, today)
            pick = {**(pick or {}), "week": week}
            d["pick"] = pick
            self.j.event("lottery_pick", dry_run=self.dry, **{k: v for k, v in pick.items() if k != "report"},
                         why=(f"本周彩票：{pick['symbol']}（财报 {earn.describe(pick['report'])}，126 天动量 {pick['momentum']:+.0%}，"
                              f"本周发财报的股票里最强），{pick['entry']} 收盘前买入，{pick['exit']} 开盘卖出")
                         if pick.get("symbol") else "本周股票池里没有合适的财报，不买彩票")
        if not pick.get("symbol") or pick.get("bought") or pick.get("skipped") or pick["entry"] != today.isoformat():
            ledger.save()
            return
        if not lot.entry_time(self.now) or pick["symbol"] not in closes:
            ledger.save()
            return
        sym, spot = pick["symbol"], closes[pick["symbol"]][-1]
        exit_day = date.fromisoformat(pick["exit"])
        try:
            contracts = self.c.option_contracts(sym, type="call", status="active",
                                                expiration_date_gte=exit_day.isoformat(),
                                                expiration_date_lte=(exit_day + timedelta(days=10)).isoformat(),
                                                strike_price_gte=f"{spot * (1 + p['otm']):.2f}",
                                                strike_price_lte=f"{spot * (1 + p['max_otm']):.2f}")
            first = min((c["expiration_date"] for c in contracts), default=None)
            contracts = [c for c in contracts if c["expiration_date"] == first]
            snaps = self.c.option_snapshots([c["symbol"] for c in contracts]) if contracts else {}
        except Exception as ex:
            contracts, snaps = [], {}
            self.j.event("alert_error", source="lottery", error=str(ex)[:200])
        choice = lot.choose_contract(contracts, snaps, spot, p)
        stake = min(p["stake_max"], d["pot"])
        if not choice or choice[1] * 100 > stake:
            pick["skipped"] = True
            self.j.event("lottery_skip", dry_run=self.dry, symbol=sym,
                         why=f"彩票仓跳过 {sym}：现价 ${spot:,.2f} 上方 {p['otm']:.0%}–{p['max_otm']:.0%} 没有一张在 ${stake:,.0f} 以内的看涨期权")
            ledger.save()
            return
        c, ask = choice
        cost = ask * 100
        why = (f"彩票仓买入：{sym} 将于 {earn.describe(pick['report'])} 发财报，本周 126 天动量最强（{pick['momentum']:+.0%}）；"
               f"行权价 ${float(c['strike_price']):,.2f}（现价上方 {float(c['strike_price']) / spot - 1:.0%}），{c['expiration_date']} 到期，"
               f"一张 ${cost:,.2f}；最多亏掉这 ${cost:,.2f}，{pick['exit']} 开盘卖出。彩票资金剩 ${d['pot'] - cost:,.2f}")
        self._order("lottery", {"ask": ask, "spot": spot, "strike": c["strike_price"]}, why, symbol=c["symbol"], qty="1",
                    side="buy", type="limit", limit_price=f"{round_option_price(ask):.2f}", time_in_force="day")
        ledger.opened({"symbol": c["symbol"], "underlying": sym, "strike": float(c["strike_price"]),
                       "expiry": c["expiration_date"], "cost": round(cost, 2), "entry": today.isoformat(),
                       "exit": pick["exit"], "report": earn.describe(pick["report"]), "dry_run": self.dry})
        pick["bought"] = True
        ledger.save()

    def _launch_allowed(self, today: date) -> bool:
        """A gated experiment (one with an attempt or a live_from date) trades only with a valid
        live_from that has arrived AND launch_approved explicitly true. A missing, empty or
        malformed field means rehearsal, never a launch. Ungated configs (the main account) trade."""
        gated = any(k in self.cfg for k in ("attempt", "live_from", "launch_approved"))
        if not gated:
            return True
        live_from = self.cfg.get("live_from")
        try:
            start = date.fromisoformat(live_from) if isinstance(live_from, str) else None
        except ValueError:
            start = None
        return start is not None and today >= start and self.cfg.get("launch_approved") is True

    def _attempt_over(self, attempt: dict, equity: float, state: dict, today: date) -> bool:
        """Start-capital loss ends the attempt, in three phases:

        1. stop opening: once the loss line is hit, an "ending" latch is saved; it never resets,
           even if equity bounces back;
        2. exit and reconcile: every cycle re-reads the broker, cancels stops and buys, and closes
           what is left. A symbol with a working sell order is waited on, not re-sent;
        3. confirm flat: only when the broker shows no positions and no open orders is the attempt
           marked ended. A new attempt needs a fresh or reset account and a new number in the config."""
        n = attempt["number"]
        if state.get(f"attempt_{n}_ended"):
            return True
        start = attempt["start_capital"]
        loss = 1 - equity / start
        if not state.get(f"attempt_{n}_ending"):
            if loss < attempt["end_loss"]:
                return False
            self.j.event("attempt_end", attempt=n, equity=equity, start_capital=start,
                         start_capital_loss=round(loss, 4), dry_run=self.dry,
                         why=f"第 {n} 次尝试触及结束线：净值 ${equity:,.2f}，起始资金 ${start:,.0f} 已亏 {loss:.1%}"
                             f"（结束线 {attempt['end_loss']:.0%}）。停止开新仓，开始平仓；券商确认无持仓、无挂单后本轮才算结束")
            if not self.dry:
                state[f"attempt_{n}_ending"] = today.isoformat()
                self.j.save_state(state)

        positions, orders = self.c.positions(), self.c.open_orders()
        if not positions and not orders:
            self.j.event("attempt_end_confirmed", attempt=n, equity=equity, dry_run=self.dry,
                         why=f"券商确认无持仓、无挂单，第 {n} 次尝试结束（净值 ${equity:,.2f}）")
            if not self.dry:
                state[f"attempt_{n}_ended"] = today.isoformat()
                self.j.save_state(state)
            return True

        # A working exit (ours, recognised by its client_order_id prefix, or any other non-stop sell)
        # is waited on: after a crash or an uncertain submit the broker's open orders are the truth.
        def is_exit(o):
            return ((o.get("client_order_id") or "").startswith(EXIT_PREFIX)
                    or (o.get("side") == "sell" and not stoplib.is_protective_stop(o)))
        exiting = {o["symbol"] for o in orders if is_exit(o)}
        blocked = set()
        for o in orders:
            if is_exit(o):
                continue
            self.j.event("cancel", symbol=o.get("symbol"), reason="attempt_end", dry_run=self.dry,
                         order_key=execution_key(o["id"]), why="尝试结束：撤销止损单和买单，准备平仓")
            if self.dry:
                continue
            try:
                status = self.c.cancel_order(o["id"])
            except AlpacaError as e:
                status = f"error: {str(e)[:300]}"
            self.j.event("cancel_ack", symbol=o.get("symbol"), order_key=execution_key(o["id"]), status=status)
            if status != "canceled":
                blocked.add(o["symbol"])  # still live, filled or failed: re-read the broker next cycle
        # Exits sent earlier whose outcome we did not see (crash, timeout): ask the broker by our own
        # client_order_id before sending another. Only "not found" (404) proves it never arrived.
        sent = state.setdefault(f"attempt_{n}_exits", {})
        for sym, cid in list(sent.items()):
            try:
                o = self.c.order_by_client_id(cid)
            except AlpacaError as e:
                if e.status == 404:
                    del sent[sym]  # never reached the broker: safe to send again
                else:
                    exiting.add(sym)  # unknown: wait rather than risk a duplicate exit
                    self.j.event("attempt_exit_pending", symbol=sym, dry_run=self.dry,
                                 why=f"无法确认上一张平仓单的状态（{str(e)[:80]}），本轮不重发")
                continue
            if o.get("status") in ("filled", "canceled", "expired", "rejected", "done_for_day"):
                del sent[sym]  # final: the position read above already reflects it
            else:
                exiting.add(sym)
        for pos in positions:
            sym = pos["symbol"]
            if sym in exiting:
                self.j.event("attempt_exit_pending", symbol=sym, dry_run=self.dry,
                             why="平仓单还在券商处理中，本轮等待，不重复下单")
            elif sym in blocked:
                self.j.event("attempt_exit_pending", symbol=sym, dry_run=self.dry,
                             why="撤单未确认，下一轮重新核对券商状态后再平仓")
            else:
                qty = abs(float(pos["qty"]))
                cid = EXIT_PREFIX + uuid.uuid4().hex
                if not self.dry:
                    sent[sym] = cid  # persisted before the broker call, so a crash cannot lose it
                    self.j.save_state(state)
                self._order("attempt_end", {"qty": pos["qty"], "current_price": pos.get("current_price")},
                            "尝试结束，平掉剩余仓位（按券商确认的剩余数量；每轮核对，直到券商确认清空）",
                            client_order_id=cid, symbol=sym, qty=f"{qty:.9g}",
                            side="sell" if float(pos["qty"]) > 0 else "buy", type="market", time_in_force="day")
        return True

    def _cancel_stops(self, sym: str, why: str) -> bool:
        """Cancel our resting stops on sym. Returns False if a stop had already filled, i.e. the
        position is (partly) gone and the caller must not sell it again this cycle."""
        self.touched.add(sym)
        clear = True
        for o in self.stops.pop(sym, []):
            self.j.event("cancel", symbol=sym, reason="protective_stop", why=why, dry_run=self.dry,
                         order_key=execution_key(o["id"]), stop_price=o.get("stop_price"), qty=o.get("qty"))
            if self.dry:
                continue
            try:
                status = self.c.cancel_order(o["id"])
            except Exception as e:
                status = f"error: {str(e)[:300]}"
            self.j.event("cancel_ack", symbol=sym, order_key=execution_key(o["id"]), status=status)
            if status != "canceled":
                clear = False  # filled, still pending or failed: re-read the broker next cycle
        return clear

    def _reconcile_stops(self, stocks: dict, busy: set, closes: dict) -> None:
        """Every held stock gets exactly one resting GTC stop for its full quantity.

        Symbols sold, trimmed or with pending orders this cycle are skipped: their quantity is
        changing, so the next cycle sizes the stop to the settled position."""
        risk = self.cfg["risk"]
        for sym, pos in stocks.items():
            if sym in busy or sym in self.touched:
                continue
            qty = float(pos["qty"])
            if qty <= 0:
                continue
            existing = self.stops.get(sym, [])
            pct, daily = stoplib.stop_pct(closes.get(sym), risk)
            entry, current = float(pos["avg_entry_price"]), float(pos["current_price"])
            price = stoplib.stop_price(entry, current, pct)
            cautious = getattr(self, "cautious", {})
            tighten = (getattr(self, "breaker", None) and f"市场熔断：{self.breaker}") or cautious.get(sym)
            if tighten:
                price = max(price, alerts.tight_stop(current, self.circuit))  # only ever raised
            same_qty = len(existing) == 1 and abs(float(existing[0]["qty"]) - qty) < 1e-9
            if same_qty and not (tighten and float(existing[0].get("stop_price") or 0) < price * 0.995):
                continue
            if existing:
                self._cancel_stops(sym, f"{tighten}，止损收紧到 ${price:,.2f}" if same_qty
                                   else f"持股数变为 {qty:g}，撤销旧止损单后按新股数重挂")
            basis = (f"{risk['stop_vol_mult']:g} 倍日波动率 {daily:.1%}" if daily is not None else "波动率数据不足，取上限")
            tif = risk.get("stop_tif", "gtc")
            life = "GTC 长期有效" if tif == "gtc" else "当日有效，收盘失效，次日第一轮重挂"
            why = (f"保护性止损（挂在券商端，{life}）：成本 ${entry:,.2f}，现价 ${current:,.2f}；"
                   f"止损距离 {pct:.1%}（{basis}，限制在 {risk['stop_min_pct']:.0%}–{risk['stop_max_pct']:.0%}），"
                   f"止损价 ${price:,.2f}")
            if tighten:
                why += f"；{tighten}，收紧到现价下方 {self.circuit['tight_stop_pct']:.0%}（只收紧不放松）"
            evidence = {"avg_entry_price": entry, "current_price": current, "daily_vol": daily, "stop_pct": pct,
                        "tightened": bool(tighten)}
            self._order("protective_stop", evidence, why, cid_prefix=stoplib.PREFIX, symbol=sym, qty=f"{qty:.9g}",
                        side="sell", type="stop", stop_price=f"{price:.2f}", time_in_force=tif)

    def _option_exits(self, options: dict, busy: set, today: date) -> None:
        for sym, pos in options.items():
            if sym in busy:
                continue
            exp = opt.occ_expiration(sym)
            report = earn.next_report(self.earnings, option_underlying(sym), today) \
                if self.cfg["options"].get("earnings_guard") else None
            if report and earn.last_safe_expiry(report) < date.fromisoformat(exp) \
                    and (date.fromisoformat(report["date"]) - today).days <= 1:
                # Safety net for a report moved earlier after we bought: sell before it, not after.
                self._close(sym, "earnings", {k: pos.get(k) for k in ("unrealized_plpc", "qty")},
                            why=f"{option_underlying(sym)} 将于 {earn.describe(report)} 发财报，合约 {exp} 到期会跨过财报，财报前平仓")
                continue
            reason = opt.should_exit(pos, today, exp, self.cfg["options"])
            if reason and reason.startswith("take_profit") and self.cfg["options"].get("hold_overnight") \
                    and self._bought_today(sym, today):
                # Small accounts get three day trades per five sessions; don't spend one on a same-day
                # profit. Stop-loss and expiry exits still go out the same day.
                continue
            if reason:
                self._close(sym, reason, {k: pos.get(k) for k in
                                          ("avg_entry_price", "current_price", "unrealized_plpc", "qty")},
                            why=opt.why_exit(pos, today, exp, self.cfg["options"]))

    def _bought_today(self, sym: str, today: date) -> bool:
        return any(f["symbol"] == sym and f["side"] == "buy" for f in self.j.fills(today.isoformat()))

    def _rebalance(self, stocks: dict, busy: set, closes: dict, equity: float, halted: bool) -> None:
        p, risk = self.cfg["stocks"], self.cfg["risk"]
        held = frozenset(stocks)
        mc = getattr(self, "macro_closes", None)
        pool = getattr(self, "rank_closes", closes)
        targets = momentum.target_weights(pool, p, self.cfg["regime_symbol"], risk["max_position_pct"], held=held,
                                          macro_closes=mc)
        score = momentum.scores(pool, p, held)
        regime = self.cfg["regime_symbol"]
        bull = regime not in closes or momentum.is_bullish(closes[regime])
        gauges = macro.gauges(mc or {})
        macro_cut = p.get("macro_scale", 1.0) < 1.0 and macro.risk_off(gauges)
        soon = {}
        today = getattr(self, "today", date.today())
        if earn.usable(self.earnings, today):
            for sym in sorted(set(targets) | set(stocks)):
                r = earn.next_report(self.earnings, sym, today)
                if r and (date.fromisoformat(r["date"]) - today).days <= 3:
                    soon[sym] = r
        if soon:
            self.j.event("earnings_watch", dry_run=self.dry, reports=soon,
                         why="3 天内发财报（只提示，不改股票仓位）：" + "；".join(f"{s} {earn.describe(r)}" for s, r in soon.items()))
        self.j.event("targets", weights={s: round(w, 4) for s, w in targets.items()}, scores=score,
                     regime="bull" if bull else "bear", macro=gauges, macro_risk_off=macro.risk_off(gauges),
                     macro_applied=macro_cut, why=macro.summary(gauges)
                     + ("" if p.get("macro_scale", 1.0) < 1.0 else "（目前只记录，不影响仓位）"))

        for sym in stocks:
            if sym not in targets and sym not in busy:
                if not self._cancel_stops(sym, "卖出前先撤销保护性止损单，释放被占用的股份"):
                    continue
                self._close(sym, "dropped_from_targets", {k: score.get(sym, {}).get(k) for k in
                                                          ("momentum", "above_trend", "rsi", "rank")},
                            why="移出目标：" + momentum.why_dropped(sym, score.get(sym), p))

        buys = []
        for sym, w in targets.items():
            if sym in busy or sym not in closes:
                continue
            price = closes[sym][-1]
            have = float(stocks[sym]["market_value"]) if sym in stocks else 0.0
            want = w * equity
            if have and abs(have - want) / want <= p["rebalance_drift"]:
                continue
            if p.get("fractional"):
                size = {"notional": f"{abs(want - have):.2f}"}  # dollar amount; broker fills fractional shares
                if abs(want - have) < 1:
                    continue
            else:
                qty = math.floor(abs(want - have) / price)
                if qty < 1:
                    continue
                size = {"qty": str(qty)}
            ref = {"reference_price": price, "reference": "IEX daily bar, latest trade at fetch time (may lag)",
                   "target_weight": w, **{k: score[sym].get(k) for k in ("momentum", "above_trend", "rsi", "rank")}}
            now = f"当前 {have / equity:.1%}" if have else "新建仓"
            why = (f"{momentum.why_selected(sym, score[sym], p)}；目标仓位 {w:.0%}（{now}"
                   + (f"，偏离超过 {p['rebalance_drift']:.0%}" if have else "") + "）")
            if not bull:
                why += f"；{regime} 跌破 200 日均线，总仓位降到 {momentum.BEAR_EXPOSURE_SCALE:.0%}"
            if macro_cut:
                why += f"；{macro.summary(gauges)}，总仓位再乘 {p['macro_scale']:.0%}"
            if want < have:
                if not self._cancel_stops(sym, "减仓前先撤销保护性止损单，减仓后按新股数重挂"):
                    continue
                self._order("trim", ref, why, symbol=sym, **size, side="sell", type="market", time_in_force="day")
            else:
                buys.append((sym, size, ref, why))
        if halted:
            return
        breaker, cautious = getattr(self, "breaker", None), getattr(self, "cautious", {})
        for sym, size, ref, why in buys:
            if breaker or sym in cautious:
                self.j.event("buy_blocked", symbol=sym, dry_run=self.dry,
                             why=f"本该买入（{why}），但{'市场熔断：' + breaker if breaker else cautious[sym]}，今天不买")
                continue
            self._order("rebalance", ref, why, symbol=sym, **size, side="buy", type="market", time_in_force="day")

    def _option_entries(self, options: dict, busy: set, closes: dict, equity: float, today: date,
                        stocks: dict | None = None) -> None:
        p = self.cfg["options"]
        pending = [s for s in busy if len(s) > 15 and s[-9] in "CP"]
        slots = p["max_positions"] - len(options) - len(pending)
        if slots <= 0:
            return
        held = {option_underlying(s) for s in [*options, *pending]}
        regime = self.cfg["regime_symbol"]
        if regime in closes and not momentum.is_bullish(closes[regime]):
            candidates = [(regime, "put")] if p.get("bear_puts", True) else []
        else:
            ranked = momentum.rank(getattr(self, "rank_closes", closes), self.cfg["stocks"])
            candidates = [(s, "call") for s in ranked[: p.get("max_rank", len(ranked))]]
            if p.get("require_held"):
                # Only on stocks the broker confirms we hold: a rejected or unfilled stock buy must
                # not leave a lone option behind. Same-cycle buys qualify from the next cycle.
                owned = {s for s, pos in (stocks or {}).items() if float(pos.get("qty") or 0) > 0}
                missing = [s for s, _ in candidates if s not in owned]
                candidates = [(s, k) for s, k in candidates if s in owned]
                if missing:
                    self.j.event("option_skip", dry_run=self.dry,
                                 why="没有买期权：" + "、".join(missing) + " 还没有券商确认的股票持仓（期权只买已持有的股票）")
        order_of = {s: i for i, (s, _) in enumerate(candidates, 1)}
        budget = p["allocation"] * equity / p["max_positions"]
        skipped = []  # why each candidate produced no order, so the journal shows options that were looked for

        for und, kind in candidates:
            if slots <= 0:
                break
            if und in held or und not in closes:
                continue
            if und in getattr(self, "cautious", {}):
                skipped.append(f"{und}：{self.cautious[und]}，今天不买它的期权")
                continue
            spot = closes[und][-1]
            latest = today + timedelta(days=p["max_dte"])
            if p.get("earnings_guard"):
                # Never hold a short-dated option through an earnings report: the expiry must settle
                # before it. An unavailable calendar means unknown, and unknown means no option.
                if not earn.usable(self.earnings, today):
                    skipped.append(f"{und}：财报日历不可用或过期，无法确认到期前没有财报")
                    continue
                report = earn.next_report(self.earnings, und, today)
                if report and earn.last_safe_expiry(report) < latest:
                    latest = earn.last_safe_expiry(report)
                    if latest < today + timedelta(days=p["min_dte"]):
                        skipped.append(f"{und}：{earn.describe(report)} 发财报，{p['min_dte']} 天以上的合约都会跨过财报")
                        continue
            contracts = self.c.option_contracts(
                und, type=kind, status="active",
                expiration_date_gte=(today + timedelta(days=p["min_dte"])).isoformat(),
                expiration_date_lte=latest.isoformat(),
                strike_price_gte=f"{spot * (1 - p.get('strike_band', 0.1)):.2f}",
                strike_price_lte=f"{spot * (1 + p.get('strike_band', 0.1)):.2f}")
            if not contracts:
                skipped.append(f"{und}：期限和行权价范围内没有合约")
                continue
            snaps = self.c.option_snapshots([c["symbol"] for c in contracts])
            pick = opt.pick_contract(contracts, snaps, spot, p, budget if p.get("budget_filter") else None)
            if not pick:
                cheapest = min((m * 100 for m in (opt.mid(snaps[c["symbol"]], 1.0) if c["symbol"] in snaps else None
                                                  for c in contracts) if m), default=None)
                why_not = opt.reject_reasons(contracts, snaps, p, budget if p.get("budget_filter") else None)
                skipped.append(f"{und}：{len(contracts)} 张合约都不合格（预算 ${budget:,.0f}"
                               + (f"，最便宜一张约 ${cheapest:,.0f}" if cheapest else "，没有有效报价")
                               + ("；" + "、".join(f"{k} {v}" for k, v in sorted(why_not.items(), key=lambda kv: -kv[1]))
                                  if why_not else "") + "）")
                continue
            contract, price = pick
            qty = math.floor(budget / (price * 100))
            if qty < 1:
                skipped.append(f"{und}：合格合约一张 ${price * 100:,.0f}，超过预算 ${budget:,.0f}")
                continue
            snap = snaps[contract["symbol"]]
            quote, greeks = snap.get("latestQuote") or {}, snap.get("greeks") or {}
            evidence = {"bid": quote.get("bp"), "ask": quote.get("ap"), "bid_size": quote.get("bs"),
                        "ask_size": quote.get("as"), "quote_time": quote.get("t"), "mid": round(price, 4),
                        "spread_pct_of_mid": round((quote["ap"] - quote["bp"]) / price, 4),
                        "delta": greeks.get("delta"), "iv": snap.get("impliedVolatility"),
                        "underlying_price": spot,
                        "underlying_price_source": "IEX daily bar, latest trade at fetch time (may lag)", "feed": "indicative"}
            days = opt.dte(contract["expiration_date"], today)
            delta = f"delta {evidence['delta']:.2f}" if evidence["delta"] is not None else "delta 缺失，按最接近平值选"
            if kind == "put":
                why = f"{regime} 跌破 200 日均线（熊市信号），买看跌期权防守"
            else:
                why = f"{und} 是动量排名第 {order_of[und]} 且还没有期权的标的，买看涨期权放大趋势收益"
            why += (f"；选 {days} 天到期、delta 最接近 {p['target_delta']} 的合约（{delta}），"
                    f"每笔预算约 ${budget:,.0f}，买卖价差 {evidence['spread_pct_of_mid']:.1%}")
            self._order(f"{kind} on {und}", evidence, why, symbol=contract["symbol"], qty=str(qty), side="buy",
                        type="limit", limit_price=f"{round_option_price(price):.2f}", time_in_force="day")
            held.add(und)
            slots -= 1
        if skipped and slots > 0:
            self.j.event("option_skip", dry_run=self.dry, why="没有买期权：" + "；".join(skipped))
