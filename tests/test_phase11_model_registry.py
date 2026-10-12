from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import (
    ModelDefinition, ModelRegistry, ModelScope, ModelStatus,
)


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
ACTIVE_AT = NOW + timedelta(seconds=3)


def definition(model_id="CAPM", scopes=(ModelScope.REQUIRED_RETURN,)):
    return ModelDefinition(
        model_id, "1", "required return", ("risk_free", "beta", "market_premium"),
        ("required_return",), scopes, ("unstable_beta", "missing_history"),
        date(2026, 9, 1), date(2026, 12, 31), "risk-model-owner",
    )


def active_registry(model_id="CAPM", scopes=(ModelScope.REQUIRED_RETURN,)):
    registry = ModelRegistry()
    model = definition(model_id, scopes)
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED,
                                    ModelStatus.ACTIVE)):
        registry.transition(model.key, status, effective_at=NOW + timedelta(seconds=index),
                            reason=f"promote to {status.value}",
                            evidence_ids=(f"evidence:{index}",))
    return registry


def test_complete_model_contract_and_lifecycle_are_persisted(tmp_path):
    registry = active_registry()
    payload = registry.payload()
    model = payload["models"][0]
    assert model["status"] == "ACTIVE"
    assert set(model) == {"model_id", "version", "purpose", "inputs", "outputs",
                          "approved_scope", "known_failure_modes", "validation_date",
                          "review_date", "owner", "status"}
    assert [item["to_status"] for item in payload["transitions"]] == [
        "VALIDATED", "APPROVED", "ACTIVE"]
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    first = registry.publish(store)
    assert first == registry.publish(store)
    assert json.loads((tmp_path / "catalog" / "model-registry" /
                       f"{first}.json").read_text()) == payload


@pytest.mark.parametrize("change,reason", [
    ({"model_id": ""}, "MODEL_DEFINITION_INVALID"),
    ({"inputs": ()}, "MODEL_INPUTS_INVALID"),
    ({"outputs": ()}, "MODEL_OUTPUTS_INVALID"),
    ({"known_failure_modes": ()}, "MODEL_FAILURE_MODES_INVALID"),
    ({"approved_scope": ()}, "MODEL_DEFINITION_INVALID"),
    ({"review_date": date(2026, 8, 31)}, "MODEL_DEFINITION_INVALID"),
])
def test_incomplete_model_metadata_fails_closed(change, reason):
    with pytest.raises(InvariantViolation, match=reason):
        replace(definition(), **change)


@pytest.mark.parametrize("model_id", ["CAPM", "MULTIFACTOR"])
@pytest.mark.parametrize("scope,output", [
    (ModelScope.POSITION_SIZING, "position_weights"),
    (ModelScope.ORDER_CREATION, "order"),
    (ModelScope.REQUIRED_RETURN, "hurdle_rate"),
    (ModelScope.POSITION_SIZING, "required_return"),
])
def test_pricing_model_identity_cannot_be_redeclared_for_other_authority(model_id, scope, output):
    with pytest.raises(InvariantViolation, match="PRICING_MODEL_AUTHORITY_CONFLICT"):
        replace(definition(model_id), approved_scope=(scope,), outputs=(output,))
    import jsonschema
    schema = json.loads(__import__("pathlib").Path("schemas/model_registry.schema.json").read_text())
    payload = definition(model_id).payload() | {"approved_scope": [scope.value], "outputs": [output]}
    assert not jsonschema.Draft202012Validator(schema["$defs"]["model"]).is_valid(payload)


@pytest.mark.parametrize("model_id", ["CAPM", "MULTIFACTOR"])
def test_rehashed_pricing_snapshot_cannot_gain_sizing_authority(model_id):
    payload = active_registry(model_id).payload()
    payload["models"][0] |= {"approved_scope": ["POSITION_SIZING"], "outputs": ["position_weights"]}
    payload["registry_hash"] = digest(canonical({key: value for key, value in payload.items()
                                                if key != "registry_hash"}))
    with pytest.raises(InvariantViolation, match="MODEL_REGISTRY_SNAPSHOT_INVALID"):
        ModelRegistry.from_payload(payload)


def test_generic_allocator_scope_and_legacy_pricing_identity_remain_explicit():
    allocator = replace(definition("ALLOCATOR"), outputs=("position_weights",),
                        approved_scope=(ModelScope.POSITION_SIZING,))
    assert allocator.approved_scope == (ModelScope.POSITION_SIZING,)
    legacy = active_registry().payload()
    assert ModelRegistry.from_payload(legacy).payload() == legacy


@pytest.mark.parametrize("model_id,scope,output", [
    ("CUSTOM_POLICY", ModelScope.REQUIRED_RETURN, "hurdle_rate"),
    ("ALLOCATOR", ModelScope.POSITION_SIZING, "position_weights"),
    ("CAPM", ModelScope.REQUIRED_RETURN, "required_return"),
    ("MULTIFACTOR", ModelScope.PRICING_BASELINE_RETURN, "pricing_baseline_return"),
])
def test_identity_specific_schema_rule_preserves_valid_generic_and_pricing_roles(model_id, scope, output):
    import jsonschema
    model = replace(definition(model_id), outputs=(output,), approved_scope=(scope,))
    schema = json.loads(__import__("pathlib").Path("schemas/model_registry.schema.json").read_text())
    jsonschema.Draft202012Validator(schema["$defs"]["model"]).validate(model.payload())


@pytest.mark.parametrize("field,reason", [
    ("inputs", "MODEL_INPUTS_INVALID"), ("outputs", "MODEL_OUTPUTS_INVALID"),
    ("known_failure_modes", "MODEL_FAILURE_MODES_INVALID"),
])
def test_identifier_list_cannot_be_supplied_as_a_string(field, reason):
    with pytest.raises(InvariantViolation, match=reason):
        replace(definition("GENERIC"), **{field: "abc"})


def test_validation_and_review_fields_are_dates_not_datetimes():
    with pytest.raises(InvariantViolation, match="MODEL_DEFINITION_INVALID"):
        replace(definition(), validation_date=NOW, review_date=NOW + timedelta(days=30))


def test_transition_evidence_ids_cannot_be_split_from_a_string():
    registry = ModelRegistry()
    registry.register(definition())
    before = registry.payload()
    with pytest.raises(InvariantViolation, match="MODEL_TRANSITION_EVIDENCE_INVALID"):
        registry.transition("CAPM@1", ModelStatus.VALIDATED, effective_at=NOW,
                            reason="test", evidence_ids="abc")
    assert registry.payload() == before


def test_registration_cannot_skip_development_and_definition_cannot_change():
    registry = ModelRegistry()
    with pytest.raises(InvariantViolation, match="MUST_START_IN_DEVELOPMENT"):
        registry.register(replace(definition(), status=ModelStatus.APPROVED))
    registry.register(definition())
    with pytest.raises(InvariantViolation, match="MODEL_DEFINITION_CONFLICT"):
        registry.register(replace(definition(), purpose="position sizing"))


def test_invalid_transition_and_missing_evidence_fail_closed():
    registry = ModelRegistry()
    registry.register(definition())
    with pytest.raises(InvariantViolation, match="LIFECYCLE_TRANSITION_INVALID"):
        registry.transition("CAPM@1", ModelStatus.ACTIVE, effective_at=NOW,
                            reason="skip review", evidence_ids=("evidence",))
    with pytest.raises(InvariantViolation, match="TRANSITION_EVIDENCE_INVALID"):
        registry.transition("CAPM@1", ModelStatus.VALIDATED, effective_at=NOW,
                            reason="validated", evidence_ids=())


def test_scope_status_review_and_registry_changes_cannot_be_bypassed():
    registry = active_registry()
    authorization = registry.authorize("CAPM@1", ModelScope.REQUIRED_RETURN, at=ACTIVE_AT)
    registry.require_authorization(authorization, model_key="CAPM@1",
                                   scope=ModelScope.REQUIRED_RETURN, at=ACTIVE_AT)
    with pytest.raises(InvariantViolation, match="MODEL_AUTHORIZATION_INVALID"):
        registry.require_authorization(
            replace(authorization, authorized_at="not-a-timestamp"), model_key="CAPM@1",
            scope=ModelScope.REQUIRED_RETURN, at=ACTIVE_AT)
    with pytest.raises(InvariantViolation, match="MODEL_SCOPE_NOT_APPROVED"):
        registry.authorize("CAPM@1", ModelScope.POSITION_SIZING, at=ACTIVE_AT)
    with pytest.raises(InvariantViolation, match="MODEL_REVIEW_OVERDUE"):
        registry.authorize("CAPM@1", ModelScope.REQUIRED_RETURN,
                           at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    registry.transition("CAPM@1", ModelStatus.DEGRADED,
                        effective_at=NOW + timedelta(minutes=1), reason="unstable beta",
                        evidence_ids=("monitor:beta",))
    with pytest.raises(InvariantViolation, match="MODEL_AUTHORIZATION_INVALID"):
        registry.require_authorization(authorization, model_key="CAPM@1",
                                       scope=ModelScope.REQUIRED_RETURN, at=ACTIVE_AT)
    registry.authorize("CAPM@1", ModelScope.REQUIRED_RETURN, at=ACTIVE_AT)
    with pytest.raises(InvariantViolation, match="MODEL_NOT_ACTIVE"):
        registry.authorize("CAPM@1", ModelScope.REQUIRED_RETURN,
                           at=NOW + timedelta(minutes=1, seconds=1))


def test_future_transition_never_activates_a_model_before_its_effective_time():
    registry = ModelRegistry()
    model = definition()
    registry.register(model)
    for index, status in enumerate((ModelStatus.VALIDATED, ModelStatus.APPROVED,
                                    ModelStatus.ACTIVE), start=1):
        registry.transition(model.key, status,
                            effective_at=NOW + timedelta(hours=1, seconds=index),
                            reason=f"schedule {status.value}", evidence_ids=(f"evidence:{index}",))
    assert registry.models[model.key].status is ModelStatus.ACTIVE
    assert registry.status_at(model.key, at=NOW) is ModelStatus.DEVELOPMENT
    with pytest.raises(InvariantViolation, match="MODEL_NOT_ACTIVE"):
        registry.authorize(model.key, ModelScope.REQUIRED_RETURN, at=NOW)
    authorization = registry.authorize(
        model.key, ModelScope.REQUIRED_RETURN, at=NOW + timedelta(hours=1, seconds=4))
    registry.require_authorization(
        authorization, model_key=model.key, scope=ModelScope.REQUIRED_RETURN,
        at=NOW + timedelta(hours=1, seconds=4))


def test_schema_matches_registry_and_model_contract():
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/model_registry.schema.json").read_text())
    payload = active_registry().payload()
    assert set(schema["required"]) == set(payload)
    assert set(schema["$defs"]["model"]["required"]) == set(payload["models"][0])
    assert set(schema["$defs"]["transition"]["required"]) == set(payload["transitions"][0])
