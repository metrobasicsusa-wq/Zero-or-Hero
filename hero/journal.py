"""Append-only trade log, equity curve and small run state, all committed to git."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


EQUITY_HEADER = ["date", "equity", "cash", "generation", "benchmark", "as_of"]

# The repo is public: only these broker fields are ever written to disk (no account
# numbers, account/order/asset ids).
ACCOUNT_FIELDS = ("equity", "last_equity", "cash", "buying_power", "long_market_value", "portfolio_value")
POSITION_FIELDS = ("symbol", "asset_class", "side", "qty", "avg_entry_price", "current_price", "lastday_price",
                   "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc", "unrealized_intraday_pl")
ORDER_FIELDS = ("symbol", "asset_class", "side", "qty", "filled_qty", "filled_avg_price", "type", "limit_price",
                "stop_price", "time_in_force", "status", "submitted_at", "filled_at", "protective_stop")
FILL_FIELDS = ("transaction_time", "symbol", "side", "qty", "price", "type", "cum_qty", "leaves_qty")


def _pick(obj: dict, fields: tuple) -> dict:
    return {k: obj.get(k) for k in fields}


def sanitize(snapshot: dict) -> dict:
    return {
        "account": _pick(snapshot.get("account") or {}, ACCOUNT_FIELDS),
        "positions": [_pick(p, POSITION_FIELDS) for p in snapshot.get("positions") or []],
        "open_orders": [_pick({**o, "protective_stop": (o.get("client_order_id") or "").startswith("hero-stop-")},
                              ORDER_FIELDS) for o in snapshot.get("open_orders") or []],
    }


def execution_key(activity_id: str) -> str:
    """Stable, non-reversible id for a broker fill (the raw id is never published)."""
    return hashlib.sha256(activity_id.encode()).hexdigest()[:16]


class Journal:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / "state.json"

    def state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    def alerts(self) -> dict:
        """Intraday alert bookkeeping (de-duplication only), kept apart from the trading state so
        that a dry run can update it without touching the state the real run depends on."""
        path = self.root / "alerts.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def save_alerts(self, book: dict) -> None:
        (self.root / "alerts.json").write_text(json.dumps(book, indent=1, ensure_ascii=False, sort_keys=True) + "\n")

    def event(self, kind: str, **data) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **data}
        with open(self.root / "trades.jsonl", "a") as f:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")

    def equity(self, day: str, equity: float, cash: float, generation: int,
               benchmark: float | None = None) -> None:
        path = self.root / "equity.csv"
        rows = list(csv.reader(open(path))) if path.exists() else [EQUITY_HEADER]
        rows[0] = EQUITY_HEADER
        as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = [day, f"{equity:.2f}", f"{cash:.2f}", str(generation), f"{benchmark:.2f}" if benchmark else "", as_of]
        # One row per day: later runs overwrite the same day's snapshot.
        if rows[-1][0] == day:
            rows[-1] = row
        else:
            rows.append(row)
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)

    def snapshot(self, data: dict) -> None:
        data = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **sanitize(data)}
        (self.root / "snapshot.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def record_fills(self, activities: list[dict]) -> int:
        """Merge broker FILL activities into fills.jsonl, keyed by execution_key. Returns new count."""
        path = self.root / "fills.jsonl"
        known = {}
        if path.exists():
            for line in open(path):
                f = json.loads(line)
                known[f["execution_key"]] = f
        added = 0
        for a in activities:
            key = execution_key(a["id"])
            added += key not in known
            order_key = execution_key(a["order_id"]) if a.get("order_id") else None
            known[key] = {"execution_key": key, "order_key": order_key, **_pick(a, FILL_FIELDS)}
        rows = sorted(known.values(), key=lambda f: (f["transaction_time"], f["execution_key"]))
        path.write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in rows))
        return added

    def fills(self, day: str) -> list[dict]:
        """Fills whose transaction time falls on the given US/Eastern trading day."""
        path = self.root / "fills.jsonl"
        if not path.exists():
            return []
        out = []
        for line in open(path):
            f = json.loads(line)
            t = datetime.fromisoformat(f["transaction_time"].replace("Z", "+00:00")).astimezone(ET)
            if t.date().isoformat() == day:
                out.append(f)
        return out

    def equity_curve(self) -> list[float]:
        path = self.root / "equity.csv"
        if not path.exists():
            return []
        return [float(r["equity"]) for r in csv.DictReader(open(path))]
