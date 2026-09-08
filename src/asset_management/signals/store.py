"""Point-in-time signal evaluation from immutable Feature snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest, utc
from asset_management.domain.errors import DataQualityError, InvariantViolation
from asset_management.features.models import FeatureSnapshot
from asset_management.quality.models import QualityGate, QualityStatus

from .models import SignalContext, SignalFeatureInput, SignalSnapshot
from .registry import SignalRegistry


SignalTransform = Callable[[Mapping[str, Mapping[str, Decimal]]], Mapping[str, Decimal]]


@dataclass(frozen=True, slots=True)
class SignalRunResult:
    status: str
    reason_code: str
    snapshot: SignalSnapshot | None
    catalog_id: str | None


class SignalStore:
    def __init__(self, store: ImmutableDatasetStore, registry: SignalRegistry) -> None:
        self.store = store
        self.registry = registry

    def evaluate(self, *, signal_id: str, version: str, context: SignalContext,
                 feature_inputs: Mapping[str, Mapping[str, SignalFeatureInput]],
                 transform: SignalTransform, quality_gate: QualityGate) -> SignalRunResult:
        definition = self.registry.get(signal_id, version)
        if definition.validity.valid_until <= context.as_of:
            return self._abstain("SIGNAL_EXPIRED")
        if len(context.history_feature_manifest_ids) < definition.minimum_history:
            return self._abstain("SIGNAL_MINIMUM_HISTORY_MISSING")
        try:
            self._verify_context_manifests(context)
            eligible, source_manifest_ids = self._eligible_inputs(definition, context, feature_inputs)
        except (InvariantViolation, DataQualityError) as exc:
            return self._abstain(str(exc))
        if quality_gate.action != "ALLOW":
            return self._abstain(quality_gate.reason_codes[0] if quality_gate.reason_codes
                                 else "SIGNAL_QUALITY_BLOCKED")
        if transform.__name__ != definition.transform_rule:
            return self._abstain("SIGNAL_TRANSFORMATION_MISMATCH")
        coverage = Decimal(len(eligible)) / Decimal(len(context.historical_universe))
        if coverage < definition.minimum_coverage:
            return self._abstain("SIGNAL_COVERAGE_INSUFFICIENT")
        try:
            transformed = dict(transform(eligible))
        except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
            return self._abstain(str(exc) or "SIGNAL_TRANSFORM_FAILED")
        if set(transformed) != set(eligible):
            return self._abstain("SIGNAL_TRANSFORM_OUTPUT_UNIVERSE_INVALID")
        if any(not isinstance(value, Decimal) or not value.is_finite()
               for value in transformed.values()):
            return self._abstain("SIGNAL_TRANSFORM_OUTPUT_INVALID")
        values = {instrument: str(transformed[instrument]) if instrument in transformed else None
                  for instrument in context.historical_universe}
        output_hash = digest(canonical(values))
        identity = {
            "definition": definition.payload(), "as_of": utc(context.as_of),
            "information_cutoff": utc(context.information_cutoff),
            "universe_manifest_id": context.universe_manifest_id,
            "historical_universe": list(context.historical_universe),
            "history_feature_manifest_ids": list(context.history_feature_manifest_ids),
            "source_feature_manifest_ids": list(source_manifest_ids), "values": values,
            "output_hash": output_hash, "code_revision": context.code_revision,
        }
        run_id = digest(canonical(identity))
        quality = QualityStatus.VALID if coverage == Decimal(1) else QualityStatus.ESTIMATED
        snapshot = SignalSnapshot(
            run_id, definition.signal_id, definition.version, "SIGNAL_VALUE", utc(context.as_of),
            utc(context.information_cutoff), values, quality.value, str(coverage), source_manifest_ids,
            context.history_feature_manifest_ids,
            context.universe_manifest_id, definition.formula_version, definition.parameter_set_id,
            context.code_revision, definition.validity, output_hash,
        )
        catalog_id = self.store.catalog("signal-snapshots", snapshot.payload())
        return SignalRunResult("READY", "OK", snapshot, catalog_id)

    @staticmethod
    def _abstain(reason_code: str) -> SignalRunResult:
        return SignalRunResult("ABSTAIN", reason_code, None, None)

    def _eligible_inputs(self, definition, context: SignalContext,
                         feature_inputs: Mapping[str, Mapping[str, SignalFeatureInput]],
                         ) -> tuple[dict[str, dict[str, Decimal]], tuple[str, ...]]:
        if set(feature_inputs) != set(context.historical_universe):
            raise DataQualityError("SIGNAL_UNIVERSE_PIT_INVALID")
        eligible: dict[str, dict[str, Decimal]] = {}
        source_manifests: set[str] = set()
        for instrument in context.historical_universe:
            inputs = feature_inputs[instrument]
            if set(inputs) != set(definition.source_feature_ids):
                raise DataQualityError("SIGNAL_SOURCE_FEATURES_INCOMPLETE")
            row: dict[str, Decimal] = {}
            complete = True
            for feature_id in definition.source_feature_ids:
                item = inputs[feature_id]
                if not isinstance(item, SignalFeatureInput):
                    raise InvariantViolation("SIGNAL_FEATURE_INPUT_INVALID")
                snapshot = item.snapshot
                self._validate_feature_snapshot(
                    snapshot, instrument, feature_id, context, definition.validity)
                self._verify_feature_manifest(item, context)
                source_manifests.add(item.manifest_id)
                if snapshot.quality_status != QualityStatus.VALID.value or snapshot.value is None:
                    complete = False
                    continue
                try:
                    value = Decimal(snapshot.value)
                except Exception:
                    raise DataQualityError("SIGNAL_FEATURE_VALUE_INVALID") from None
                if not value.is_finite():
                    raise DataQualityError("SIGNAL_FEATURE_VALUE_INVALID")
                row[feature_id] = value
            if complete:
                eligible[instrument] = row
        return eligible, tuple(sorted(source_manifests))

    @staticmethod
    def _validate_feature_snapshot(snapshot: FeatureSnapshot, instrument: str, feature_id: str,
                                   context: SignalContext, validity) -> None:
        if snapshot.instrument_id != instrument or snapshot.feature_id != feature_id:
            raise DataQualityError("SIGNAL_FEATURE_IDENTITY_INVALID")
        try:
            as_of = datetime.fromisoformat(snapshot.as_of)
            cutoff = datetime.fromisoformat(snapshot.information_cutoff)
        except ValueError:
            raise DataQualityError("SIGNAL_FEATURE_CONTEXT_INVALID") from None
        if (as_of != context.as_of or cutoff != context.information_cutoff or
                context.universe_manifest_id not in snapshot.input_manifest_ids or
                snapshot.validity.forecast_horizon != validity.forecast_horizon or
                snapshot.validity.holding_horizon != validity.holding_horizon or
                snapshot.validity.valid_until <= context.as_of):
            raise DataQualityError("SIGNAL_FEATURE_CONTEXT_INVALID")

    def _verify_context_manifests(self, context: SignalContext) -> None:
        try:
            universe, universe_rows = self.store.read(context.universe_manifest_id)
            history = [self.store.read(identifier)
                       for identifier in context.history_feature_manifest_ids]
        except (FileNotFoundError, ValueError):
            raise DataQualityError("SIGNAL_CONTEXT_MANIFEST_UNVERIFIED") from None
        try:
            members = tuple(sorted(row["instrument_id"] for row in universe_rows))
            history_valid = all(
                manifest.layer == "gold" and manifest.dataset == "feature-snapshot" and
                datetime.fromisoformat(manifest.available_at) <= context.information_cutoff and
                isinstance(body, dict) and
                context.universe_manifest_id in body.get("input_manifest_ids", ()) and
                datetime.fromisoformat(body["as_of"]) <= context.as_of and
                datetime.fromisoformat(body["information_cutoff"]) <= context.information_cutoff
                for manifest, body in history
            )
        except (KeyError, TypeError, ValueError):
            raise DataQualityError("SIGNAL_CONTEXT_MANIFEST_INVALID") from None
        if (len(set(members)) != len(members) or members != context.historical_universe or
                universe.layer != "silver" or universe.dataset != "historical-universe" or
                datetime.fromisoformat(universe.available_at) > context.information_cutoff or
                not history_valid):
            raise DataQualityError("SIGNAL_CONTEXT_MANIFEST_INVALID")

    def _verify_feature_manifest(self, item: SignalFeatureInput, context: SignalContext) -> None:
        try:
            manifest, body = self.store.read(item.manifest_id)
        except (FileNotFoundError, ValueError):
            raise DataQualityError("SIGNAL_FEATURE_MANIFEST_UNVERIFIED") from None
        if (manifest.layer != "gold" or manifest.dataset != "feature-snapshot" or
                datetime.fromisoformat(manifest.available_at) > context.information_cutoff or
                not isinstance(body, dict) or body.get("feature_run_id") != item.snapshot.feature_run_id or
                body.get("instrument_id") != item.snapshot.instrument_id or
                body.get("feature_id") != item.snapshot.feature_id or
                body.get("as_of") != item.snapshot.as_of or
                body.get("information_cutoff") != item.snapshot.information_cutoff or
                body.get("value") != item.snapshot.value or
                context.universe_manifest_id not in body.get("input_manifest_ids", ())):
            raise DataQualityError("SIGNAL_FEATURE_MANIFEST_INVALID")
