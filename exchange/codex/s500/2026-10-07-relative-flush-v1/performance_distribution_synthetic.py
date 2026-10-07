"""Synthetic-only distribution/aggregator timing; no market files are read."""
from decimal import Decimal
import gc
import hashlib
import json
from pathlib import Path
import resource
import time

import analyze_relative
from return_distribution import compute_distribution


def synthetic_records(n):
    return [{"case_id": f"synthetic-{i:06d}",
             "net_return": str(Decimal((i * 17) % 101 - 50) / 1000)} for i in range(n)]


def benchmark():
    timings = []
    for n, repetitions in [(0, 100), (10, 100), (100, 50), (1000, 10), (5000, 5)]:
        records = synthetic_records(n)
        gc.collect()
        start = time.perf_counter()
        for _ in range(repetitions):
            result = compute_distribution(records)
            assert result["n"] == n
        elapsed = time.perf_counter() - start
        timings.append({"call": "compute_distribution", "synthetic_n": n,
                        "repetitions": repetitions, "seconds": elapsed,
                        "seconds_per_call": elapsed / repetitions})
    data = []
    for i, record in enumerate(synthetic_records(5000)):
        data.append({**record, "date": f"2026-01-{i % 20 + 1:02d}",
                     "signal_status": "signal", "paired_control_status": "complete_three",
                     "risk_set_eligible": True, "flush_detected": True,
                     "matched_excess": "0.001", "entry_minute": 32, "t_min": 15})
    start = time.perf_counter()
    for _ in range(5):
        result = analyze_relative.summarize(data)
        assert result["target_net_distribution"]["n"] == 5000
    elapsed = time.perf_counter() - start
    timings.append({"call": "analyze_relative.summarize", "synthetic_n": 5000,
                    "repetitions": 5, "seconds": elapsed, "seconds_per_call": elapsed / 5})
    root = Path(__file__).parent
    return {
        "scope": "synthetic_only_no_market_rows_read_or_analysis_main_run",
        "timings": timings,
        "maximum_process_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                           for name in ("return_distribution.py", "analyze_relative.py")},
        "interpretation": "microbenchmark_not_a_guarantee_of_production_runtime_or_bucket_memory",
    }


if __name__ == "__main__":
    print(json.dumps(benchmark(), ensure_ascii=False, indent=2))
