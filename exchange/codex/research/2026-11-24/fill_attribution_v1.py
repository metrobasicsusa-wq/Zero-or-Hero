"""Reproduce the 2026-11-24 FILL attribution from the 27 pinned public daily JSONs.
Run with Python 3.11+ and a directory containing those files. Totals are USD.
Scope is the fixed equity-only sample with empty starting and ending inventory.
"""
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys

FILES = {
    "2026-10-01.json": "d86479847e5229c5522bfade148c4eedb1adf7ff",
    "2026-10-02.json": "ec5bdc3b88603be359966a6738ce0282ec848b21",
    "2026-10-05.json": "1a02b1128b0aae983450907706c2eded4590e42d",
    "2026-10-06.json": "3fefc339222cd25b463c0afb92ac1497070d2b82",
    "2026-10-07.json": "57dd5c67f0595108e54736419a9b02ce49622815",
    "2026-10-08.json": "84977c4b44e3d776cf26675709505083dee5c118",
    "2026-10-09.json": "b5fe5de1db6c7117732da535d48592b8c587d75d",
    "2026-10-12.json": "ae78b97c0935d7b197830b4c0ad627c1f6baaf93",
    "2026-10-13.json": "321a1a15171103a8c82e79077942e07417811553",
    "2026-10-14.json": "1b931125a6fe988f7058bc74e3bd4dcba1600b2a",
    "2026-10-15.json": "9034ac7ea2fee988da13e8143784303764bfe1a3",
    "2026-10-16.json": "553af37d73be868421bf8d25b1b8f473770669b8",
    "2026-10-19.json": "c1c197672f6bffd86f980e1443670151eab73a7d",
    "2026-10-20.json": "8f19ceab72356d6051ad1e45e599b2401695bc79",
    "2026-10-21.json": "62cc57cb03bc32da4a77fdefa0ae295a4e0a3204",
    "2026-10-22.json": "73525a1d07880a5f98689e6b39a8df2b479f5dda",
    "2026-10-23.json": "38df95cfeb55c5caaa7dedbdfac40788b910fd62",
    "2026-10-26.json": "a65bd28c1558bcc8140eb12b1da2e8f66b47adf6",
    "2026-10-27.json": "8a8c8e9044d9c7d8ad022794769a59b7b1690958",
    "2026-10-28.json": "521773487fd9273463fa0cd7fe5959f887444107",
    "2026-10-29.json": "d93fcbc000d4d7e313bb655b6c5fc94e0aeb47d8",
    "2026-10-30.json": "94f0c8dc8e0f816553704f3b2081e68df1f026ab",
    "2026-11-02.json": "4b943c5b3e6fb9a1428f9bbac71a317a4e295246",
    "2026-11-03.json": "b3f726f60adcfa07415af55f88b2a113ce554147",
    "2026-11-04.json": "29d027e043ee37fd3629bfdfc534de77bbd16165",
    "2026-11-05.json": "598a125c845dd4a1edd0dd771603703214127aa9",
    "2026-11-06.json": "ca519b67d0733d02f2307915d8ae00ca41f01250"
}
ZERO = D(0)
CENT = D("0.01")


def dollars(amount):
    return float(amount.quantize(CENT))


def compute(directory):
    fills = []
    ids = set()
    sessions = defaultdict(list)
    for name, expected in FILES.items():
        raw = (directory / name).read_bytes()
        prefix = b"blob " + str(len(raw)).encode() + b"\0"
        if hashlib.sha1(prefix + raw).hexdigest() != expected:
            raise ValueError(f"Unexpected file revision: {name}")
        day = date.fromisoformat(name[:-5])
        monday = (day - timedelta(days=day.weekday())).isoformat()
        sessions[monday].append(day.isoformat())
        audit = json.loads(raw, parse_float=D)
        assert audit["trading_date"] == day.isoformat()
        assert audit["fill_count"] == len(audit["fills"])
        for row in audit["fills"]:
            key = row["execution_key"]
            assert key not in ids
            ids.add(key)
            assert row["quantity_unit"] == "shares" and int(row.get("multiplier", 1)) == 1
            assert row["sleeve"] in ("swing", "intraday") and row["side"] in ("buy", "sell")
            fills.append((datetime.fromisoformat(row["at"]), monday, row))

    lots = defaultdict(deque)
    opened = {}
    realized = []
    flows = ZERO
    for at, week, row in sorted(fills, key=lambda record: record[0]):
        sleeve, symbol = row["sleeve"], row["symbol"]
        book = lots[sleeve, symbol]
        quantity, price = D(str(row["quantity"])), D(str(row["price_usd"]))
        assert quantity > 0 and price > 0
        if row["side"] == "buy":
            if not book:
                opened[sleeve, symbol] = at.isoformat()
            book.append([quantity, price])
            flows -= quantity * price
            continue
        flows += quantity * price
        while quantity:
            if not book:
                raise ValueError("Sale lacks matched opening inventory")
            available, basis = book[0]
            matched = min(available, quantity)
            realized.append((week, sleeve, symbol, opened[sleeve, symbol],
                             (price - basis) * matched, (price + basis) * matched))
            quantity -= matched
            book[0][0] -= matched
            if not book[0][0]:
                book.popleft()
    assert not any(lots.values())
    assert flows == sum(row[4] for row in realized)

    def summary(records):
        tickers, sleeves, episodes = defaultdict(D), defaultdict(D), defaultdict(D)
        turnover = ZERO
        for _, sleeve, symbol, start, pnl, traded in records:
            tickers[symbol] += pnl
            sleeves[sleeve] += pnl
            episodes[sleeve, symbol, start] += pnl
            turnover += traded
        gross = sum(tickers.values(), ZERO)
        best = max(episodes, key=episodes.get) if episodes else None
        removed = max(ZERO, episodes[best]) if best else ZERO
        return {
            "gross_usd": dollars(gross),
            "by_sleeve_usd": {k: dollars(sleeves[k]) for k in ("swing", "intraday", "options")},
            "by_symbol_usd": {k: dollars(tickers[k]) for k in sorted(tickers, key=tickers.get, reverse=True)},
            "trade_contributions": [
                {"sleeve": k[0], "symbol": k[1], "opened_at": k[2], "realized_usd": dollars(episodes[k])}
                for k in sorted(episodes, key=episodes.get, reverse=True)
            ],
            "gross_ex_largest_winning_trade_usd": dollars(gross - removed),
            "gross_ex_largest_winning_symbol_usd": dollars(gross - max([ZERO, *tickers.values()])),
            "matched_buy_plus_sell_notional_usd": dollars(turnover),
            "cost_scenarios_usd": {str(bps): dollars(gross - turnover * D(bps) / 10000)
                                   for bps in (0, 1, 5, 10)},
            "break_even_cost_bps_per_side": float((gross * 10000 / turnover).quantize(D("0.0001"))) if turnover else None,
            "actual_after_fee_pnl_usd": None,
        }

    return {
        "fill_count": len(fills),
        "full_period": summary(realized),
        "intraday_all": summary([r for r in realized if r[1] == "intraday"]),
        "intraday_ex_amzn": summary([r for r in realized if r[1] == "intraday" and r[2] != "AMZN"]),
        "weekly": [{"week_of": w, "audited_session_dates": days,
                    "intraday_ex_amzn": summary([r for r in realized if r[0] == w and r[1] == "intraday" and r[2] != "AMZN"]),
                    **summary([r for r in realized if r[0] == w])} for w, days in sessions.items()]
    }


if __name__ == "__main__":
    print(json.dumps(compute(Path(sys.argv[1])), ensure_ascii=False, indent=2))
