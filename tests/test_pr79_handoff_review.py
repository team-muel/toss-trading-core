"""One bounded self-adversarial pass for the PR #79 owner handoff.

Synthetic artifacts only. No broker, cloud, or systemd call is executed.
"""
from datetime import timedelta

import pytest

from asset_management.calculations import (
    CalculationLineageGraph, CalculationNode, CalculationNodeType,
    ModelCalculationBinding, bind_authorized_model_calculation,
    model_authorization_payload, publish_model_lineage_evidence,
)
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.governance import ModelScope
from asset_management.pricing import materialize_usd_fred_risk_free_curve
from asset_management.validation import ModelLineageRuntimeEvidence, assemble_d2_runtime_evidence
from asset_management.cli.legacy_retirement import gcp_plan
from test_pr79_review_regressions import write_curve, curve_body
from test_model_lineage_evidence import graph, registry_and_authorization, NOW, LICENSE
from test_legacy_retirement import PROJECT, INSTANCE, METRIC, policy, metric


@pytest.mark.parametrize('schema,quality', [
    ('unapproved-schema', 'RAW'), ('fred-risk-free-curve@1', 'VALID'),
])
def test_direct_curve_materializer_rejects_unapproved_contract(tmp_path, schema, quality):
    store = ImmutableDatasetStore(tmp_path)
    identifier = write_curve(store, curve_body(), schema_version=schema, quality_status=quality)
    with pytest.raises(DataQualityError, match='RISK_FREE_MANIFEST_CONTEXT_INVALID'):
        materialize_usd_fred_risk_free_curve(store=store, manifest_id=identifier, information_cutoff=NOW)


def forged_model_inputs(tmp_path, mutation):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    lineage = graph(store)
    bound_at = NOW
    if mutation == 'backdated':
        bound_at -= timedelta(days=1)
    else:
        original = lineage.nodes[lineage.final_node_id]
        output = dict(original.output_value)
        output['model_key' if mutation == 'model' else 'model_scope'] = (
            'UNAPPROVED@1' if mutation == 'model' else 'FORECAST_TOTAL_RETURN')
        final = CalculationNode.create(
            node_type=CalculationNodeType.FINAL_ESTIMATE, formula_version=original.formula_version,
            parameter_set_id=original.parameter_set_id, input_ids=original.input_ids,
            intermediate_values={'x': '1'}, output_value=output)
        nodes = {key: node for key, node in lineage.nodes.items() if key != original.node_id}
        lineage = CalculationLineageGraph(final.node_id, nodes | {final.node_id: final})
    body = dict(model_key='CAPM@2', scope=ModelScope.PRICING_BASELINE_RETURN.value,
                authorization_hash=authorization.authorization_hash,
                lineage_graph_hash=lineage.graph_hash, final_node_id=lineage.final_node_id,
                bound_at=bound_at.isoformat())
    binding = ModelCalculationBinding(
        body['model_key'], ModelScope.PRICING_BASELINE_RETURN, body['authorization_hash'],
        body['lineage_graph_hash'], body['final_node_id'], bound_at, digest(canonical(body)))
    return store, registry, authorization, lineage, binding


def raw_model_receipt(store, registry, authorization, lineage, binding,
                      *, schema='model-lineage-evidence@1', quality='VALID'):
    parents = tuple(sorted(node.raw_manifest_id for node in lineage.trace() if node.raw_manifest_id))
    body = {'registry': registry.payload(), 'model_authorization': model_authorization_payload(authorization),
            'binding': binding.payload(), 'lineage': lineage.payload()}
    return store.write(body, layer='gold', source='model-lineage', dataset='model-lineage-evidence',
        schema_version=schema, quality_status=quality, retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW, license_tag=LICENSE, code_revision='synthetic-adversarial',
        request_hash=digest(canonical(body)), parent_manifest_ids=parents, allow_mixed_parent_contracts=True)


@pytest.mark.parametrize('mutation', ['model', 'scope', 'backdated'])
def test_model_publisher_rederives_binding_instead_of_trusting_hashes(tmp_path, mutation):
    store, registry, authorization, lineage, binding = forged_model_inputs(tmp_path, mutation)
    with pytest.raises(InvariantViolation):
        publish_model_lineage_evidence(store=store, registry=registry, authorization=authorization,
            binding=binding, lineage=lineage, published_at=NOW, code_revision='synthetic-adversarial')


@pytest.mark.parametrize('mutation', ['model', 'scope', 'backdated'])
def test_model_runtime_rederives_binding_even_for_self_consistent_receipt(tmp_path, mutation):
    store, registry, authorization, lineage, binding = forged_model_inputs(tmp_path, mutation)
    receipt = raw_model_receipt(store, registry, authorization, lineage, binding)
    result = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None,
        model_lineage=ModelLineageRuntimeEvidence(registry, authorization, binding, lineage,
                                                  store, NOW, receipt.manifest_id))
    assert not result.checks['MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE'].passed


@pytest.mark.parametrize('schema,quality', [
    ('unapproved-schema', 'VALID'),
])
def test_model_runtime_rejects_unapproved_receipt_contract(tmp_path, schema, quality):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    lineage = graph(store)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key='CAPM@2', scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    receipt = raw_model_receipt(store, registry, authorization, lineage, binding, schema=schema, quality=quality)
    result = assemble_d2_runtime_evidence(risk_free=None, factor_risk=None,
        model_lineage=ModelLineageRuntimeEvidence(registry, authorization, binding, lineage,
                                                  store, NOW, receipt.manifest_id))
    assert not result.checks['MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE'].passed
    assert result.failure_reasons['MODEL_SCOPE_AND_CALCULATION_LINEAGE_COMPLETE'] == 'MODEL_LINEAGE_EVIDENCE_BINDING_INVALID'


@pytest.mark.parametrize('selector', ['mixed-or', 'denominator'])
def test_retirement_preserves_metrics_with_partially_understood_selectors(selector):
    literal = 'metric.type="logging.googleapis.com/user/unrelated" AND resource.type="gce_instance"'
    dynamic = 'metric.type = starts_with("logging.googleapis.com/user/foundation_")'
    threshold = {'filter': literal + ' OR ' + dynamic} if selector == 'mixed-or' else {
        'filter': literal, 'denominatorFilter': dynamic}
    other = policy(name=f'projects/{PROJECT}/alertPolicies/5678', displayName='Canonical monitoring',
                   conditions=[{'conditionThreshold': threshold}])
    plan = gcp_plan(project=PROJECT, instance=INSTANCE, policies=[policy(), other], metrics=[metric()])
    assert not any(command[1:4] == ['logging', 'metrics', 'delete'] for command in plan['commands'])
    assert any(item['name'] == METRIC for item in plan['retained_for_review'])


def test_retirement_still_deletes_eligible_metric_with_unrelated_literal_selector():
    other = policy(name=f'projects/{PROJECT}/alertPolicies/5678', displayName='Canonical monitoring',
        conditions=[{'conditionThreshold': {'filter': 'metric.type="logging.googleapis.com/user/unrelated"'}}])
    plan = gcp_plan(project=PROJECT, instance=INSTANCE, policies=[policy(), other], metrics=[metric()])
    assert len(plan['commands']) == 2
    assert plan['commands'][0][1:4] == ['monitoring', 'policies', 'delete']
    assert plan['commands'][1][1:4] == ['logging', 'metrics', 'delete']


def test_immutable_store_already_rejects_raw_quality_for_gold(tmp_path):
    store = ImmutableDatasetStore(tmp_path)
    registry, authorization = registry_and_authorization()
    lineage = graph(store)
    binding = bind_authorized_model_calculation(model_registry=registry, authorization=authorization,
        model_key='CAPM@2', scope=ModelScope.PRICING_BASELINE_RETURN, lineage=lineage, bound_at=NOW)
    with pytest.raises(ValueError, match='INVALID_QUALITY'):
        raw_model_receipt(store, registry, authorization, lineage, binding, quality='RAW')
