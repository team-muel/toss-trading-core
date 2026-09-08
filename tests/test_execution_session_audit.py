from dataclasses import replace
from datetime import timedelta
import pytest
from test_phase_m6_execution_microstructure import assess, quote, calendar, policy, NOW
from test_phase_m6_order_intent_planner import kwargs
from asset_management.execution import to_executable_quote, plan_order_intents
from asset_management.domain.enums import DecisionAction
from asset_management.domain.errors import DataQualityError


def test_session_close_blocks_a_still_fresh_quote():
    close = calendar().regular_close_at
    result = assess(arrival_quote=quote(observed_at=close-timedelta(seconds=2),
                                        available_at=close-timedelta(seconds=1)), evaluated_at=close)
    assert result.action is DecisionAction.DEFER
    assert 'EXECUTION_SESSION_CHANGED' in result.reason_codes


def test_bridge_rejects_modified_assessment_and_bounds_session_expiry():
    with pytest.raises(DataQualityError):
        to_executable_quote(assessment=replace(assess(), executable_price_reference=quote().bid), policy=policy())
    close = calendar().regular_close_at
    result = assess(arrival_quote=quote(observed_at=close-timedelta(seconds=2),
                                        available_at=close-timedelta(seconds=1)),
                    evaluated_at=close-timedelta(seconds=1))
    assert to_executable_quote(assessment=result, policy=policy()).valid_until == close


def test_planner_expiry_is_exclusive():
    args = kwargs()
    args['evaluated_at'] = args['quotes']['SPY'].valid_until
    with pytest.raises(DataQualityError, match='ORDER_QUOTE_STALE'):
        plan_order_intents(**args)
