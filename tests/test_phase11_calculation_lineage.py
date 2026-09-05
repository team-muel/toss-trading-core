from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from asset_management.calculations import (
    CalculationLineageGraph, CalculationNode, CalculationNodeType,
)
from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError, InvariantViolation


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
LICENSE = "purpose=internal-research;redistribution=forbidden;retention=perpetual"


def bronze(store, body=None):
    return store.write(
        body or {"instrument_id": "SPY", "close": "100"},
        layer="bronze", source="provider", dataset="prices-raw", schema_version="raw-v1",
        retrieved_at=NOW, available_at=NOW, provider_timestamp=NOW,
        license_tag=LICENSE, code_revision="git:abcdef0", request_hash="a" * 64,
        quality_status="RAW",
    )


def derived(kind, parents, formula, intermediate, output):
    return CalculationNode.create(
        node_type=kind, formula_version=formula, parameter_set_id="parameters-v1",
        input_ids=tuple(node.node_id for node in parents),
        intermediate_values=intermediate, output_value=output,
    )


def graph(store):
    raw = CalculationNode.from_raw_manifest(
        store, bronze(store).manifest_id, parameter_set_id="parameters-v1")
    feature = derived(CalculationNodeType.FEATURE, (raw,), "return@1",
                      {"start": "90", "end": "100"}, {"return": "0.1111111111"})
    intermediate = derived(CalculationNodeType.INTERMEDIATE_CALCULATION, (feature,),
                           "shrinkage@2", {"confidence": "0.8", "prior": "0.04"},
                           {"shrunk_return": "0.0968888889"})
    final = derived(CalculationNodeType.FINAL_ESTIMATE, (intermediate,), "expected-return@3",
                    {"cost": "0.001"}, {"net_expected_return": "0.0958888889"})
    nodes = {node.node_id: node for node in (raw, feature, intermediate, final)}
    return CalculationLineageGraph(final.node_id, nodes)


def test_final_estimate_traces_to_verified_immutable_raw_manifest(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    lineage = graph(store)
    assert [node.node_type for node in lineage.trace()] == list(CalculationNodeType)
    lineage.verify_raw_manifests(store)
    catalog_id = lineage.publish(store)
    assert len(catalog_id) == 64
    persisted = json.loads((tmp_path / "catalog" / "calculation-lineage" /
                            f"{catalog_id}.json").read_text())
    assert persisted == lineage.payload()
    assert persisted["nodes"][-1]["output_hash"]


def test_same_inputs_formula_parameters_and_values_are_deterministic(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    first = graph(store)
    second = graph(store)
    assert first == second
    assert first.graph_hash == second.graph_hash
    assert first.publish(store) == second.publish(store)
    with pytest.raises(TypeError):
        first.trace()[0].output_value["close"] = "999"


def test_missing_parent_and_layer_skip_fail_closed(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    valid = graph(store)
    nodes = dict(valid.nodes)
    raw = valid.trace()[0]
    feature = valid.trace()[1]
    del nodes[raw.node_id]
    with pytest.raises(InvariantViolation, match="CALCULATION_PARENT_MISSING"):
        CalculationLineageGraph(valid.final_node_id, nodes)
    skipped = CalculationNode.create(
        node_type=CalculationNodeType.INTERMEDIATE_CALCULATION,
        formula_version="bad@1", parameter_set_id="parameters-v1",
        input_ids=(raw.node_id,), intermediate_values={"value": "1"}, output_value="1")
    broken = dict(valid.nodes) | {skipped.node_id: skipped}
    final = derived(CalculationNodeType.FINAL_ESTIMATE, (skipped,), "final@1",
                    {"value": "1"}, "1")
    broken[final.node_id] = final
    with pytest.raises(InvariantViolation, match="CALCULATION_LAYER_ORDER_INVALID"):
        CalculationLineageGraph(final.node_id, broken)
    assert feature.node_type is CalculationNodeType.FEATURE


def test_output_hash_node_id_and_required_metadata_cannot_be_fabricated(tmp_path):
    node = graph(ImmutableDatasetStore(tmp_path, credentials_classified=True)).trace()[1]
    with pytest.raises(InvariantViolation, match="CALCULATION_OUTPUT_HASH_INVALID"):
        replace(node, output_hash="0" * 64)
    with pytest.raises(InvariantViolation, match="CALCULATION_NODE_ID_INVALID"):
        replace(node, node_id="0" * 64)
    with pytest.raises(InvariantViolation, match="CALCULATION_INTERMEDIATE_VALUES_MISSING"):
        CalculationNode.create(
            node_type=CalculationNodeType.FEATURE, formula_version="feature@1",
            parameter_set_id="parameters-v1", input_ids=("a" * 64,),
            intermediate_values={}, output_value="1")
    with pytest.raises(InvariantViolation, match="CALCULATION_VALUE_NOT_FINITE"):
        derived(CalculationNodeType.FEATURE, (node,), "feature@1",
                {"bad": Decimal("NaN")}, "1")
    with pytest.raises(InvariantViolation, match="CALCULATION_NODE_CONTRACT_INVALID"):
        CalculationNode.create(
            node_type=CalculationNodeType.FEATURE, formula_version="feature@1",
            parameter_set_id="parameters-v1", input_ids=("not-a-node-id",),
            intermediate_values={"value": "1"}, output_value="1")


def test_raw_manifest_must_exist_be_bronze_and_match_content(tmp_path):
    store = ImmutableDatasetStore(tmp_path, credentials_classified=True)
    with pytest.raises(DataQualityError, match="CALCULATION_RAW_MANIFEST_UNVERIFIED"):
        CalculationNode.from_raw_manifest(store, "0" * 64, parameter_set_id="parameters-v1")
    parent = bronze(store)
    silver = store.write(
        [{"instrument_id": "SPY", "close": "100"}], layer="silver", source="provider",
        dataset="prices", schema_version="silver-v1", retrieved_at=NOW, available_at=NOW,
        provider_timestamp=NOW, license_tag=LICENSE, code_revision="git:abcdef0",
        request_hash="b" * 64, parent_manifest_ids=(parent.manifest_id,),
    )
    with pytest.raises(DataQualityError, match="CALCULATION_RAW_MANIFEST_NOT_BRONZE"):
        CalculationNode.from_raw_manifest(store, silver.manifest_id,
                                          parameter_set_id="parameters-v1")


def test_schema_matches_graph_and_node_contract(tmp_path):
    root = __import__("pathlib").Path(__file__).parents[1]
    schema = json.loads((root / "schemas/calculation_lineage.schema.json").read_text())
    payload = graph(ImmutableDatasetStore(tmp_path, credentials_classified=True)).payload()
    assert set(schema["required"]) == set(payload)
    assert set(schema["$defs"]["node"]["required"]) == set(payload["nodes"][0])
