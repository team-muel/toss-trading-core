"""Versioned feature definitions and immutable snapshot records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from asset_management.quality.models import QualityStatus


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    namespace: str
    name: str
    version: str
    input_fields: tuple[str, ...]
    lookback: str
    horizon: str
    transformation: str
    missing_policy: str = "MISSING_HISTORY"
    quality_policy: str = "REQUIRE_VALID"

    def __post_init__(self) -> None:
        values = (self.feature_id, self.namespace, self.name, self.version,
                  self.lookback, self.horizon, self.transformation)
        if any(not value.strip() for value in values) or not self.input_fields:
            raise ValueError("FEATURE_DEFINITION_INCOMPLETE")
        if not self.feature_id.startswith(self.namespace + ".") or len(set(self.input_fields)) != len(self.input_fields):
            raise ValueError("FEATURE_DEFINITION_INVALID")
        if self.missing_policy != "MISSING_HISTORY":
            raise ValueError("FEATURE_MISSING_POLICY_UNSAFE")
        if self.quality_policy != "REQUIRE_VALID":
            raise ValueError("FEATURE_QUALITY_POLICY_UNSAFE")


@dataclass(frozen=True)
class FeatureInput:
    values: Mapping[str, object]
    available_at: datetime
    event_time: datetime

    def __post_init__(self) -> None:
        for value in (self.available_at, self.event_time):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("FEATURE_INPUT_TIME_NOT_AWARE")


@dataclass(frozen=True)
class FeatureContext:
    instrument_id: str
    as_of: datetime
    information_cutoff: datetime
    input_manifest_ids: tuple[str, ...]
    universe_manifest_id: str
    parameter_set_id: str
    parent_state_id: str | None
    code_revision: str

    def __post_init__(self) -> None:
        if (not self.instrument_id.strip() or not self.input_manifest_ids or
                not self.universe_manifest_id or not self.parameter_set_id.strip()):
            raise ValueError("FEATURE_CONTEXT_INCOMPLETE")
        if (len(set(self.input_manifest_ids)) != len(self.input_manifest_ids) or
                self.universe_manifest_id not in self.input_manifest_ids):
            raise ValueError("FEATURE_CONTEXT_LINEAGE_INVALID")
        for value in (self.as_of, self.information_cutoff):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("FEATURE_CONTEXT_TIME_NOT_AWARE")
        if self.information_cutoff > self.as_of:
            raise ValueError("FEATURE_CUTOFF_AFTER_AS_OF")


@dataclass(frozen=True)
class FeatureValue:
    value: Decimal | None
    quality_status: QualityStatus
    reason_code: str | None = None


@dataclass(frozen=True)
class FeatureSnapshot:
    feature_run_id: str
    instrument_id: str
    feature_id: str
    as_of: str
    information_cutoff: str
    value: str | None
    quality_status: str
    input_manifest_ids: tuple[str, ...]
    parameter_set_id: str
    parent_state_id: str | None
    code_revision: str


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
