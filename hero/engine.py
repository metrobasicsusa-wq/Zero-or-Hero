"""One trading cycle: exits, stock rebalance, option entries. Safe to run repeatedly."""

from __future__ import annotations

import math
from datetime import date, timedelta

from hero.journal import Journal
from hero.strategies import momentum
from hero.strategies import options as opt

HISTORY_DAYS = 420


def closes_from_bars(bars: dict[str, list[dict]]) -> dict[str, list[float]]:
    return {s: [float(b["c"]) for b in bs] for s, bs in bars.items() if bs}


def option_underlying(symbol: str) -> str:
    return symbol[:-15]


def round_option_price(x: float) -> float:
    tick = 0.05 if x >= 3 else 0.01
    return round(round(x / tick) * tick, 2)


class Engine:
    def __init__(self, client, cfg: dict, journal: Journal, dry_run: bool = False):
        self.c, self.cfg, self.j, self.dry = client, cfg, journal, dry_run

    def _order(self, reason: str, **order) -> None:
        self.j.event("order", reason=reason, dry_run=self.dry, **order)
        if not self.dry:
            self.c.submit_order(**order)

    def _close(self, symbol: str, reason: str) -> None:
        self.j.event("close", symbol=symbol, reason=reason, dry_run=self.dry)
        if not self.dry:
            self.c.close_position(symbol)

    def run(self, today: date | None = None, force: bool = False) -> dict:
        today = today or date.today()
        if not force and not self.c.clock().get("is_open"):
            return {"status": "market_closed"}

        acct = self.c.account()
        equity, last = float(acct["equity"]), float(acct["last_equity"])
        day_pl = equity / last - 1 if last else 0.0
        halted = day_pl <= self.cfg["risk"]["daily_loss_halt"]
        if halted:
            self.j.event("halt", day_pl=round(day_pl, 4))

        positions = self.c.positions()
        busy = {o["symbol"] for o in self.c.open_orders()}
        stocks = {p["symbol"]: p for p in positions if p.get("asset_class") == "us_equity"}
        options = {p["symbol"]: p for p in positions if p.get("asset_class") == "us_option"}

        universe = self.cfg["universe"]
        start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        closes = closes_from_bars(self.c.daily_bars(universe, start))

        self._option_exits(options, busy, today)
        state = self.j.state()
        if state.get("last_rebalance") != today.isoformat():
            self._rebalance(stocks, busy, closes, equity, halted)
            state["last_rebalance"] = today.isoformat()
        if self.cfg["options"]["enabled"] and not halted:
            self._option_entries(options, busy, closes, equity, today)

        state["last_run"] = today.isoformat()
        # A dry run must not mark the day as rebalanced, or the real run would skip it.
        if not self.dry:
            self.j.save_state(state)
        bench = closes.get(self.cfg["regime_symbol"], [None])[-1]
        self.j.equity(today.isoformat(), equity, float(acct["cash"]), self.cfg["generation"], bench)
        return {"status": "ok", "equity": equity, "day_pl": day_pl, "halted": halted}

    def _option_exits(self, options: dict, busy: set, today: date) -> None:
        for sym, pos in options.items():
            if sym in busy:
                continue
            reason = opt.should_exit(pos, today, opt.occ_expiration(sym), self.cfg["options"])
            if reason:
                self._close(sym, reason)

    def _rebalance(self, stocks: dict, busy: set, closes: dict, equity: float, halted: bool) -> None:
        p, risk = self.cfg["stocks"], self.cfg["risk"]
        targets = momentum.target_weights(closes, p, self.cfg["regime_symbol"], risk["max_position_pct"])
        self.j.event("targets", weights={s: round(w, 4) for s, w in targets.items()})

        for sym in stocks:
            if sym not in targets and sym not in busy:
                self._close(sym, "dropped_from_targets")

        buys = []
        for sym, w in targets.items():
            if sym in busy or sym not in closes:
                continue
            price = closes[sym][-1]
            have = float(stocks[sym]["market_value"]) if sym in stocks else 0.0
            want = w * equity
            if have and abs(have - want) / want <= p["rebalance_drift"]:
                continue
            qty = math.floor(abs(want - have) / price)
            if qty < 1:
                continue
            if want < have:
                self._order("trim", symbol=sym, qty=str(qty), side="sell", type="market", time_in_force="day")
            else:
                buys.append((sym, qty))
        if halted:
            return
        for sym, qty in buys:
            self._order("rebalance", symbol=sym, qty=str(qty), side="buy", type="market", time_in_force="day")

    def _option_entries(self, options: dict, busy: set, closes: dict, equity: float, today: date) -> None:
        p = self.cfg["options"]
        pending = [s for s in busy if len(s) > 15 and s[-9] in "CP"]
        slots = p["max_positions"] - len(options) - len(pending)
        if slots <= 0:
            return
        held = {option_underlying(s) for s in [*options, *pending]}
        regime = self.cfg["regime_symbol"]
        if regime in closes and not momentum.is_bullish(closes[regime]):
            candidates = [(regime, "put")]
        else:
            candidates = [(s, "call") for s in momentum.rank(closes, self.cfg["stocks"])]
        budget = p["allocation"] * equity / p["max_positions"]

        for und, kind in candidates:
            if slots <= 0:
                break
            if und in held or und not in closes:
                continue
            spot = closes[und][-1]
            contracts = self.c.option_contracts(
                und, type=kind, status="active",
                expiration_date_gte=(today + timedelta(days=p["min_dte"])).isoformat(),
                expiration_date_lte=(today + timedelta(days=p["max_dte"])).isoformat(),
                strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
            if not contracts:
                continue
            snaps = self.c.option_snapshots([c["symbol"] for c in contracts])
            pick = opt.pick_contract(contracts, snaps, spot, p)
            if not pick:
                continue
            contract, price = pick
            qty = math.floor(budget / (price * 100))
            if qty < 1:
                continue
            self._order(f"{kind} on {und}", symbol=contract["symbol"], qty=str(qty), side="buy",
                        type="limit", limit_price=f"{round_option_price(price):.2f}", time_in_force="day")
            held.add(und)
            slots -= 1
