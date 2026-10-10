"""Public synthetic provider examples, not account or operational evidence."""
import json
from pathlib import Path

import pytest
from toss_trading.contracts import toss

FIXTURE = json.loads((Path(__file__).parent / "fixtures/toss_1_2_24_examples.json").read_text(encoding="utf-8"))
PARSERS = {
    "/api/v1/accounts": toss.require_accounts,
    "/api/v1/holdings": toss.holdings_items,
    "/api/v1/orders": lambda body: toss.orders_page(body, status="CLOSED"),
    "/api/v1/orders/{orderId}": toss.order_detail,
    "/api/v1/buying-power": toss.require_buying_power,
    "/api/v1/sellable-quantity": toss.require_sellable_quantity,
    "/api/v1/commissions": toss.commission_rate_items,
}


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["endpoint"] + ":" + case["name"])
def test_current_documented_example_and_missing_envelope(case):
    parser = PARSERS[case["endpoint"]]
    parser(case["body"])
    with pytest.raises(toss.TossContractError):
        parser({"data": case["body"]["result"]})


def test_documented_examples_cover_every_account_parser():
    assert {case["endpoint"] for case in FIXTURE["cases"]} == set(PARSERS)
    assert FIXTURE["scope"] == "DOCUMENTED_SYNTHETIC_EXAMPLES_ONLY"
