"""AMA-37 immutable calculation lineage graph."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import math
import re
from types import MappingProxyType
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError, InvariantViolation


class CalculationNodeType(StrEnum):
    RAW_DATA = "RAW_DATA"
    FEATURE = "FEATURE"
    INTERMEDIATE_CALCULATION = "INTERMEDIATE_CALCULATION"
    FINAL_ESTIMATE = "FINAL_ESTIMATE"


_RANK = {kind: rank for rank, kind in enumerate(CalculationNodeType)}


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvariantViolation("CALCULATION_VALUE_NOT_FINITE")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvariantViolation("CALCULATION_VALUE_TIME_NOT_AWARE")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key.strip() for key in value):
            raise InvariantViolation("CALCULATION_VALUE_KEY_INVALID")
        return {key: _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if type(value) is float and not math.isfinite(value):
        raise InvariantViolation("CALCULATION_VALUE_NOT_FINITE")
    if value is None or type(value) in {str, int, bool, float}:
        return value
    raise InvariantViolation("CALCULATION_VALUE_UNSUPPORTED")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CalculationNode:
    node_id: str
    node_type: CalculationNodeType
    formula_version: str
    parameter_set_id: str
    input_ids: tuple[str, ...]
    intermediate_values: Mapping[str, object]
    output_value: object
    output_hash: str
    raw_manifest_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.node_type, CalculationNodeType) or
                not isinstance(self.formula_version, str) or not self.formula_version.strip() or
                not isinstance(self.parameter_set_id, str) or not self.parameter_set_id.strip() or
                len(set(self.input_ids)) != len(self.input_ids) or
                any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                    for item in self.input_ids) or
                not re.fullmatch(r"[0-9a-f]{64}", self.node_id) or
                not re.fullmatch(r"[0-9a-f]{64}", self.output_hash) or
                (self.raw_manifest_id is not None and
                 not re.fullmatch(r"[0-9a-f]{64}", self.raw_manifest_id))):
            raise InvariantViolation("CALCULATION_NODE_CONTRACT_INVALID")
        intermediate = _jsonable(dict(self.intermediate_values))
        if not intermediate:
            raise InvariantViolation("CALCULATION_INTERMEDIATE_VALUES_MISSING")
        output = _jsonable(self.output_value)
        if self.node_type is CalculationNodeType.RAW_DATA:
            if self.input_ids or not self.raw_manifest_id:
                raise InvariantViolation("CALCULATION_RAW_NODE_INVALID")
        elif not self.input_ids or self.raw_manifest_id is not None:
            raise InvariantViolation("CALCULATION_DERIVED_NODE_INVALID")
        if digest(canonical(output)) != self.output_hash:
            raise InvariantViolation("CALCULATION_OUTPUT_HASH_INVALID")
        identity = {
            "node_type": self.node_type.value,
            "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id,
            "input_ids": sorted(self.input_ids),
            "intermediate_values": intermediate,
            "output_value": output,
            "output_hash": self.output_hash,
            "raw_manifest_id": self.raw_manifest_id,
        }
        if digest(canonical(identity)) != self.node_id:
            raise InvariantViolation("CALCULATION_NODE_ID_INVALID")
        object.__setattr__(self, "input_ids", tuple(sorted(self.input_ids)))
        object.__setattr__(self, "intermediate_values", _freeze(intermediate))
        object.__setattr__(self, "output_value", _freeze(output))

    @classmethod
    def create(cls, *, node_type: CalculationNodeType, formula_version: str,
               parameter_set_id: str, input_ids: tuple[str, ...],
               intermediate_values: Mapping[str, object], output_value: object,
               raw_manifest_id: str | None = None) -> "CalculationNode":
        intermediate = _jsonable(dict(intermediate_values))
        output = _jsonable(output_value)
        output_hash = digest(canonical(output))
        identity = {
            "node_type": node_type.value,
            "formula_version": formula_version,
            "parameter_set_id": parameter_set_id,
            "input_ids": sorted(input_ids),
            "intermediate_values": intermediate,
            "output_value": output,
            "output_hash": output_hash,
            "raw_manifest_id": raw_manifest_id,
        }
        return cls(digest(canonical(identity)), node_type, formula_version, parameter_set_id,
                   input_ids, intermediate, output, output_hash, raw_manifest_id)

    def payload(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "formula_version": self.formula_version,
            "parameter_set_id": self.parameter_set_id,
            "input_ids": list(self.input_ids),
            "intermediate_values": _jsonable(self.intermediate_values),
            "output_value": _jsonable(self.output_value),
            "output_hash": self.output_hash,
            "raw_manifest_id": self.raw_manifest_id,
        }

    @classmethod
    def from_raw_manifest(cls, store: ImmutableDatasetStore, manifest_id: str, *,
                          parameter_set_id: str) -> "CalculationNode":
        try:
            manifest, body = store.read(manifest_id)
        except (FileNotFoundError, ValueError) as exc:
            raise DataQualityError("CALCULATION_RAW_MANIFEST_UNVERIFIED") from exc
        if manifest.layer != "bronze":
            raise DataQualityError("CALCULATION_RAW_MANIFEST_NOT_BRONZE")
        return cls.create(
            node_type=CalculationNodeType.RAW_DATA,
            formula_version=f"raw-schema:{manifest.schema_version}",
            parameter_set_id=parameter_set_id,
            input_ids=(),
            intermediate_values={"dataset": manifest.dataset, "source": manifest.source},
            output_value=body,
            raw_manifest_id=manifest.manifest_id,
        )


@dataclass(frozen=True, slots=True)
class CalculationLineageGraph:
    final_node_id: str
    nodes: Mapping[str, CalculationNode]

    def __post_init__(self) -> None:
        values = dict(self.nodes)
        if (not values or set(values) != {node.node_id for node in values.values()} or
                self.final_node_id not in values or
                values[self.final_node_id].node_type is not CalculationNodeType.FINAL_ESTIMATE):
            raise InvariantViolation("CALCULATION_GRAPH_CONTRACT_INVALID")
        for node in values.values():
            for parent_id in node.input_ids:
                parent = values.get(parent_id)
                if parent is None:
                    raise InvariantViolation("CALCULATION_PARENT_MISSING")
                if _RANK[parent.node_type] + 1 != _RANK[node.node_type]:
                    raise InvariantViolation("CALCULATION_LAYER_ORDER_INVALID")
        object.__setattr__(self, "nodes", MappingProxyType(values))
        traced = self.trace()
        if {node.node_type for node in traced} != set(CalculationNodeType):
            raise InvariantViolation("CALCULATION_LINEAGE_INCOMPLETE")

    def trace(self) -> tuple[CalculationNode, ...]:
        visited: set[str] = set()
        ordered: list[CalculationNode] = []

        def visit(identifier: str) -> None:
            if identifier in visited:
                return
            node = self.nodes[identifier]
            for parent_id in node.input_ids:
                visit(parent_id)
            visited.add(identifier)
            ordered.append(node)

        visit(self.final_node_id)
        return tuple(ordered)

    def verify_raw_manifests(self, store: ImmutableDatasetStore) -> None:
        for node in self.trace():
            if node.node_type is not CalculationNodeType.RAW_DATA:
                continue
            try:
                manifest, body = store.read(node.raw_manifest_id or "")
            except (FileNotFoundError, ValueError) as exc:
                raise DataQualityError("CALCULATION_RAW_MANIFEST_UNVERIFIED") from exc
            if (manifest.layer != "bronze" or manifest.content_sha256 != node.output_hash or
                    _jsonable(body) != node.output_value):
                raise DataQualityError("CALCULATION_RAW_MANIFEST_CONFLICT")

    def payload(self) -> dict[str, object]:
        return {
            "final_node_id": self.final_node_id,
            "nodes": [self.nodes[key].payload() for key in sorted(self.nodes)],
            "graph_hash": self.graph_hash,
        }

    @property
    def graph_hash(self) -> str:
        body = {"final_node_id": self.final_node_id,
                "nodes": [self.nodes[key].payload() for key in sorted(self.nodes)]}
        return digest(canonical(body))

    def publish(self, store: ImmutableDatasetStore) -> str:
        self.verify_raw_manifests(store)
        identifier = store.catalog("calculation-lineage", self.payload())
        if identifier != digest(canonical(self.payload())):
            raise InvariantViolation("CALCULATION_GRAPH_PUBLICATION_MISMATCH")
        return identifier
