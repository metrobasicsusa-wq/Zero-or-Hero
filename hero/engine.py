"""One trading cycle: exits, stock rebalance, option entries. Safe to run repeatedly."""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone

from hero import alerts
from hero import earnings as earn
from hero import macro
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

    def run(self, today: date | None = None, force: bool = False) -> dict:
        today = today or date.today()
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
        self._intraday(stocks, today)

        self._option_exits(options, busy, today)
        if state.get("last_rebalance") != today.isoformat():
            self._rebalance(stocks, busy, closes, equity, halted)
            state["last_rebalance"] = today.isoformat()
        if self.cfg["options"]["enabled"] and not halted and not self.breaker:
            self._option_entries(options, busy, closes, equity, today, stocks)
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
