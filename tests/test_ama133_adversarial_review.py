"""Adversarial regressions for the user-authorized AMA-133 quota fallback.

These tests assert semantic boundaries, not that a review thread was resolved.
Broker transports remain synthetic; no live API or credentials are used.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from asset_management.decisions import DecisionState, RiskGovernor
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import DecayProfile, SignalValidity
from asset_management.execution.intents import OrderIntent, TargetWeight
from asset_management.execution.submission import SubmissionJournal
from asset_management.decisions.governor import target_weight_hash, ReasonCode
from asset_management.signals import ForecastCombinationRequest, ForecastCombinationRegistry, ForecastCombiner
from asset_management.data.immutable import ImmutableDatasetStore
from test_phase17_risk_governor import policy, inputs, DEFAULT_TARGET
from test_durable_submission import authority, args, proof, _append_authority_snapshot, NOW
from test_phase_m35_forecast_combination import source, request, parameters, UNIVERSE


def test_duck_typed_risk_approval_cannot_create_an_order_intent():
    target = {'SPY': D('.9'), 'CASH': D('.1')}
    target_hash = target_weight_hash(target)
    forged = SimpleNamespace(state=DecisionState.ALLOW, runtime_run_id='run',
                             policy_version='risk', portfolio_target_id='target',
                             portfolio_target_hash=target_hash, approved_target_hash=target_hash,
                             risk_decision_id='invented', exposure_multiplier=D(1))
    with pytest.raises(InvariantViolation, match='approved risk decision'):
        OrderIntent('run', 'risk', 'target', target_hash, forged,
                    tuple(TargetWeight(k, v, D(0)) for k, v in target.items()), ())


def test_cash_scope_cannot_be_relabelled_after_risk_approval():
    issuer = RiskGovernor(policy())
    decision = issuer.decide(inputs(spread_high=True))
    token = issuer.authorize(decision)
    with pytest.raises(InvariantViolation, match='cash instrument'):
        token.bind_target(DEFAULT_TARGET, cash_instrument_id='SPY')


def test_validated_policy_multipliers_are_immutable():
    value = policy()
    with pytest.raises(TypeError):
        value.reduction_multipliers[ReasonCode.SPREAD_HIGH] = D(2)


@pytest.mark.parametrize('advance', [0, 10])
def test_recovery_cannot_overwrite_a_new_same_state_ambiguity(tmp_path, advance):
    ledger = authority()
    path = tmp_path / 'submissions.db'
    journal = SubmissionJournal(path, authority_conn=ledger)
    other = SubmissionJournal(path)
    client = journal.submit_once(**args(), submit=lambda _: {}).client_order_id
    def race(query):
        other.mark_ambiguous(client, operation='CANCEL', at=NOW + timedelta(seconds=advance))
        return proof(query)
    try:
        result = journal.recover(client, lookup=race)
        assert result.state == 'UNKNOWN_BROKER_STATE'
        assert result.reason == 'RECOVERY_STATE_CHANGED_REQUERY'
    finally:
        other.close(); journal.close(); ledger.close()


def test_ambiguity_timestamp_cannot_move_backwards(tmp_path):
    journal = SubmissionJournal(tmp_path / 'submissions.db')
    client = journal.submit_once(**args(), submit=lambda _: {}).client_order_id
    try:
        journal.mark_ambiguous(client, operation='CANCEL', at=NOW + timedelta(seconds=20))
        with pytest.raises(DataQualityError, match='RECOVERY_OPERATION_TIME_INVALID'):
            journal.mark_ambiguous(client, operation='REPLACE', at=NOW + timedelta(seconds=5))
    finally:
        journal.close()


def test_late_arrival_of_pre_cancel_request_cannot_clear_recovery(tmp_path):
    ledger = authority()
    journal = SubmissionJournal(tmp_path / 'submissions.db', authority_conn=ledger)
    client = journal.submit_once(**args(), submit=lambda _: {}).client_order_id
    cutoff = NOW + timedelta(seconds=10)
    journal.mark_ambiguous(client, operation='CANCEL', at=cutoff)
    fresh = _append_authority_snapshot(ledger, 'OPEN', cutoff + timedelta(seconds=1),
                                       sequence=2, suffix='2')
    # A read started before cancel can legitimately return after it. Arrival
    # time alone cannot establish that the broker observed the new operation.
    ledger.execute('UPDATE am_raw_api_response SET requested_at_utc=? WHERE raw_response_id=?',
                   (NOW.isoformat(), fresh['source_response_id']))
    ledger.commit()
    try:
        assert journal.recover(client, lookup=lambda q: proof(q, **fresh)).state == 'UNKNOWN_BROKER_STATE'
    finally:
        journal.close(); ledger.close()


def test_impossible_forecast_correlation_is_rejected():
    sources = tuple(source(i, f'signal-{i}', UNIVERSE) for i in range(1, 4))
    covariance = tuple(tuple(D(1) if i == j else D(0) for j in range(3)) for i in range(3))
    correlation = ((D(1), D('.9'), D('.9')), (D('.9'), D(1), D('-.9')), (D('.9'), D('-.9'), D(1)))
    with pytest.raises(InvariantViolation, match='CORRELATION_NOT_PSD'):
        ForecastCombinationRequest(sources, covariance, correlation, request().evaluated_at)


@pytest.mark.parametrize('profile', list(DecayProfile))
def test_validity_end_is_exclusive_for_every_decay_profile(profile):
    end = NOW + timedelta(days=1)
    validity = SignalValidity(21, 21, end, profile,
                              3600 if profile is DecayProfile.EXPONENTIAL else None)
    assert validity.effective_weight(produced_at=NOW, evaluated_at=end) == 0


def test_combiner_applies_validity_decay_without_discounting_execution_cost(tmp_path):
    value = request()
    combiner = ForecastCombiner(ImmutableDatasetStore(tmp_path), ForecastCombinationRegistry([parameters()]))
    first = combiner.combine(replace(value, evaluated_at=value.sources[0].as_of),
                             combination_id='forecast-overlay', version='1')
    later = combiner.combine(value, combination_id='forecast-overlay', version='1')
    assert first.status == later.status == 'READY'
    for instrument in UNIVERSE:
        initial, current = first.report['components'][instrument], later.report['components'][instrument]
        assert D(current['gross_point_estimate']) == D(initial['gross_point_estimate']) * D('.5')
        assert D(current['uncertainty']) == D(initial['uncertainty'])
        assert D(current['net_point_estimate']) == D(current['gross_point_estimate']) - D(later.report['expected_implementation_cost'])
    assert first.report['expected_implementation_cost'] == later.report['expected_implementation_cost']
    assert first.report['combined_forecast_id'] != later.report['combined_forecast_id']
    assert later.report['forecast_validity_decay']['weight'] == '0.5'
    assert later.report['forecast_validity_decay']['stage'] == 'FORECAST_VALIDITY'
    assert later.report['evaluated_at'] != first.report['evaluated_at']
    schema = json.loads((Path(__file__).parents[1] / 'schemas/forecast_combination.schema.json').read_text())
    Draft202012Validator(schema).validate(dict(later.report))


def test_custom_cash_scope_is_supported_when_the_policy_authorizes_it():
    target = {'SPY': D('.8'), 'USD': D('.2')}
    issuer = RiskGovernor(replace(policy(), cash_instrument_id='USD'))
    decision = issuer.decide(inputs(spread_high=True, portfolio_target_hash=target_weight_hash(target)))
    token, weights = issuer.authorize_target(decision, target, cash_instrument_id='USD')
    assert weights == {'SPY': D('.40'), 'USD': D('.60')}
    assert token.cash_instrument_id == 'USD'


def test_policy_replacement_cannot_reuse_a_previously_issued_decision():
    issuer = RiskGovernor(policy())
    decision = issuer.decide(inputs(spread_high=True))
    issuer.policy = replace(policy(), cash_instrument_id='SPY')
    with pytest.raises(InvariantViolation, match='policy is not current'):
        issuer.authorize(decision)


def test_same_version_different_policy_content_changes_decision_identity():
    first = RiskGovernor(policy()).decide(inputs())
    second = RiskGovernor(replace(policy(), cash_instrument_id='USD')).decide(inputs())
    assert first.content_hash != second.content_hash


def test_intent_detaches_caller_owned_target_list():
    issuer = RiskGovernor(policy())
    decision = issuer.decide(inputs())
    token, weights = issuer.authorize_target(decision, DEFAULT_TARGET, cash_instrument_id='CASH')
    supplied = [TargetWeight(k, v, D(0)) for k, v in weights.items()]
    intent = OrderIntent(decision.runtime_run_id, decision.policy_version, decision.portfolio_target_id,
                         decision.portfolio_target_hash, token, supplied, ())
    supplied[0] = TargetWeight('SPY', D(1), D(0))
    assert target_weight_hash({item.instrument_id: item.target for item in intent.target_weights}) == token.approved_target_hash


def test_forecast_request_detaches_caller_owned_matrices():
    value = request()
    covariance = [list(row) for row in value.covariance]
    correlation = [list(row) for row in value.correlation]
    frozen = replace(value, covariance=covariance, correlation=correlation)
    covariance[0][1] = D(100)
    correlation[0][1] = D(-100)
    assert frozen.covariance == value.covariance
    assert frozen.correlation == value.correlation


@pytest.mark.parametrize('kind', ['state', 'snapshot'])
def test_recovery_checks_all_persisted_observation_times(tmp_path, kind):
    ledger = authority()
    journal = SubmissionJournal(tmp_path / 'submissions.db', authority_conn=ledger)
    client = journal.submit_once(**args(), submit=lambda _: {}).client_order_id
    cutoff = NOW + timedelta(seconds=10)
    journal.mark_ambiguous(client, operation='REPLACE', at=cutoff)
    fresh = _append_authority_snapshot(ledger, 'OPEN', cutoff + timedelta(seconds=1), sequence=2, suffix='2')
    if kind == 'state':
        ledger.execute('UPDATE am_order_state_event SET observed_at_utc=? WHERE sequence_no=2', (NOW.isoformat(),))
    else:
        ledger.execute('UPDATE am_account_snapshot SET observed_at_utc=? WHERE account_snapshot_id=?',
                       (NOW.isoformat(), fresh['snapshot_id']))
    ledger.commit()
    try:
        assert journal.recover(client, lookup=lambda q: proof(q, **fresh)).state == 'UNKNOWN_BROKER_STATE'
    finally:
        journal.close(); ledger.close()


def test_exact_psd_validation_preserves_singular_gram_matrices():
    from asset_management.signals.forecast_combination import _is_psd
    import random
    rng = random.Random(133)
    for _ in range(100):
        rows = [[D(rng.randrange(-3, 4)) for _ in range(3)] for _ in range(2)]
        gram = tuple(tuple(sum(row[i] * row[j] for row in rows) for j in range(3)) for i in range(3))
        assert _is_psd(gram)
    assert not _is_psd(((D(1), D(2)), (D(2), D(1))))
    assert not _is_psd(((D(0), D(1)), (D(1), D(1))))
