"""Shared deterministic construction without collapsing component state."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore, canonical
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import (OperationalState, StateComponent, StatePolicy, StateSnapshot,
                     StateType, is_blocking, state_identity, worst_quality)


class StateEngine:
    def __init__(self, *, state_type: StateType, component_names: tuple[str, ...]):
        if not component_names or len(component_names) != len(set(component_names)):
            raise ValueError("STATE_COMPONENT_CONTRACT_INVALID")
        self.state_type = state_type
        self.component_names = component_names

    def build(self, *, as_of: datetime, components: Mapping[str, StateComponent],
              policy: StatePolicy, code_revision: str,
              derive_regime: bool = False) -> StateSnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("STATE_AS_OF_NOT_AWARE")
        if set(components) != set(self.component_names):
            raise DataQualityError("STATE_COMPONENTS_INCOMPLETE")
        if not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
            raise DataQualityError("STATE_CODE_REVISION_INVALID")
        if self.state_type in {StateType.MARKET, StateType.COMPANY} and any(
            component.quality_status is QualityStatus.VALID and
            (not isinstance(component.value, Decimal) or not component.value.is_finite())
            for component in components.values()
        ):
            raise DataQualityError("CONTINUOUS_STATE_VALUE_INVALID")
        quality = worst_quality(components)
        confidence = min(component.confidence for component in components.values())
        freshness = max(component.freshness_seconds for component in components.values())
        operational = self._operational_state(components, quality, confidence, freshness, policy)
        multiplier = self._risk_multiplier(operational, policy)
        regime = self._derive_regime(components) if derive_regime else None
        state_id = state_identity(state_type=self.state_type, as_of=as_of, components=components,
                                  policy=policy, code_revision=code_revision, regime_label=regime)
        feature_ids = tuple(sorted({item for component in components.values()
                                    for item in component.input_feature_ids}))
        return StateSnapshot(state_id, self.state_type, as_of.astimezone(timezone.utc).isoformat(),
                             dict(components), str(confidence), quality, freshness, feature_ids,
                             policy.policy_version, code_revision, operational, str(multiplier), regime)

    def recompute_component(self, snapshot: StateSnapshot, *, component_name: str,
                            component: StateComponent, as_of: datetime,
                            policy: StatePolicy, code_revision: str,
                            derive_regime: bool = False) -> StateSnapshot:
        if snapshot.state_type is not self.state_type or component_name not in self.component_names:
            raise DataQualityError("STATE_COMPONENT_CONTRACT_MISMATCH")
        values = dict(snapshot.components)
        values[component_name] = component
        return self.build(as_of=as_of, components=values, policy=policy,
                          code_revision=code_revision, derive_regime=derive_regime)

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

    def _derive_regime(self, components: Mapping[str, StateComponent]) -> str | None:
        if self.state_type is not StateType.MARKET:
            raise DataQualityError("REGIME_ONLY_AVAILABLE_FOR_MARKET_STATE")
        if any(not isinstance(component.value, Decimal) for component in components.values()):
            return None
        growth = components["growth"].value
        trend = components["trend"].value
        volatility = components["volatility"].value
        if growth > 0 and trend > 0 and volatility <= 0:
            return "EXPANSION"
        if growth < 0 and trend < 0:
            return "CONTRACTION"
        return "TRANSITION"


class StateRepository:
    """Immutable catalog storage keyed by deterministic state identity."""

    def __init__(self, store: ImmutableDatasetStore):
        self.store = store

    def publish(self, snapshot: StateSnapshot) -> str:
        content = canonical(snapshot.payload())
        path = self.store.layout.resolve("catalog", f"state-snapshots/{snapshot.state_id}.json")
        self.store._publish(path, content)
        return snapshot.state_id
