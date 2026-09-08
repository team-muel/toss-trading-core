from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from asset_management.decisions import RiskGovernor, RiskGovernorPolicy, RiskInputs
from asset_management.decisions.governor import SOFT_REDUCTIONS, target_weight_hash
from asset_management.domain.enums import OrderState
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.execution import (ExecutableQuote, InstrumentOrderRule, IntentSide, OpenOrderExposure,
                                        OrderIntent, PlannedOrderIntent, TargetWeight, net_open_order_quantities,
                                        plan_order_intents)


NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
D = Decimal
TARGETS = {"SPY": D(".5"), "CASH": D(".5")}
TARGET_HASH = target_weight_hash(TARGETS)


def source_intent():
    policy = RiskGovernorPolicy("risk@1", {reason: D(".75") for _, reason in SOFT_REDUCTIONS})
    decision = RiskGovernor(policy).decide(RiskInputs(
        runtime_run_id="run:1", portfolio_target_id="target:1", portfolio_target_hash=TARGET_HASH,
        policy_version="risk@1", as_of_utc=NOW.isoformat(), evidence_ids=("account:1", "risk:1"),
    ))
    authorization, approved_targets = decision.authorize_target(TARGETS, cash_instrument_id="CASH")
    return OrderIntent("run:1", "risk@1", "target:1", TARGET_HASH, authorization,
                       (TargetWeight("SPY", approved_targets["SPY"], D(".1")),
                        TargetWeight("CASH", approved_targets["CASH"], D(".9"))),
                       ("rebalance",))


def kwargs(**changes):
    values = dict(source=source_intent(), nav=D(1000),
                  quotes={"SPY": ExecutableQuote(D("100.03"), NOW, NOW + timedelta(minutes=1), True),
                          "CASH": ExecutableQuote(D(1), NOW, NOW + timedelta(minutes=1), True)},
                  current_quantities={"SPY": D(".5"), "CASH": D(500)},
                  open_order_quantities={"SPY": D(2)},
                  rules={"SPY": InstrumentOrderRule(D(".5"), D(".05"), D(1), D(100), D(".00011"), D(".01")),
                         "CASH": InstrumentOrderRule(D(1), D(".01"), D(1), D(1), D(0), D(".01"))},
                  evaluated_at=NOW)
    values.update(changes)
    return values


def test_netting_rounding_fee_and_client_id_are_deterministic():
    first = plan_order_intents(**kwargs())
    second = plan_order_intents(**kwargs())
    assert first == second and len(first) == 1
    planned = first[0]
    assert (planned.side, planned.quantity, planned.notional_amount, planned.limit_price, planned.expected_fee) == (
        IntentSide.BUY, D(2), D("200.00"), D("100.00"), D(".03"))
    assert planned.order_intent_id.startswith("intent-") and planned.client_order_id.startswith("client-")
    assert planned.payload()["notional_amount"] == "200.000"


@pytest.mark.parametrize("changes, reason", [
    ({"quotes": {"SPY": ExecutableQuote(D(100), NOW, NOW + timedelta(minutes=1), False)}}, "ORDER_SESSION_CLOSED"),
    ({"quotes": {"SPY": ExecutableQuote(D(100), NOW - timedelta(minutes=2), NOW - timedelta(minutes=1), True)}}, "ORDER_QUOTE_STALE"),
    ({"quotes": {"SPY": ExecutableQuote(D(100), NOW + timedelta(seconds=1), NOW + timedelta(minutes=1), True)}}, "ORDER_QUOTE_FROM_FUTURE"),
    ({"current_quantities": {"SPY": D("2.5"), "CASH": D(500)}}, ""),
])
def test_closed_stale_or_fully_netted_exposure_cannot_create_an_order(changes, reason):
    if reason:
        with pytest.raises(DataQualityError, match=reason):
            plan_order_intents(**kwargs(**changes))
    else:
        assert plan_order_intents(**kwargs(**changes)) == ()


def test_missing_rule_or_unapproved_risk_binding_fails_closed():
    with pytest.raises(DataQualityError, match="ORDER_PLANNER_INPUT_INVALID"):
        plan_order_intents(**kwargs(rules={}))
    source = source_intent()
    approved = source.risk_authorization
    with pytest.raises(InvariantViolation, match="different portfolio target"):
        OrderIntent("run:1", "risk@1", "other", source.portfolio_target_hash, approved,
                    source.target_weights, ())


def test_order_intent_requires_named_unique_target_weights():
    with pytest.raises(InvariantViolation, match="named fractions"):
        TargetWeight("", D(".5"), D(".1"))
    source = source_intent()
    with pytest.raises(InvariantViolation, match="requires target weights"):
        OrderIntent(source.run_id, source.policy_version, source.portfolio_target_id,
                    source.portfolio_target_hash, source.risk_authorization,
                    (TargetWeight("SPY", D(".5"), D(".1")), TargetWeight("SPY", D(".4"), D(".1"))), ())


def test_tampered_plan_or_sub_minimum_delta_is_rejected_or_skipped():
    planned = plan_order_intents(**kwargs())[0]
    with pytest.raises(DataQualityError, match="PLANNED_ORDER_INTENT_INVALID"):
        PlannedOrderIntent(planned.order_intent_id, planned.client_order_id, planned.instrument_id, planned.side,
                           planned.quantity, D("199.99"), planned.limit_price, planned.expected_fee,
                           planned.quote_observed_at, planned.source_order_intent, planned.content_hash)
    assert plan_order_intents(**kwargs(current_quantities={"SPY": D(2), "CASH": D(500)})) == ()


def test_only_remaining_open_or_partial_fill_quantity_is_netted():
    exposure = OpenOrderExposure("broker:1", "SPY", IntentSide.BUY, D(3), D(1), OrderState.PARTIALLY_FILLED)
    assert net_open_order_quantities((exposure,)) == {"SPY": D(2)}
    plan = plan_order_intents(**kwargs(open_order_quantities=net_open_order_quantities((exposure,))))
    assert plan[0].quantity == D(2)


@pytest.mark.parametrize("exposures, reason", [
    ((OpenOrderExposure("broker:1", "SPY", IntentSide.BUY, D(2), D(0), OrderState.OPEN),
      OpenOrderExposure("broker:2", "SPY", IntentSide.SELL, D(2), D(0), OrderState.OPEN)),
     "OPEN_ORDER_EXPOSURE_CONFLICT"),
    ((OpenOrderExposure("broker:1", "SPY", IntentSide.BUY, D(2), D(0), OrderState.OPEN),
      OpenOrderExposure("broker:1", "QQQ", IntentSide.BUY, D(2), D(0), OrderState.OPEN)),
     "OPEN_ORDER_EXPOSURE_INVALID"),
])
def test_ambiguous_open_order_exposure_fails_closed(exposures, reason):
    with pytest.raises(DataQualityError, match=reason):
        net_open_order_quantities(exposures)


def test_unknown_order_state_cannot_be_converted_to_open_exposure():
    with pytest.raises(DataQualityError, match="OPEN_ORDER_EXPOSURE_INVALID"):
        OpenOrderExposure("broker:1", "SPY", IntentSide.BUY, D(2), D(0), OrderState.UNKNOWN)


def test_plan_payload_matches_the_published_json_contract():
    planned = plan_order_intents(**kwargs())[0]
    schema_path = Path(__file__).parents[1] / "schemas/order_intent_plan.schema.json"
    schema = json.loads(schema_path.read_text())
    assert set(schema["required"]) == set(planned.payload())
    assert schema["properties"]["side"]["enum"] == ["BUY", "SELL"]
