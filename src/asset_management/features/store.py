"""Deterministic feature evaluation and immutable gold publication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import re
from typing import Callable, Mapping

from asset_management.data.immutable import ImmutableDatasetStore, StoredDatasetManifest, canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityGate, QualityStatus

from .models import FeatureContext, FeatureDefinition, FeatureInput, FeatureSnapshot, FeatureValue, utc
from .registry import FeatureRegistry


@dataclass(frozen=True)
class FeatureRunResult:
    status: str
    reason_code: str
    snapshot: FeatureSnapshot
    manifest_id: str | None


class FeatureStore:
    def __init__(self, store: ImmutableDatasetStore, registry: FeatureRegistry):
        self.store = store
        self.registry = registry

    def evaluate(self, *, feature_id: str, context: FeatureContext,
                 inputs: Mapping[str, FeatureInput], transform: Callable[..., Decimal],
                 parameters: Mapping[str, object] | None = None,
                 quality_gate: QualityGate) -> FeatureRunResult:
        definition = self.registry.get(feature_id)
        params = dict(parameters or {})
        missing = set(definition.input_fields) - set(inputs)
        reason: str | None = None
        value: Decimal | None = None
        quality = QualityStatus.VALID
        if transform.__name__ != definition.transformation:
            quality, reason = QualityStatus.BLOCKED, "FEATURE_TRANSFORMATION_MISMATCH"
        elif quality_gate.action != "ALLOW":
            quality = QualityStatus.BLOCKED
            reason = quality_gate.reason_codes[0] if quality_gate.reason_codes else "DATA_QUALITY_BLOCKED"
        elif missing:
            quality, reason = QualityStatus.MISSING, "MISSING_HISTORY"
        elif any(item.available_at > context.information_cutoff or item.event_time > context.as_of
                 for item in inputs.values()):
            quality, reason = QualityStatus.QUARANTINED, "FEATURE_INPUT_AFTER_CUTOFF"
        else:
            try:
                value = transform(*(inputs[name].values["value"] for name in definition.input_fields), **params)
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise DataQualityError("FEATURE_OUTPUT_INVALID")
            except (DataQualityError, KeyError, TypeError, ValueError) as exc:
                reason = str(exc) or type(exc).__name__
                quality = QualityStatus.MISSING if reason == "MISSING_HISTORY" else QualityStatus.QUARANTINED
                value = None
        identity = {
            "definition": asdict(definition), "instrument_id": context.instrument_id,
            "as_of": utc(context.as_of), "information_cutoff": utc(context.information_cutoff),
            "inputs": {name: {"values": _jsonable(dict(item.values)), "available_at": utc(item.available_at),
                              "event_time": utc(item.event_time)} for name, item in sorted(inputs.items())},
            "parameters": _jsonable(params), "input_manifest_ids": sorted(context.input_manifest_ids),
            "universe_manifest_id": context.universe_manifest_id,
            "parameter_set_id": context.parameter_set_id,
            "parent_state_id": context.parent_state_id, "code_revision": context.code_revision,
            "validity": context.validity.payload(),
            "quality_gate": _jsonable(asdict(quality_gate)),
        }
        run_id = digest(canonical(identity))
        snapshot = FeatureSnapshot(run_id, context.instrument_id, feature_id, utc(context.as_of),
                                   utc(context.information_cutoff), str(value) if value is not None else None,
                                   str(quality), tuple(sorted(context.input_manifest_ids)),
                                   context.parameter_set_id, context.parent_state_id, context.code_revision,
                                   context.validity)
        if quality is not QualityStatus.VALID:
            return FeatureRunResult("NO_TRADE", reason or "FEATURE_QUALITY_BLOCKED", snapshot, None)
        manifest = self._publish(definition, context, snapshot)
        return FeatureRunResult("READY", "OK", snapshot, manifest.manifest_id)

    def _publish(self, definition: FeatureDefinition, context: FeatureContext,
                 snapshot: FeatureSnapshot) -> StoredDatasetManifest:
        if not re.fullmatch(r"git:[0-9a-f]{7,40}", context.code_revision):
            raise DataQualityError("FEATURE_CODE_REVISION_INVALID")
        parents = tuple(sorted(set(context.input_manifest_ids)))
        if context.universe_manifest_id not in parents:
            raise DataQualityError("HISTORICAL_UNIVERSE_LINEAGE_MISSING")
        manifests = [self.store.read(identifier)[0] for identifier in parents]
        if any(item.layer != "silver" or item.quality_status != "VALID" for item in manifests):
            raise DataQualityError("FEATURE_INPUT_MANIFEST_INVALID")
        if any(item.available_at > snapshot.information_cutoff for item in manifests):
            raise DataQualityError("FEATURE_MANIFEST_AFTER_CUTOFF")
        universe_manifest = next(item for item in manifests
                                 if item.manifest_id == context.universe_manifest_id)
        if universe_manifest.dataset != "historical-universe":
            raise DataQualityError("HISTORICAL_UNIVERSE_MANIFEST_INVALID")
        sources = {item.source for item in manifests}
        licenses = {item.license_tag for item in manifests}
        if len(sources) != 1 or len(licenses) != 1:
            raise DataQualityError("FEATURE_COMBINED_CONTRACT_REQUIRED")
        definition_id = self.store.catalog("feature-definitions", asdict(definition))
        body = {**asdict(snapshot), "input_manifest_ids": list(snapshot.input_manifest_ids),
                "validity": snapshot.validity.payload(),
                "feature_definition_catalog_id": definition_id}
        latest_provider = max(item.provider_timestamp for item in manifests)
        return self.store.write(
            body, layer="gold", source=manifests[0].source, dataset="feature-snapshot",
            schema_version="phase11-feature-snapshot-v1",
            retrieved_at=datetime.fromisoformat(snapshot.as_of),
            available_at=datetime.fromisoformat(snapshot.as_of),
            provider_timestamp=datetime.fromisoformat(latest_provider),
            license_tag=manifests[0].license_tag, code_revision=context.code_revision,
            request_hash=digest(canonical(identity_for_request(snapshot, definition_id))),
            parent_manifest_ids=parents,
        )


def identity_for_request(snapshot: FeatureSnapshot, definition_id: str) -> dict:
    return {"feature_run_id": snapshot.feature_run_id, "feature_definition_catalog_id": definition_id,
            "input_manifest_ids": list(snapshot.input_manifest_ids)}


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return utc(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
