"""Canonical PIT construction of MarketState from immutable Feature snapshots.

The builder is deliberately representation-only. It does not infer regimes, select assets,
or create Forecast/Risk authority. A component binding is an explicit, content-addressed
contract; the initial built-in foundation spec leaves every economically unresolved dimension
unavailable instead of inventing proxies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.features.registry import FeatureRegistry
from asset_management.features.store import identity_for_request
from asset_management.quality.models import QualityStatus

from .market import MARKET_COMPONENTS, MarketStateEngine
from .models import StateComponent, StateFeatureInput, StateNormalization, StatePolicy, StateSnapshot


_REASON = re.compile(r"[A-Z][A-Z0-9_]*")
_SEMANTIC = re.compile(r"[A-Z][A-Z0-9_]*")


@dataclass(frozen=True, slots=True)
class MarketStateComponentSpec:
    component_id: str
    semantic_type: str
    unit: str
    formula_version: str
    source_feature_id: str | None = None
    source_instrument_id: str | None = None
    normalization: StateNormalization = StateNormalization.RAW
    unavailable_reason: str = "UNMAPPED_COMPONENT"
    missing_reason: str = "SOURCE_FEATURE_UNAVAILABLE"

    def __post_init__(self) -> None:
        if (self.component_id not in MARKET_COMPONENTS or
                not _SEMANTIC.fullmatch(self.semantic_type) or
                not self.unit.strip() or not self.formula_version.strip() or
                not isinstance(self.normalization, StateNormalization) or
                not _REASON.fullmatch(self.unavailable_reason) or
                not _REASON.fullmatch(self.missing_reason)):
            raise ValueError("MARKET_STATE_COMPONENT_SPEC_INVALID")
        if self.source_feature_id is None:
            if self.source_instrument_id is not None:
                raise ValueError("MARKET_STATE_COMPONENT_SPEC_INVALID")
        elif (not self.source_feature_id.startswith("market.") or
              not isinstance(self.source_instrument_id, str) or
              not self.source_instrument_id.strip() or
              self.normalization is not StateNormalization.RAW):
            # AMA-176 supports only identity binding of an already-defined Feature value.
            # Normalization/composition belongs to a future explicitly reviewed formula.
            raise ValueError("MARKET_STATE_DIRECT_BINDING_INVALID")

    @property
    def mode(self) -> str:
        return "UNAVAILABLE" if self.source_feature_id is None else "DIRECT_FEATURE"

    def payload(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "semantic_type": self.semantic_type,
            "unit": self.unit,
            "formula_version": self.formula_version,
            "source_feature_id": self.source_feature_id,
            "source_instrument_id": self.source_instrument_id,
            "normalization": self.normalization.value,
            "unavailable_reason": self.unavailable_reason,
            "missing_reason": self.missing_reason,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class MarketStateSpec:
    spec_id: str
    version: str
    components: tuple[MarketStateComponentSpec, ...]

    def __post_init__(self) -> None:
        if not self.spec_id.strip() or not self.version.strip():
            raise ValueError("MARKET_STATE_SPEC_INVALID")
        ids = tuple(item.component_id for item in self.components)
        if len(ids) != len(set(ids)) or set(ids) != set(MARKET_COMPONENTS):
            raise ValueError("MARKET_STATE_SPEC_COMPONENTS_INVALID")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": "market-state-spec-v1",
            "spec_id": self.spec_id,
            "version": self.version,
            "components": [item.payload() for item in sorted(
                self.components, key=lambda item: item.component_id)],
        }

    @property
    def spec_hash(self) -> str:
        return digest(canonical(self.payload()))

    def component(self, component_id: str) -> MarketStateComponentSpec:
        for item in self.components:
            if item.component_id == component_id:
                return item
        raise ValueError("MARKET_STATE_SPEC_COMPONENT_MISSING")


@dataclass(frozen=True, slots=True)
class MarketStateBuildResult:
    snapshot: StateSnapshot
    spec_catalog_id: str


def foundation_market_state_spec() -> MarketStateSpec:
    """Current approved foundation: preserve unresolved dimensions as unavailable.

    Phase-0 found no reviewed Feature->State economic binding on master for any of the nine
    dimensions. Existing Feature names remain candidate inputs, not authority to promote a
    value into MarketState. Adding a direct binding therefore requires a code-reviewed spec
    change rather than an implicit name match.
    """
    return MarketStateSpec(
        spec_id="market-state-foundation",
        version="1",
        components=tuple(
            MarketStateComponentSpec(
                component_id=name,
                semantic_type=f"{name.upper()}_STATE",
                unit="unknown",
                formula_version=f"{name}.unavailable@1",
                unavailable_reason="UNMAPPED_COMPONENT",
            )
            for name in MARKET_COMPONENTS
        ),
    )


class MarketStateBuilder:
    def __init__(self, store: ImmutableDatasetStore, feature_registry: FeatureRegistry) -> None:
        self.store = store
        self.feature_registry = feature_registry

    def build(self, *, spec: MarketStateSpec, as_of: datetime,
              information_cutoff: datetime, policy: StatePolicy, code_revision: str,
              feature_inputs: Mapping[str, StateFeatureInput] | None = None
              ) -> MarketStateBuildResult:
        if not isinstance(spec, MarketStateSpec):
            raise ValueError("MARKET_STATE_SPEC_REQUIRED")
        if as_of.tzinfo is None or as_of.utcoffset() is None or \
                information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
            raise ValueError("MARKET_STATE_TIME_NOT_AWARE")
        as_of_utc = as_of.astimezone(timezone.utc)
        cutoff_utc = information_cutoff.astimezone(timezone.utc)
        if cutoff_utc > as_of_utc:
            raise DataQualityError("MARKET_STATE_CUTOFF_AFTER_AS_OF")
        inputs = {} if feature_inputs is None else dict(feature_inputs)
        if any(not isinstance(key, str) or not isinstance(item, StateFeatureInput)
               for key, item in inputs.items()):
            raise DataQualityError("MARKET_STATE_FEATURE_INPUT_INVALID")
        spec_catalog_id = self.store.catalog("market-state-specs", spec.payload())
        if spec_catalog_id != spec.spec_hash:
            raise DataQualityError("MARKET_STATE_SPEC_IDENTITY_INVALID")

        active = {item.component_id for item in spec.components if item.source_feature_id is not None}
        if set(inputs) - active:
            raise DataQualityError("MARKET_STATE_UNBOUND_FEATURE_INPUT")
        for component_spec in spec.components:
            if component_spec.source_feature_id is not None:
                try:
                    self.feature_registry.get(component_spec.source_feature_id)
                except ValueError:
                    raise DataQualityError("MARKET_STATE_FEATURE_DEFINITION_MISSING") from None

        components: dict[str, StateComponent] = {}
        for component_id in MARKET_COMPONENTS:
            component_spec = spec.component(component_id)
            item = inputs.get(component_id)
            if component_spec.source_feature_id is None:
                components[component_id] = self._unavailable(
                    component_spec, spec, spec_catalog_id, as_of_utc, cutoff_utc,
                    component_spec.unavailable_reason,
                )
                continue
            if item is None:
                components[component_id] = self._unavailable(
                    component_spec, spec, spec_catalog_id, as_of_utc, cutoff_utc,
                    component_spec.missing_reason,
                )
                continue
            self._verify_feature_input(component_spec, item, as_of_utc, cutoff_utc)
            snapshot = item.snapshot
            try:
                value = Decimal(snapshot.value) if snapshot.value is not None else None
            except (InvalidOperation, TypeError, ValueError):
                raise DataQualityError("MARKET_STATE_FEATURE_VALUE_INVALID") from None
            if value is None or not value.is_finite() or snapshot.quality_status != QualityStatus.VALID.value:
                raise DataQualityError("MARKET_STATE_FEATURE_VALUE_INVALID")
            feature_as_of = datetime.fromisoformat(snapshot.as_of).astimezone(timezone.utc)
            freshness_seconds = int((as_of_utc - feature_as_of).total_seconds())
            if freshness_seconds < 0:
                raise DataQualityError("MARKET_STATE_FEATURE_AFTER_AS_OF")
            components[component_id] = StateComponent(
                value=value,
                component_id=component_id,
                semantic_type=component_spec.semantic_type,
                unit=component_spec.unit,
                normalization=component_spec.normalization,
                as_of=as_of_utc.isoformat(),
                information_cutoff=cutoff_utc.isoformat(),
                confidence=Decimal(1),
                quality_status=QualityStatus.VALID,
                freshness_seconds=freshness_seconds,
                input_evidence_ids=tuple(sorted((spec_catalog_id, item.manifest_id))),
                parameter_set_id=spec.spec_hash,
                formula_version=component_spec.formula_version,
                input_features=(item,),
            )

        state = MarketStateEngine().build(
            as_of=as_of_utc,
            information_cutoff=cutoff_utc,
            components=components,
            policy=policy,
            code_revision=code_revision,
        )
        return MarketStateBuildResult(state, spec_catalog_id)

    def _unavailable(self, component_spec: MarketStateComponentSpec, spec: MarketStateSpec,
                     spec_catalog_id: str, as_of: datetime, cutoff: datetime,
                     reason_code: str) -> StateComponent:
        return StateComponent(
            value=None,
            component_id=component_spec.component_id,
            semantic_type=component_spec.semantic_type,
            unit=component_spec.unit,
            normalization=component_spec.normalization,
            as_of=as_of.isoformat(),
            information_cutoff=cutoff.isoformat(),
            confidence=Decimal(0),
            quality_status=QualityStatus.MISSING,
            freshness_seconds=0,
            input_evidence_ids=(spec_catalog_id,),
            parameter_set_id=spec.spec_hash,
            formula_version=component_spec.formula_version,
            input_features=(),
            reason_code=reason_code,
        )

    def _verify_feature_input(self, component_spec: MarketStateComponentSpec,
                              item: StateFeatureInput, as_of: datetime,
                              cutoff: datetime) -> None:
        snapshot = item.snapshot
        if (snapshot.feature_id != component_spec.source_feature_id or
                snapshot.instrument_id != component_spec.source_instrument_id):
            raise DataQualityError("MARKET_STATE_FEATURE_IDENTITY_INVALID")
        try:
            feature_as_of = datetime.fromisoformat(snapshot.as_of).astimezone(timezone.utc)
            feature_cutoff = datetime.fromisoformat(snapshot.information_cutoff).astimezone(timezone.utc)
        except (TypeError, ValueError):
            raise DataQualityError("MARKET_STATE_FEATURE_CONTEXT_INVALID") from None
        if (feature_as_of > as_of or feature_cutoff > cutoff or
                snapshot.validity.valid_until <= as_of):
            raise DataQualityError("MARKET_STATE_FEATURE_CONTEXT_INVALID")
        if snapshot.input_manifest_ids != tuple(sorted(set(snapshot.input_manifest_ids))):
            raise DataQualityError("MARKET_STATE_FEATURE_LINEAGE_INVALID")
        try:
            definition = self.feature_registry.get(snapshot.feature_id)
            manifest, body = self.store.read(item.manifest_id)
            parent_manifests = [self.store.read(identifier)[0]
                                for identifier in snapshot.input_manifest_ids]
        except (FileNotFoundError, ValueError):
            raise DataQualityError("MARKET_STATE_FEATURE_MANIFEST_UNVERIFIED") from None
        if not parent_manifests:
            raise DataQualityError("MARKET_STATE_FEATURE_MANIFEST_INVALID")

        definition_id = digest(canonical(asdict(definition)))
        definition_path = self.store.layout.resolve(
            "catalog", f"feature-definitions/{definition_id}.json")
        try:
            definition_bytes = definition_path.read_bytes()
        except OSError:
            raise DataQualityError("MARKET_STATE_FEATURE_DEFINITION_UNVERIFIED") from None
        if definition_bytes != canonical(asdict(definition)):
            raise DataQualityError("MARKET_STATE_FEATURE_DEFINITION_INVALID")

        expected = {
            "feature_run_id": snapshot.feature_run_id,
            "instrument_id": snapshot.instrument_id,
            "feature_id": snapshot.feature_id,
            "as_of": snapshot.as_of,
            "information_cutoff": snapshot.information_cutoff,
            "value": snapshot.value,
            "quality_status": snapshot.quality_status,
            "input_manifest_ids": list(snapshot.input_manifest_ids),
            "parameter_set_id": snapshot.parameter_set_id,
            "parent_state_id": snapshot.parent_state_id,
            "code_revision": snapshot.code_revision,
            "validity": snapshot.validity.payload(),
            "feature_definition_catalog_id": definition_id,
        }
        try:
            manifest_retrieved = datetime.fromisoformat(manifest.retrieved_at).astimezone(timezone.utc)
            manifest_available = datetime.fromisoformat(manifest.available_at).astimezone(timezone.utc)
            manifest_provider = datetime.fromisoformat(manifest.provider_timestamp).astimezone(timezone.utc)
            parent_available = tuple(
                datetime.fromisoformat(parent.available_at).astimezone(timezone.utc)
                for parent in parent_manifests
            )
            parent_provider = tuple(
                datetime.fromisoformat(parent.provider_timestamp).astimezone(timezone.utc)
                for parent in parent_manifests
            )
        except (TypeError, ValueError):
            raise DataQualityError("MARKET_STATE_FEATURE_MANIFEST_INVALID") from None
        parent_sources = {parent.source for parent in parent_manifests}
        parent_licenses = {parent.license_tag for parent in parent_manifests}
        expected_request_hash = digest(canonical(identity_for_request(snapshot, definition_id)))
        if (manifest.layer != "gold" or manifest.dataset != "feature-snapshot" or
                manifest.schema_version != "phase11-feature-snapshot-v1" or
                manifest.quality_status != "VALID" or
                manifest_retrieved != feature_as_of or manifest_available != feature_as_of or
                manifest_available > cutoff or
                manifest.code_revision != snapshot.code_revision or
                manifest.request_hash != expected_request_hash or
                tuple(sorted(manifest.parent_manifest_ids)) != snapshot.input_manifest_ids or
                any(parent.layer != "silver" or parent.quality_status != "VALID"
                    for parent in parent_manifests) or
                any(available > feature_cutoff for available in parent_available) or
                not any(parent.dataset == "historical-universe" for parent in parent_manifests) or
                len(parent_sources) != 1 or len(parent_licenses) != 1 or
                manifest.source != next(iter(parent_sources)) or
                manifest.license_tag != next(iter(parent_licenses)) or
                manifest_provider != max(parent_provider) or
                not isinstance(body, dict) or body != expected):
            raise DataQualityError("MARKET_STATE_FEATURE_MANIFEST_INVALID")
