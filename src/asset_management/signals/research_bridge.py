"""Explicit boundary from research alpha evidence to production Signal candidates.

The bridge deliberately consumes research *raw expression scores*, not simulated
positions, forecasts, expected returns, portfolio weights, or order intents.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import isfinite
import re
from types import MappingProxyType
from typing import Mapping

from alpha_management.history import HistorySimulationResult
from asset_management.data.immutable import canonical, digest
from asset_management.domain.errors import InvariantViolation
from asset_management.domain.horizon import DECISION_HORIZONS


_HASH = re.compile(r"[0-9a-f]{64}")


class GrossNetBasis(StrEnum):
    NOT_A_RETURN = "NOT_A_RETURN"
    GROSS_RETURN = "GROSS_RETURN"
    NET_RETURN = "NET_RETURN"


class CostTiming(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BEFORE_FORECAST = "BEFORE_FORECAST"
    AFTER_FORECAST = "AFTER_FORECAST"


class DecayStage(StrEnum):
    EXPRESSION = "EXPRESSION"
    RESEARCH_POSITION = "RESEARCH_POSITION"
    FORECAST_VALIDITY = "FORECAST_VALIDITY"
    IC_DIAGNOSTIC = "IC_DIAGNOSTIC"


@dataclass(frozen=True, slots=True)
class ResearchSignalBridgeContract:
    signal_id: str
    version: str
    currency: str
    forecast_horizon: int
    gross_net_basis: GrossNetBasis = GrossNetBasis.NOT_A_RETURN
    cost_timing: CostTiming = CostTiming.NOT_APPLICABLE
    bridge_version: str = "research-signal-bridge@1"

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.signal_id)
            or not isinstance(self.version, str)
            or not self.version.strip()
            or not re.fullmatch(r"[A-Z]{3}", self.currency)
            or self.forecast_horizon not in DECISION_HORIZONS
            or self.gross_net_basis is not GrossNetBasis.NOT_A_RETURN
            or self.cost_timing is not CostTiming.NOT_APPLICABLE
            or not isinstance(self.bridge_version, str)
            or not self.bridge_version.strip()
        ):
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_CONTRACT_INVALID")

    @property
    def key(self) -> str:
        return f"{self.signal_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class ResearchSignalBridgeRecord:
    signal_key: str
    semantic_type: str
    currency: str
    forecast_horizon: int
    gross_net_basis: GrossNetBasis
    cost_timing: CostTiming
    values: Mapping[str, Decimal]
    research_expression_hash: str
    research_lineage_id: str
    dataset_manifest_ids: tuple[str, ...]
    code_revision: str
    consumed_decay_stages: tuple[DecayStage, ...]
    observed_unconsumed_decay_stages: tuple[DecayStage, ...]
    bridge_version: str

    def __post_init__(self) -> None:
        if (
            self.semantic_type != "SIGNAL_VALUE"
            or not self.signal_key.strip()
            or not re.fullmatch(r"[A-Z]{3}", self.currency)
            or self.forecast_horizon not in DECISION_HORIZONS
            or self.gross_net_basis is not GrossNetBasis.NOT_A_RETURN
            or self.cost_timing is not CostTiming.NOT_APPLICABLE
            or not _HASH.fullmatch(self.research_expression_hash)
            or not _HASH.fullmatch(self.research_lineage_id)
            or not self.dataset_manifest_ids
            or any(not _HASH.fullmatch(item) for item in self.dataset_manifest_ids)
            or not self.code_revision.strip()
        ):
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_RECORD_INVALID")
        values = dict(sorted(self.values.items()))
        if not values or any(
            not key.strip() or not isinstance(value, Decimal) or not value.is_finite()
            for key, value in values.items()
        ):
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_VALUES_INVALID")
        consumed = tuple(dict.fromkeys(self.consumed_decay_stages))
        observed = tuple(dict.fromkeys(self.observed_unconsumed_decay_stages))
        if len(consumed) != len(self.consumed_decay_stages) or len(observed) != len(self.observed_unconsumed_decay_stages):
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_DECAY_INVALID")
        if DecayStage.IC_DIAGNOSTIC in consumed or DecayStage.FORECAST_VALIDITY in consumed:
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_DECAY_INVALID")
        if set(consumed) & set(observed):
            raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_DECAY_INVALID")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "dataset_manifest_ids", tuple(sorted(self.dataset_manifest_ids)))

    def payload(self) -> dict[str, object]:
        return {
            "signal_key": self.signal_key,
            "semantic_type": self.semantic_type,
            "currency": self.currency,
            "forecast_horizon": self.forecast_horizon,
            "gross_net_basis": self.gross_net_basis.value,
            "cost_timing": self.cost_timing.value,
            "values": {key: str(value) for key, value in self.values.items()},
            "research_expression_hash": self.research_expression_hash,
            "research_lineage_id": self.research_lineage_id,
            "dataset_manifest_ids": list(self.dataset_manifest_ids),
            "code_revision": self.code_revision,
            "consumed_decay_stages": [stage.value for stage in self.consumed_decay_stages],
            "observed_unconsumed_decay_stages": [stage.value for stage in self.observed_unconsumed_decay_stages],
            "bridge_version": self.bridge_version,
        }


def bridge_history_result(
    result: HistorySimulationResult,
    contract: ResearchSignalBridgeContract,
) -> ResearchSignalBridgeRecord:
    if not isinstance(result, HistorySimulationResult) or not result.points:
        raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_INPUT_INVALID")
    point = result.points[-1]
    if point.signal_time_utc is None or point.information_cutoff_utc is None:
        raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_INPUT_INVALID")
    if not point.dataset_manifest_ids or not point.code_revision:
        raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_LINEAGE_INVALID")
    if any(value is None or not isfinite(value) for value in point.raw.values()):
        raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_VALUES_INVALID")
    values = {key: Decimal(str(value)) for key, value in point.raw.items()}

    consumed: list[DecayStage] = []
    observed: list[DecayStage] = []
    if "ts_decay_linear" in result.expression:
        consumed.append(DecayStage.EXPRESSION)
    if result.settings.decay > 0:
        observed.append(DecayStage.RESEARCH_POSITION)

    lineage_payload = {
        "expression_hash": result.expression_hash,
        "dataset_manifest_ids": sorted(point.dataset_manifest_ids),
        "universe_version": point.universe_version,
        "signal_universe_version": point.signal_universe_version,
        "source_run_id": point.source_run_id,
        "code_revision": point.code_revision,
        "signal_time_utc": point.signal_time_utc.isoformat(),
        "information_cutoff_utc": point.information_cutoff_utc.isoformat(),
        "bridge_version": contract.bridge_version,
    }
    return ResearchSignalBridgeRecord(
        signal_key=contract.key,
        semantic_type="SIGNAL_VALUE",
        currency=contract.currency,
        forecast_horizon=contract.forecast_horizon,
        gross_net_basis=contract.gross_net_basis,
        cost_timing=contract.cost_timing,
        values=values,
        research_expression_hash=result.expression_hash,
        research_lineage_id=digest(canonical(lineage_payload)),
        dataset_manifest_ids=point.dataset_manifest_ids,
        code_revision=point.code_revision,
        consumed_decay_stages=tuple(consumed),
        observed_unconsumed_decay_stages=tuple(observed),
        bridge_version=contract.bridge_version,
    )


def require_decay_stage_available(record: ResearchSignalBridgeRecord, stage: DecayStage) -> None:
    if not isinstance(record, ResearchSignalBridgeRecord) or not isinstance(stage, DecayStage):
        raise InvariantViolation("RESEARCH_SIGNAL_BRIDGE_DECAY_INVALID")
    if stage is DecayStage.IC_DIAGNOSTIC:
        raise InvariantViolation("IC_DECAY_IS_DIAGNOSTIC_NOT_TRANSFORMATION")
    if stage in record.consumed_decay_stages:
        raise InvariantViolation("DECAY_STAGE_ALREADY_CONSUMED")
