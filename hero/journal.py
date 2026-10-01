"""Append-only trade log, equity curve and small run state, all committed to git."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


class Journal:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / "state.json"

    def state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    def event(self, kind: str, **data) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **data}
        with open(self.root / "trades.jsonl", "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def equity(self, day: str, equity: float, cash: float, generation: int) -> None:
        path = self.root / "equity.csv"
        rows = list(csv.reader(open(path))) if path.exists() else [["date", "equity", "cash", "generation"]]
        row = [day, f"{equity:.2f}", f"{cash:.2f}", str(generation)]
        # One row per day: later runs overwrite the same day's snapshot.
        if rows[-1][0] == day:
            rows[-1] = row
        else:
            rows.append(row)
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)

    def equity_curve(self) -> list[float]:
        path = self.root / "equity.csv"
        if not path.exists():
            return []
        return [float(r["equity"]) for r in csv.DictReader(open(path))]
