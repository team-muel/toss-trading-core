from datetime import date, datetime, timedelta, timezone

import pytest

from asset_management.calculations import (
    MODEL_LINEAGE_EVIDENCE_DATASET, CalculationLineageGraph, CalculationNode, CalculationNodeType,
    bind_authorized_model_calculation, model_authorization_payload, publish_model_lineage_evidence,
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


def graph(store: ImmutableDatasetStore, *, raw_available_at: datetime = NOW) -> CalculationLineageGraph:
    raw_manifest = store.write({"value": "1"}, layer="bronze", source="test-source", dataset="raw-input",
        schema_version="raw@1", retrieved_at=raw_available_at, available_at=raw_available_at,
        provider_timestamp=raw_available_at, license_tag=LICENSE, code_revision="raw@1",
        request_hash="e" * 64, quality_status="RAW")
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


def test_rejects_raw_input_that_arrived_after_binding(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    raw_available_at = NOW + timedelta(days=1)
    lineage = graph(store, raw_available_at=raw_available_at)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    with pytest.raises(InvariantViolation, match="RAW_AFTER_BINDING"):
        publish_model_lineage_evidence(store=store, registry=registry, authorization=authorization,
            binding=binding, lineage=lineage, published_at=raw_available_at,
            code_revision="model-lineage-evidence@1")


def test_runtime_rejects_legacy_lineage_with_post_binding_raw_input(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    published_at = NOW + timedelta(days=1)
    lineage = graph(store, raw_available_at=published_at)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key="CAPM@2", scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    parents = tuple(sorted(node.raw_manifest_id for node in lineage.trace() if node.raw_manifest_id is not None))
    body = {"registry": registry.payload(), "model_authorization": model_authorization_payload(authorization),
            "binding": binding.payload(), "lineage": lineage.payload()}
    legacy_evidence = store.write(
        body, layer="gold", source="model-lineage", dataset=MODEL_LINEAGE_EVIDENCE_DATASET,
        schema_version="model-lineage-evidence@1", retrieved_at=published_at, available_at=published_at,
        provider_timestamp=published_at, license_tag=LICENSE, code_revision="legacy-model-lineage-evidence@1",
        request_hash="f" * 64, parent_manifest_ids=parents, allow_mixed_parent_contracts=True)
    result = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None,
        model_lineage=ModelLineageRuntimeEvidence(
            registry, authorization, binding, lineage, store, published_at, legacy_evidence.manifest_id))
    name = "MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE"
    assert not result.checks[name].passed
    assert result.failure_reasons[name] == "MODEL_LINEAGE_RAW_AFTER_BINDING"
