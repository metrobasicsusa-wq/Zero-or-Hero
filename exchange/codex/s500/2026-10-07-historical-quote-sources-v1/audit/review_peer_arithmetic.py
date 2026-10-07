"""Recompute the stated hypothetical peer spread example using Decimal."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", required=True)
    args = parser.parse_args()
    stage = Path(args.stage_dir)
    path = stage / "peer-reply.json"
    document = json.loads(path.read_text())
    example = document["quoted_example_not_actual_trade"]
    with localcontext() as context:
        context.prec = 28
        bid, ask = Decimal(example["bid"]), Decimal(example["ask"])
        multiplier, quantity = Decimal(example["assumed_explicit_multiplier"]), Decimal(example["quantity"])
        cost = ask * multiplier * quantity
        liquidation = bid * multiplier * quantity
        loss = cost - liquidation
        recalculated = {
            "spread_over_midpoint": (ask - bid) / ((ask + bid) / 2),
            "purchase_premium": cost,
            "instant_bid_liquidation_value": liquidation,
            "instant_loss_before_fees": loss,
            "instant_loss_as_fraction_of_premium": loss / cost,
        }
        for name, value in recalculated.items():
            assert Decimal(example[name]) == value, name
        assert cost <= Decimal("500")
        assert (quantity + 1) * ask * multiplier > Decimal("500")
    assert document["peer_execution_or_quote_evidence_independently_verified"] is False
    assert document["no_request_to_change_peer_trades_or_workflows"] is True
    assert document["own_order_requests"] == 0
    output = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "status": "arithmetic_and_unverified_labels_pass",
        "recomputed": {name: str(value) for name, value in recalculated.items()},
        "maximum_contracts_at_assumed_ask_before_fees": 45,
        "original_quote_and_actual_fill_verified": False,
        "explanatory_limits": [
            "The numbers are a peer-reported hypothetical example, not independently acquired quotes.",
            "The assumed multiplier and capacity for 45 contracts are not established by this arithmetic.",
            "Two-sided unchanged fill prices, no fees, no latency and no impact are explicit assumptions.",
            "No acceptable spread threshold is established; midpoint limits do not imply fills.",
            "Paper fills, if later observed, remain simulator evidence and do not guarantee live fills.",
        ],
        "sha256": {"peer-reply.json": hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    (stage / "audit" / "peer-arithmetic-review.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"status": output["status"], "recomputed": output["recomputed"]}))


if __name__ == "__main__":
    main()
