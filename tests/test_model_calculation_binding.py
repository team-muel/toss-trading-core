from datetime import date, datetime, timezone

import pytest

from asset_management.calculations import (
    CalculationLineageGraph, CalculationNode, CalculationNodeType,
    bind_authorized_model_calculation,
)
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import ModelDefinition, ModelRegistry, ModelScope, ModelStatus
from runtime_model_support import persisted_runtime_authorization


NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def authorization():
    registry = ModelRegistry()
    model = ModelDefinition("CAPM", "2", "baseline", ("risk_free",), ("pricing_baseline_return",),
        (ModelScope.PRICING_BASELINE_RETURN,), ("input missing",), date(2026, 1, 1),
        date(2026, 12, 31), "owner")
    registry.register(model)
    for status in (ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE):
        registry.transition(model.key, status, effective_at=NOW, reason="test", evidence_ids=("evidence:test",))
    return persisted_runtime_authorization(
        registry, model_key=model.key, scope=ModelScope.PRICING_BASELINE_RETURN,
        as_of=NOW, information_cutoff=NOW,
    )


def lineage(*, declared=True):
    raw = CalculationNode.create(node_type=CalculationNodeType.RAW_DATA, formula_version="raw@1",
        parameter_set_id="p@1", input_ids=(), intermediate_values={"source": "test"},
        output_value={"value": "1"}, raw_manifest_id="a" * 64)
    feature = CalculationNode.create(node_type=CalculationNodeType.FEATURE, formula_version="feature@1",
        parameter_set_id="p@1", input_ids=(raw.node_id,), intermediate_values={"x": "1"}, output_value={"x": "1"})
    intermediate = CalculationNode.create(node_type=CalculationNodeType.INTERMEDIATE_CALCULATION,
        formula_version="model@1", parameter_set_id="p@1", input_ids=(feature.node_id,),
        intermediate_values={"x": "1"}, output_value={"x": "1"})
    output = {"model_key": "CAPM@2", "model_scope": "PRICING_BASELINE_RETURN", "value": ".08"} if declared else {"value": ".08"}
    final = CalculationNode.create(node_type=CalculationNodeType.FINAL_ESTIMATE, formula_version="capm@2",
        parameter_set_id="p@1", input_ids=(intermediate.node_id,), intermediate_values={"x": "1"}, output_value=output)
    return CalculationLineageGraph(final.node_id, {item.node_id: item for item in (raw, feature, intermediate, final)})


def test_authorized_scope_and_complete_lineage_create_reproducible_binding():
    repository, token = authorization()
    first = bind_authorized_model_calculation(model_registry_evidence=repository, runtime_authorization=token,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage(), bound_at=NOW)
    second = bind_authorized_model_calculation(model_registry_evidence=repository, runtime_authorization=token,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage(), bound_at=NOW)
    assert first.binding_id == second.binding_id and len(first.binding_id) == 64


def test_binding_rejects_lineage_that_does_not_declare_authorized_scope():
    repository, token = authorization()
    with pytest.raises(InvariantViolation, match="LINEAGE_SCOPE_MISMATCH"):
        bind_authorized_model_calculation(model_registry_evidence=repository, runtime_authorization=token,
            model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN,
            lineage=lineage(declared=False), bound_at=NOW)
