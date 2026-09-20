"""Shared deterministic construction without collapsing component state."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import (OperationalState, StateComponent, StateNormalization, StatePolicy,
                     StateSnapshot, StateType, is_blocking, state_identity, worst_quality)


_UNAVAILABLE_WITHOUT_FEATURE = {
    QualityStatus.MISSING,
    QualityStatus.PRIMARY_PENDING,
    QualityStatus.BLOCKED,
    QualityStatus.QUARANTINED,
}


class StateEngine:
    def __init__(self, *, state_type: StateType, component_names: tuple[str, ...],
                 allow_legacy_cutoff_inference: bool = False):
        if not component_names or len(component_names) != len(set(component_names)):
            raise ValueError("STATE_COMPONENT_CONTRACT_INVALID")
        self.state_type = state_type
        self.component_names = component_names
        self.allow_legacy_cutoff_inference = allow_legacy_cutoff_inference

    def build(self, *, as_of: datetime, components: Mapping[str, StateComponent],
              policy: StatePolicy, code_revision: str,
              information_cutoff: datetime | None = None) -> StateSnapshot:
        if set(components) != set(self.component_names):
            raise DataQualityError("STATE_COMPONENTS_INCOMPLETE")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("STATE_TIME_NOT_AWARE")
        as_of_utc = as_of.astimezone(timezone.utc)
        if information_cutoff is None:
            if not self.allow_legacy_cutoff_inference:
                raise DataQualityError("STATE_INFORMATION_CUTOFF_REQUIRED")
            try:
                component_cutoffs = {
                    datetime.fromisoformat(component.information_cutoff).astimezone(timezone.utc)
                    for component in components.values()
                }
            except (TypeError, ValueError):
                raise DataQualityError("STATE_LEGACY_CUTOFF_UNAVAILABLE") from None
            if len(component_cutoffs) != 1:
                raise DataQualityError("STATE_LEGACY_CUTOFF_AMBIGUOUS")
            cutoff_utc = next(iter(component_cutoffs))
        else:
            if information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
                raise ValueError("STATE_TIME_NOT_AWARE")
            cutoff_utc = information_cutoff.astimezone(timezone.utc)
        if cutoff_utc > as_of_utc:
            raise DataQualityError("STATE_CUTOFF_AFTER_AS_OF")
        if not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
            raise DataQualityError("STATE_CODE_REVISION_INVALID")
        for name, component in components.items():
            if component.component_id != name:
                raise DataQualityError("STATE_COMPONENT_ID_MISMATCH")
            component_as_of = datetime.fromisoformat(component.as_of).astimezone(timezone.utc)
            component_cutoff = datetime.fromisoformat(component.information_cutoff).astimezone(timezone.utc)
            if component_as_of != as_of_utc or component_cutoff != cutoff_utc:
                raise DataQualityError("STATE_COMPONENT_CONTEXT_MISMATCH")

        if self.state_type in {StateType.MARKET, StateType.COMPANY}:
            if any(component.normalization in {StateNormalization.STRUCTURED,
                                               StateNormalization.CATEGORICAL}
                   for component in components.values()):
                raise DataQualityError("CONTINUOUS_STATE_NORMALIZATION_INVALID")
            for component in components.values():
                if component.input_features:
                    continue
                if (component.value is not None or
                        component.quality_status not in _UNAVAILABLE_WITHOUT_FEATURE or
                        component.reason_code is None or
                        component.confidence != Decimal(0)):
                    raise DataQualityError("STATE_COMPONENT_UNAVAILABLE_INVALID")
            if any(component.quality_status is QualityStatus.VALID and
                   (not isinstance(component.value, Decimal) or not component.value.is_finite())
                   for component in components.values()):
                raise DataQualityError("CONTINUOUS_STATE_VALUE_INVALID")
        if self.state_type is StateType.SYSTEM and any(
            component.normalization is not StateNormalization.CATEGORICAL
            for component in components.values()
        ):
            raise DataQualityError("SYSTEM_STATE_NORMALIZATION_INVALID")

        quality = worst_quality(components)
        confidence = min(component.confidence for component in components.values())
        freshness = max(component.freshness_seconds for component in components.values())
        operational = self._operational_state(components, quality, confidence, freshness, policy)
        multiplier = self._risk_multiplier(operational, policy)
        state_id = state_identity(
            state_type=self.state_type, as_of=as_of_utc, information_cutoff=cutoff_utc,
            components=components, policy=policy, code_revision=code_revision,
        )
        evidence_ids = tuple(sorted({item for component in components.values()
                                     for item in component.input_evidence_ids}))
        feature_inputs = tuple(item for component in components.values()
                               for item in component.input_features)
        feature_ids = tuple(sorted({item.snapshot.feature_id for item in feature_inputs}))
        feature_run_ids = tuple(sorted({item.snapshot.feature_run_id for item in feature_inputs}))
        feature_manifest_ids = tuple(sorted({item.manifest_id for item in feature_inputs}))
        data_manifest_ids = tuple(sorted({identifier for item in feature_inputs
                                          for identifier in item.snapshot.input_manifest_ids}))
        lineage_ids = tuple(sorted({component.calculation_lineage_id
                                    for component in components.values()
                                    if component.calculation_lineage_id is not None}))
        return StateSnapshot(
            state_id=state_id,
            state_type=self.state_type,
            as_of=as_of_utc.isoformat(),
            information_cutoff=cutoff_utc.isoformat(),
            components=dict(components),
            confidence=str(confidence),
            quality_status=quality,
            freshness=freshness,
            input_evidence_ids=evidence_ids,
            input_feature_ids=feature_ids,
            input_feature_run_ids=feature_run_ids,
            input_feature_manifest_ids=feature_manifest_ids,
            input_data_manifest_ids=data_manifest_ids,
            calculation_lineage_ids=lineage_ids,
            policy_version=policy.policy_version,
            code_revision=code_revision,
            operational_state=operational,
            risk_multiplier=str(multiplier),
        )

    def recompute_component(self, snapshot: StateSnapshot, *, component_name: str,
                            component: StateComponent, as_of: datetime, policy: StatePolicy,
                            code_revision: str, information_cutoff: datetime | None = None) -> StateSnapshot:
        if snapshot.state_type is not self.state_type or component_name not in self.component_names:
            raise DataQualityError("STATE_COMPONENT_CONTRACT_MISMATCH")
        values = dict(snapshot.components)
        values[component_name] = component
        return self.build(as_of=as_of, information_cutoff=information_cutoff,
                          components=values, policy=policy, code_revision=code_revision)

    def _operational_state(self, components: Mapping[str, StateComponent], quality: QualityStatus,
                           confidence: Decimal, freshness: int,
                           policy: StatePolicy) -> OperationalState:
        if self.state_type is StateType.SYSTEM:
            values = {str(component.value).upper() for component in components.values()}
            allowed = {"NORMAL", "DEGRADED", "STALE", "BLOCKED", "UNKNOWN", "HALTED",
                       "CAUTION", "REDUCED_RISK", "NO_NEW_TRADES"}
            if not values <= allowed:
                raise DataQualityError("SYSTEM_HEALTH_VALUE_INVALID")
            if values & {"HALTED", "BLOCKED"}:
                return OperationalState.HALTED
            if values & {"STALE", "UNKNOWN", "NO_NEW_TRADES"}:
                return OperationalState.NO_NEW_TRADES
        if is_blocking(quality) or freshness > policy.stale_after_seconds:
            return OperationalState.NO_NEW_TRADES
        if confidence < policy.minimum_confidence:
            return OperationalState.REDUCED_RISK
        if self.state_type is StateType.SYSTEM:
            values = {str(component.value).upper() for component in components.values()}
            if "REDUCED_RISK" in values:
                return OperationalState.REDUCED_RISK
            if values & {"DEGRADED", "CAUTION"}:
                return OperationalState.CAUTION
        if confidence < policy.caution_confidence or quality is not QualityStatus.VALID:
            return OperationalState.CAUTION
        return OperationalState.NORMAL

    @staticmethod
    def _risk_multiplier(state: OperationalState, policy: StatePolicy) -> Decimal:
        return {
            OperationalState.NORMAL: Decimal(1),
            OperationalState.CAUTION: policy.caution_risk_multiplier,
            OperationalState.REDUCED_RISK: policy.reduced_risk_multiplier,
            OperationalState.NO_NEW_TRADES: Decimal(0),
            OperationalState.HALTED: Decimal(0),
        }[state]


class StateRepository:
    """Immutable catalog storage keyed by deterministic state identity."""

    def __init__(self, store: ImmutableDatasetStore):
        self.store = store

    def publish(self, snapshot: StateSnapshot) -> str:
        content = canonical(snapshot.payload())
        path = self.store.layout.resolve("catalog", f"state-snapshots/{snapshot.state_id}.json")
        self.store._publish(path, content)
        return snapshot.state_id
