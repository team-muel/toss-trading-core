from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import pytest

from test_phase_m6_execution_microstructure import assess, policy as micro_policy, NOW
from test_phase_m6_order_intent_planner import kwargs
from asset_management.execution.planner import plan_order_intents
from asset_management.execution.scheduling import SchedulingPolicy, ScheduleMode, plan_schedule
from asset_management.domain.errors import DataQualityError


def inputs(**changes):
    rule = replace(kwargs()["rules"]["SPY"], minimum_quantity=D('.5'), minimum_notional=D(1))
    values = dict(parent=plan_order_intents(**kwargs())[0], market=assess(), micro_policy=micro_policy(),
                  policy=SchedulingPolicy('schedule@1', ScheduleMode.TWAP_LITE, D('.1'), D('.1'),
                                          D(20), D('.2'), 2, timedelta(seconds=10)),
                  rule=rule, supported_order_types=frozenset({'LIMIT'}), capability_evidence_id='paper:limit:v1',
                  evaluated_at=NOW, transition_start=NOW, valid_until=NOW+timedelta(minutes=5),
                  forecast_valid_until=NOW+timedelta(minutes=4), urgency=D(0), adv_quantity=D(1000),
                  volatility=D('.01'), expected_impact_bps=D(1), window_volumes=(D(100), D(100)), event_times=())
    values.update(changes)
    return values


@pytest.mark.parametrize('mode', [m for m in ScheduleMode if m is not ScheduleMode.EVENT_DEFER])
def test_modes_preserve_quantity_prices_and_deterministic_ids(mode):
    args = inputs()
    args['policy'] = replace(args['policy'], mode=mode)
    if mode in (ScheduleMode.IMMEDIATE, ScheduleMode.PASSIVE_LIMIT):
        args['window_volumes'] = (D(100),)
    result = plan_schedule(**args)
    assert result.state == 'PLANNED'
    assert result.payload() == plan_schedule(**args).payload()
    assert sum(c.quantity for c in result.children) == args['parent'].quantity
    for child in result.children:
        assert child.limit_price <= args['parent'].limit_price
        assert child.quantity <= child.volume_cap * result.participation
        assert NOW <= child.not_before < child.expires_at <= args['forecast_valid_until']


@pytest.mark.parametrize('changes,reason', [
    ({'supported_order_types': frozenset()}, 'BROKER_ORDER_TYPE_UNSUPPORTED'),
    ({'window_volumes': (D(1), D(1))}, 'PARTICIPATION_CAPACITY_INSUFFICIENT'),
    ({'window_volumes': ()}, 'SCHEDULE_INPUT_INVALID'),
    ({'event_times': (NOW+timedelta(seconds=30),)}, 'EVENT_DEFER'),
    ({'valid_until': NOW}, 'EXECUTION_WINDOW_UNAVAILABLE'),
    ({'adv_quantity': D(1)}, 'ORDER_ADV_LIMIT'),
    ({'expected_impact_bps': D(99)}, 'MARKET_COST_LIMIT'),
    ({'evaluated_at': NOW+timedelta(minutes=1)}, 'MICROSTRUCTURE:QUOTE_STALE_OR_UNAVAILABLE'),
])
def test_failure_has_no_children(changes, reason):
    if reason == 'SCHEDULE_INPUT_INVALID':
        with pytest.raises(DataQualityError, match=reason):
            plan_schedule(**inputs(**changes))
    else:
        result = plan_schedule(**inputs(**changes))
        assert result.state == 'NO_TRADE' and result.reason == reason and not result.children


def test_pov_uses_per_window_volume_and_urgency_shortens_window():
    args = inputs()
    args['policy'] = replace(args['policy'], mode=ScheduleMode.POV_LITE)
    args['window_volumes'] = (D(5), D(15))
    result = plan_schedule(**args)
    assert [c.quantity for c in result.children] == [D('.5'), D('1.5')]
    urgent = plan_schedule(**(args | {'urgency': D(1), 'window_volumes': (D(100), D(100))}))
    assert urgent.children[-1].expires_at < result.children[-1].expires_at


def test_urgency_cannot_reuse_full_window_volume():
    result = plan_schedule(**inputs(urgency=D(1), window_volumes=(D(10), D(10))))
    assert result.reason == 'PARTICIPATION_CAPACITY_INSUFFICIENT' and not result.children


def test_event_mode_and_subminimum_children_defer():
    args = inputs()
    event = plan_schedule(**(args | {'policy': replace(args['policy'], mode=ScheduleMode.EVENT_DEFER)}))
    assert event.reason == 'EVENT_DEFER'
    small = plan_schedule(**(args | {'rule': replace(args['rule'], minimum_quantity=D(2))}))
    assert small.reason == 'CHILD_BELOW_MINIMUM'


def test_schema_matches_payload():
    schema = json.loads((Path(__file__).parents[1]/'schemas/execution_schedule.schema.json').read_text())
    assert set(schema['required']) == set(plan_schedule(**inputs()).payload())
