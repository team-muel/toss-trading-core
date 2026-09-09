"""A PIT macro-state research model over canonical immutable ALFRED datasets.

This returns state evidence, not a cross-sectional score, allocation or order.
No independent data repository, backtester or forecast authority is introduced.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from typing import Mapping
from zoneinfo import ZoneInfo

from asset_management.data.alfred import DATASET, RAW_SCHEMA, ROW_SCHEMA, SCHEMA_VERSION, SERIES, SOURCE, normalize_vintages
from asset_management.data.immutable import ImmutableDatasetStore, canonical, digest
from asset_management.domain.errors import DataQualityError
from asset_management.time.asof import AsOfContext, require_as_of_context


@dataclass(frozen=True, slots=True)
class MacroStateSpec:
    lookback_months: int = 3
    publication_lag_days: int = 1
    maximum_rate_age_days: int = 7
    maximum_monthly_age_days: int = 75
    source_timezone: str = "America/Chicago"
    policy_version: str = "macro-state-v1"

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("lookback_months", 1, 12), ("publication_lag_days", 1, 7),
            ("maximum_rate_age_days", 1, 31), ("maximum_monthly_age_days", 31, 180),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid {name}")
        if self.source_timezone != "America/Chicago" or not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("invalid macro source timezone or policy")

    @property
    def spec_hash(self) -> str:
        return digest(canonical({"schema_version": "macro-state-spec-v1", **asdict(self)}))


@dataclass(frozen=True, slots=True)
class MacroStateResult:
    evidence_json: str

    def payload(self) -> dict:
        return json.loads(self.evidence_json)

    @property
    def evidence_hash(self) -> str:
        return digest(self.evidence_json.encode("utf-8"))


def _read_rows(store: ImmutableDatasetStore, manifest_id: str, series_id: str,
               context: AsOfContext) -> list[dict]:
    manifest, rows = store.read(manifest_id)
    expected_schema = f"{SCHEMA_VERSION}:{digest(canonical(ROW_SCHEMA))}"
    if (manifest.source, manifest.dataset, manifest.layer, manifest.quality_status, manifest.schema_version) != (
            SOURCE, DATASET, "silver", "VALID", expected_schema):
        raise DataQualityError("MACRO_MANIFEST_CONTRACT_INVALID")
    context.require_known_at(datetime.fromisoformat(manifest.available_at), label="macro dataset")
    if not isinstance(rows, list) or not rows or len(manifest.parent_manifest_ids) != 1:
        raise DataQualityError("MACRO_MANIFEST_CONTENT_INVALID")
    parent, raw = store.read(manifest.parent_manifest_ids[0])
    expected_raw_schema = f"{SCHEMA_VERSION}:raw:{digest(canonical(RAW_SCHEMA))}"
    if (parent.source, parent.dataset, parent.layer, parent.schema_version, parent.quality_status) != (
            SOURCE, DATASET, "bronze", expected_raw_schema, "RAW"):
        raise DataQualityError("MACRO_RAW_LINEAGE_INVALID")
    context.require_known_at(datetime.fromisoformat(parent.available_at), label="macro raw dataset")
    output_type = rows[0].get("output_type") if isinstance(rows[0], dict) else None
    expected = [dict(row, series_id=series_id) for row in normalize_vintages(raw, series_id=series_id, output_type=output_type)]
    if rows != expected:
        raise DataQualityError("MACRO_NORMALIZATION_MISMATCH")
    return rows


def _monthly_window(rows: list[dict], count: int) -> list[dict] | None:
    window = rows[-count:]
    if len(window) != count:
        return None
    periods = [date.fromisoformat(row["observation_date"]) for row in window]
    month_indices = [day.year * 12 + day.month for day in periods]
    if any(day.day != 1 for day in periods) or any(b != a + 1 for a, b in zip(month_indices, month_indices[1:])):
        return None
    if any(row["value_status"] != "OBSERVED" for row in window):
        return None
    return window


def run_macro_state(spec: MacroStateSpec, *, store: ImmutableDatasetStore,
                    manifests: Mapping[str, str], context: AsOfContext) -> MacroStateResult:
    if not isinstance(spec, MacroStateSpec):
        raise ValueError("MacroStateSpec required")
    context = require_as_of_context(context)
    if context.parameter_set_id != spec.spec_hash or context.policy_version != spec.policy_version:
        raise DataQualityError("MACRO_SPEC_CONTEXT_MISMATCH")
    if not isinstance(manifests, Mapping) or set(manifests) - SERIES:
        raise DataQualityError("MACRO_SERIES_CONTRACT_INVALID")
    local_day = context.information_cutoff_utc.astimezone(ZoneInfo(spec.source_timezone)).date()
    vintage_cutoff = local_day - timedelta(days=spec.publication_lag_days)
    selected, reasons = {}, []
    for series_id in sorted(SERIES):
        if series_id not in manifests:
            reasons.append(f"MISSING_SERIES:{series_id}")
            continue
        rows = _read_rows(store, manifests[series_id], series_id, context)
        snapshot = [row for row in rows if row["realtime_start"] <= vintage_cutoff.isoformat() <= row["realtime_end"]
                    and row["observation_date"] <= local_day.isoformat()]
        if len({row["observation_date"] for row in snapshot}) != len(snapshot):
            raise DataQualityError("MACRO_CONFLICTING_SNAPSHOT")
        snapshot.sort(key=lambda row: row["observation_date"])
        known_periods = [row["observation_date"] for row in rows
                         if row["realtime_start"] <= vintage_cutoff.isoformat()
                         and row["observation_date"] <= local_day.isoformat()]
        if known_periods and (not snapshot or max(known_periods) > snapshot[-1]["observation_date"]):
            reasons.append(f"EXPIRED_LATEST_VINTAGE:{series_id}")
        selected[series_id] = snapshot
        if not snapshot:
            reasons.append(f"MISSING_VINTAGE:{series_id}")
            continue
        age = (local_day - date.fromisoformat(snapshot[-1]["observation_date"])).days
        limit = spec.maximum_rate_age_days if series_id in {"DGS2", "DGS10"} else spec.maximum_monthly_age_days
        if age > limit:
            reasons.append(f"STALE_SERIES:{series_id}")
        if snapshot[-1]["value_status"] != "OBSERVED":
            reasons.append(f"MISSING_VALUE:{series_id}")
    states = None
    if not reasons:
        inflation = _monthly_window(selected["CPIAUCSL"], 13 + spec.lookback_months)
        unemployment = _monthly_window(selected["UNRATE"], 1 + spec.lookback_months)
        policy = _monthly_window(selected["FEDFUNDS"], 1 + spec.lookback_months)
        if inflation is None or unemployment is None or policy is None:
            reasons.append("MISSING_CONTIGUOUS_MONTHLY_HISTORY")
        elif selected["DGS2"][-1]["observation_date"] != selected["DGS10"][-1]["observation_date"]:
            reasons.append("YIELD_CURVE_DATE_MISMATCH")
        else:
            cpi = [Decimal(row["value"]) for row in inflation]
            if any(value <= 0 for value in cpi):
                reasons.append("CPI_LEVEL_INVALID")
            else:
                def direction(value: Decimal) -> int:
                    return 1 if value > Decimal("1e-12") else -1 if value < Decimal("-1e-12") else 0
                current_yoy = cpi[-1] / cpi[-13] - 1
                prior_yoy = cpi[-1 - spec.lookback_months] / cpi[-13 - spec.lookback_months] - 1
                states = {
                    "yield_curve": direction(Decimal(selected["DGS10"][-1]["value"]) - Decimal(selected["DGS2"][-1]["value"])),
                    "inflation_trend": -direction(current_yoy - prior_yoy),
                    "unemployment_trend": -direction(Decimal(unemployment[-1]["value"]) - Decimal(unemployment[0]["value"])),
                    "policy_rate_trend": -direction(Decimal(policy[-1]["value"]) - Decimal(policy[0]["value"])),
                }
    payload = {
        "schema_version": "macro-state-run-v1", "validation_scope": "MECHANISM_ONLY",
        "status": "UNAVAILABLE" if reasons else "COMPUTED", "reason_codes": reasons,
        "semantic_type": "MACRO_STATE", "states": states,
        "spec": asdict(spec), "spec_hash": spec.spec_hash,
        "context": {**asdict(context), "as_of_utc": context.as_of_utc.isoformat(),
                    "information_cutoff_utc": context.information_cutoff_utc.isoformat()},
        "vintage_cutoff_date": vintage_cutoff.isoformat(),
        "manifest_ids": dict(sorted(manifests.items())), "selected_vintages": selected,
        "information_set_hash": digest(canonical(selected)),
        "distinct_periods": {series: len(rows) for series, rows in selected.items()},
    }
    return MacroStateResult(canonical(payload).decode("utf-8"))
