from datetime import date, datetime, timezone

import pytest

from asset_management.calculations import (
    CalculationLineageGraph, CalculationNode, CalculationNodeType, bind_authorized_model_calculation,
    publish_model_lineage_evidence,
)
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import InvariantViolation
from asset_management.governance import ModelDefinition, ModelRegistry, ModelScope, ModelStatus
from asset_management.validation import ModelLineageRuntimeEvidence, assemble_d2_runtime_evidence


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
LICENSE = "purpose=research;redistribution=forbidden;retention=project"


def registry_and_authorization():
    registry = ModelRegistry()
    model = ModelDefinition("CAPM", "2", "baseline", ("risk_free",), ("pricing_baseline_return",),
        (ModelScope.PRICING_BASELINE_RETURN,), ("input missing",), date(2026, 1, 1),
        date(2026, 12, 31), "owner")
    registry.register(model)
    for status in (ModelStatus.VALIDATED, ModelStatus.APPROVED, ModelStatus.ACTIVE):
        registry.transition(model.key, status, effective_at=NOW, reason="test", evidence_ids=("evidence:test",))
    return registry, registry.authorize(model.key, ModelScope.PRICING_BASELINE_RETURN, at=NOW)


def graph(store: ImmutableDatasetStore) -> CalculationLineageGraph:
    raw_manifest = store.write({"value": "1"}, layer="bronze", source="test-source", dataset="raw-input",
        schema_version="raw@1", retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW, license_tag=LICENSE,
        code_revision="raw@1", request_hash="e" * 64, quality_status="RAW")
    raw = CalculationNode.from_raw_manifest(store, raw_manifest.manifest_id, parameter_set_id="p@1")
    feature = CalculationNode.create(node_type=CalculationNodeType.FEATURE, formula_version="feature@1",
        parameter_set_id="p@1", input_ids=(raw.node_id,), intermediate_values={"x": "1"}, output_value={"x": "1"})
    intermediate = CalculationNode.create(node_type=CalculationNodeType.INTERMEDIATE_CALCULATION,
        formula_version="model@1", parameter_set_id="p@1", input_ids=(feature.node_id,),
        intermediate_values={"x": "1"}, output_value={"x": "1"})
    final = CalculationNode.create(node_type=CalculationNodeType.FINAL_ESTIMATE, formula_version="capm@2",
        parameter_set_id="p@1", input_ids=(intermediate.node_id,), intermediate_values={"x": "1"},
        output_value={"model_key": "CAPM@2", "model_scope": "PRICING_BASELINE_RETURN", "value": ".08"})
    return CalculationLineageGraph(final.node_id, {node.node_id: node for node in (raw, feature, intermediate, final)})


def test_publishes_and_verifies_exact_authorization_lineage_evidence(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    lineage = graph(store)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    evidence = publish_model_lineage_evidence(store=store, registry=registry, authorization=authorization,
        binding=binding, lineage=lineage, published_at=NOW, code_revision="model-lineage-evidence@1")
    result = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None,
        model_lineage=ModelLineageRuntimeEvidence(registry, authorization, binding, lineage, store, NOW, evidence.manifest_id))
    assert result.checks["MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE"].passed


def test_rejects_evidence_after_authorization_review_window(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    lineage = graph(store)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    with pytest.raises(InvariantViolation, match="MODEL_REVIEW_OVERDUE"):
        publish_model_lineage_evidence(store=store, registry=registry, authorization=authorization,
            binding=binding, lineage=lineage, published_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            code_revision="model-lineage-evidence@1")
