"""Controlled fixture for the published Nov 23 intraday research method.
Give the path to the pinned zerotrader/intraday.py. All prices and rows are synthetic.
"""
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import runpy
import sys


def run(path):
    expected = "7f0492f70bace1d3dd8618edd61a780b48f1f259516f7bdb08f3d3e390085bdf"
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Research source hash differs")
    engine = runpy.run_path(str(path))
    days = [date(2026, 11, 23)]
    while len(days) < 153:
        day = days[-1] - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        days.append(day)
    days.reverse()
    bars = []
    for day in days:
        start = datetime.combine(day, time(9, 30), tzinfo=engine["NY"])
        for idx in range(26):
            price, close, volume = 100, 100, 10
            if day == days[-1] and idx == 2:
                price, close, volume = 100, 101, 100
            if day == days[-1] and idx >= 3:
                price, close = 101, 101.1 if idx == 3 else 102
            bars.append({"t": (start + idx * engine["BAR"]).isoformat(), "o": price,
                         "l": min(price, close), "h": max(price, close), "c": close, "v": volume})
    cases = {"previous_sessions_only": bars[:-26], "through_1030": bars[:-22], "full_final_day": bars}
    out = {}
    for name, rows in cases.items():
        data = {"TEST": rows}
        result = engine["research"](data)
        trades = engine["_simulate"](engine["_prepare"](data), engine["DEFAULT_PARAMS"])
        out[name] = {"sessions": result["validation"]["sessions"], "splits": result["validation"]["split_dates"],
                     "all_synthetic_rankings": result["rankings"],
                     "trades": [{k: t[k] for k in ("entry_time", "exit_time", "exit_reason", "net_return")} for t in trades]}
    assert not out["previous_sessions_only"]["trades"]
    assert out["through_1030"]["trades"][0]["exit_time"].endswith("10:30:00-05:00")
    assert out["full_final_day"]["trades"][0]["exit_time"].endswith("15:45:00-05:00")
    return out


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1])), indent=2))
