"""AMA-38 canonical outputs cannot acquire authority through another scope."""
from dataclasses import replace
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from asset_management.domain.errors import InvariantViolation
from asset_management.governance import ModelScope
from test_phase11_model_registry import definition, active_registry


OUTPUTS = (
    "pricing_baseline_return", "model_relative_alpha",
    "expected_benchmark_active_return", "realized_active_return", "regression_alpha",
)
SCHEMA = json.loads((Path(__file__).parents[1] / "schemas/model_registry.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA["$defs"]["model"])


@pytest.mark.parametrize("output", OUTPUTS)
def test_matching_canonical_output_authority_is_valid(output):
    model = replace(definition(), outputs=(output,), approved_scope=(ModelScope(output.upper()),))
    VALIDATOR.validate(model.payload())


@pytest.mark.parametrize("output,scope", [
    (output, scope) for output in OUTPUTS for scope in ModelScope
    if scope.value != output.upper()
])
def test_canonical_output_cannot_borrow_another_scope(output, scope):
    payload = definition().payload()
    payload.update(outputs=[output], approved_scope=[scope.value])
    assert not VALIDATOR.is_valid(payload)
    with pytest.raises(InvariantViolation, match="PRICING_OUTPUT_AUTHORITY_CONFLICT"):
        replace(definition(), outputs=(output,), approved_scope=(scope,))


@pytest.mark.parametrize("output", OUTPUTS)
@pytest.mark.parametrize("change", ("wrong_output", "extra_output", "extra_scope"))
def test_canonical_scope_cannot_expand_its_output_authority(output, change):
    scope = ModelScope(output.upper())
    outputs = ("unrelated",) if change == "wrong_output" else (output,)
    scopes = (scope,)
    if change == "extra_output":
        outputs += ("unrelated",)
    if change == "extra_scope":
        scopes += (ModelScope.ORDER_CREATION,)
    payload = definition().payload()
    payload.update(outputs=list(outputs), approved_scope=[s.value for s in scopes])
    assert not VALIDATOR.is_valid(payload)
    with pytest.raises(InvariantViolation, match="PRICING_OUTPUT_AUTHORITY_CONFLICT"):
        replace(definition(), outputs=outputs, approved_scope=scopes)


def test_legacy_registry_hash_and_schema_are_preserved():
    payload = active_registry().payload()
    assert payload["registry_hash"] == "ee201efd9ee3dec366f16d1c54e727710f6ecb7d6d91abf51e3d5d6e822ff41b"
    Draft202012Validator.check_schema(SCHEMA)
    Draft202012Validator(SCHEMA).validate(payload)
