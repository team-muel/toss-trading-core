from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest
import jsonschema
from pathlib import Path

from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.domain.horizon import (
    DECISION_HORIZONS, DecayProfile, SignalValidity, require_horizon_alignment,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def validity(*, forecast=63, holding=21, days=10, profile=DecayProfile.LINEAR,
             half_life=None):
    return SignalValidity(forecast, holding, NOW + timedelta(days=days), profile, half_life)


def test_supported_horizons_and_payload_are_explicit():
    item = validity()
    assert DECISION_HORIZONS == (21, 63, 126, 252)
    assert item.payload() == {
        "forecast_horizon": 63,
        "holding_horizon": 21,
        "valid_until": "2026-09-16T00:00:00+00:00",
        "decay_profile": "LINEAR",
        "half_life_seconds": None,
    }


@pytest.mark.parametrize("changes,reason", [
    ({"forecast_horizon": 30}, "SIGNAL_HORIZON_UNSUPPORTED"),
    ({"holding_horizon": 30}, "SIGNAL_HORIZON_UNSUPPORTED"),
    ({"forecast_horizon": 21.0}, "SIGNAL_HORIZON_UNSUPPORTED"),
    ({"holding_horizon": Decimal(21)}, "SIGNAL_HORIZON_UNSUPPORTED"),
    ({"forecast_horizon": True}, "SIGNAL_HORIZON_UNSUPPORTED"),
    ({"valid_until": datetime(2026, 9, 7)}, "SIGNAL_VALID_UNTIL_NOT_AWARE"),
    ({"decay_profile": "LINEAR"}, "SIGNAL_DECAY_PROFILE_UNKNOWN"),
    ({"decay_profile": DecayProfile.EXPONENTIAL}, "SIGNAL_DECAY_POLICY_INVALID"),
    ({"half_life_seconds": 60}, "SIGNAL_DECAY_POLICY_INVALID"),
])
def test_unknown_or_incomplete_contract_fails_closed(changes, reason):
    values = dict(forecast_horizon=63, holding_horizon=21,
                  valid_until=NOW + timedelta(days=10), decay_profile=DecayProfile.LINEAR,
                  half_life_seconds=None)
    values.update(changes)
    with pytest.raises(InvariantViolation, match=reason):
        SignalValidity(**values)


def test_step_linear_and_exponential_decay_are_deterministic():
    midpoint = NOW + timedelta(days=5)
    assert validity(profile=DecayProfile.STEP).effective_weight(
        produced_at=NOW, evaluated_at=midpoint) == Decimal(1)
    assert validity().effective_weight(produced_at=NOW, evaluated_at=midpoint) == Decimal("0.5")
    exponential = validity(profile=DecayProfile.EXPONENTIAL, half_life=5 * 86400)
    assert abs(exponential.effective_weight(produced_at=NOW, evaluated_at=midpoint) -
               Decimal("0.5")) < Decimal("1e-30")
    assert validity().effective_weight(
        produced_at=NOW, evaluated_at=NOW + timedelta(days=11)) == 0


def test_invalid_evaluation_times_and_windows_fail_closed():
    item = validity()
    with pytest.raises(DataQualityError, match="SIGNAL_EVALUATED_BEFORE_PRODUCTION"):
        item.effective_weight(produced_at=NOW, evaluated_at=NOW - timedelta(seconds=1))
    with pytest.raises(DataQualityError, match="SIGNAL_EVALUATION_TIME_NOT_AWARE"):
        item.effective_weight(produced_at=NOW.replace(tzinfo=None), evaluated_at=NOW)
    expired_at_birth = SignalValidity(63, 21, NOW, DecayProfile.STEP)
    with pytest.raises(InvariantViolation, match="SIGNAL_VALIDITY_WINDOW_INVALID"):
        expired_at_birth.effective_weight(produced_at=NOW, evaluated_at=NOW)


def test_horizon_alignment_rejects_forecast_and_holding_mismatch():
    first = validity()
    assert require_horizon_alignment((first, first)) is first
    with pytest.raises(DataQualityError, match="SIGNAL_HORIZON_MISMATCH"):
        require_horizon_alignment((first, validity(forecast=126)))
    with pytest.raises(DataQualityError, match="SIGNAL_HORIZON_MISMATCH"):
        require_horizon_alignment((first, validity(holding=63)))
    with pytest.raises(DataQualityError, match="SIGNAL_SET_EMPTY"):
        require_horizon_alignment(())


def test_schema_requires_complete_serialized_contract():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/decision_horizon.schema.json").read_text())
    assert set(schema["required"]) == set(validity().payload())
    for name in ("feature_snapshot", "pricing_result", "expected_return"):
        consumer = json.loads((root / f"schemas/{name}.schema.json").read_text())
        assert "validity" in consumer["required"]


@pytest.mark.parametrize("values", [(None,), (object(),), (validity(), None)])
def test_alignment_requires_typed_validity_even_for_one_input(values):
    with pytest.raises(DataQualityError, match="SIGNAL_VALIDITY_REQUIRED"):
        require_horizon_alignment(iter(values))


@pytest.mark.parametrize("changes", [
    {"days": 11}, {"profile": DecayProfile.STEP},
    {"profile": DecayProfile.EXPONENTIAL, "half_life": 3600},
])
def test_alignment_rejects_different_expiry_or_decay(changes):
    with pytest.raises(DataQualityError, match="SIGNAL_VALIDITY_MISMATCH"):
        require_horizon_alignment((validity(), validity(**changes)))


def test_complete_validity_roundtrips_without_coercion():
    item = validity(profile=DecayProfile.EXPONENTIAL, half_life=3600)
    assert SignalValidity.from_payload(item.payload()) == item


@pytest.mark.parametrize("update", [
    lambda p: p.update(forecast_horizon="63"),
    lambda p: p.update(holding_horizon=21.0),
    lambda p: p.update(valid_until="invalid"),
    lambda p: p.update(decay_profile="UNKNOWN"),
    lambda p: p.update(extra="ignored"),
])
def test_payload_decoder_rejects_coercion_or_incomplete_contract(update):
    payload = validity().payload()
    update(payload)
    with pytest.raises(InvariantViolation, match="SIGNAL_VALIDITY_PAYLOAD_INVALID"):
        SignalValidity.from_payload(payload)


@pytest.mark.parametrize("name", ["decision_horizon", "forecast_combination",
    "portfolio_transition", "signal_forecast_calibration", "state_snapshot"])
@pytest.mark.parametrize("profile,half_life,valid", [
    ("STEP", None, True), ("LINEAR", None, True), ("EXPONENTIAL", 3600, True),
    ("EXPONENTIAL", None, False), ("EXPONENTIAL", 0, False),
    ("STEP", 3600, False), ("LINEAR", 3600, False),
])
def test_all_serialized_validity_contracts_match_decay_policy(name, profile, half_life, valid):
    schema = json.loads((Path(__file__).parents[1] / "schemas" / f"{name}.schema.json").read_text())
    fields = set(validity().payload())
    candidates = []
    def visit(node):
        if isinstance(node, dict):
            if set(node.get("required", [])) == fields and fields <= set(node.get("properties", {})):
                candidates.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    assert len(candidates) == 1
    payload = validity().payload() | {"decay_profile": profile, "half_life_seconds": half_life}
    validator = jsonschema.Draft202012Validator(candidates[0])
    assert validator.is_valid(payload) is valid
