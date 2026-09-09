from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


MACRO_SIGNAL_NAMES = (
    "yield_curve",
    "inflation_trend",
    "unemployment_trend",
    "policy_rate_trend",
)
REQUIRED_MACRO_SERIES = frozenset(
    {"DGS2", "DGS10", "CPIAUCSL", "UNRATE", "FEDFUNDS"}
)


@dataclass(frozen=True)
class MacroVintageObservation:
    """One ALFRED value version and the date on which it first became public."""

    series_id: str
    observation_date: str
    value: str
    realtime_start: str
    realtime_end: str
    raw_manifest_id: str = ""


def parse_alfred_payload(
    body: bytes | str | dict[str, Any],
    *,
    series_id: str,
    raw_manifest_id: str = "",
) -> list[MacroVintageObservation]:
    payload = (
        json.loads(body)
        if isinstance(body, (bytes, str))
        else body
    )
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise ValueError("ALFRED response has no observations array")
    canonical_series = series_id.strip().upper()
    if not canonical_series:
        raise ValueError("ALFRED series_id is required")
    parsed: list[MacroVintageObservation] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        observation_date = str(item.get("date", ""))
        versions: list[tuple[str, str, Any]] = []
        if {"value", "realtime_start", "realtime_end"} <= set(item):
            versions.append(
                (
                    str(item["realtime_start"]),
                    str(item["realtime_end"]),
                    item["value"],
                )
            )
        else:
            prefix = f"{canonical_series}_"
            for field, value in item.items():
                if field == "date" or not str(field).startswith(prefix):
                    continue
                suffix = str(field)[len(prefix) :]
                if len(suffix) != 8 or not suffix.isdigit():
                    raise ValueError("invalid ALFRED output-type-3 vintage column")
                versions.append(
                    (
                        f"{suffix[:4]}-{suffix[4:6]}-{suffix[6:]}",
                        "9999-12-31",
                        value,
                    )
                )
        for realtime_start, realtime_end, raw_value in versions:
            if str(raw_value).strip() == ".":
                continue
            try:
                date.fromisoformat(observation_date)
                start = date.fromisoformat(realtime_start)
                end = date.fromisoformat(realtime_end)
                value = Decimal(str(raw_value))
            except (ValueError, InvalidOperation) as exc:
                raise ValueError("invalid ALFRED vintage observation") from exc
            if end < start or not value.is_finite():
                raise ValueError("invalid ALFRED vintage interval or value")
            parsed.append(
                MacroVintageObservation(
                    series_id=canonical_series,
                    observation_date=observation_date,
                    value=str(value),
                    realtime_start=realtime_start,
                    realtime_end=realtime_end,
                    raw_manifest_id=raw_manifest_id,
                )
            )
    return parsed


class PointInTimeMacroStore:
    """Resolve the latest value version that was actually knowable on a date."""

    def __init__(
        self,
        observations: Iterable[MacroVintageObservation],
        *,
        publication_lag_days: int = 1,
    ) -> None:
        if not 0 <= publication_lag_days <= 7:
            raise ValueError("macro publication lag must be between zero and seven")
        self.publication_lag_days = publication_lag_days
        unique: dict[tuple[str, str, str], MacroVintageObservation] = {}
        for item in observations:
            key = (item.series_id, item.observation_date, item.realtime_start)
            existing = unique.get(key)
            if existing is not None and Decimal(existing.value) != Decimal(item.value):
                raise ValueError(f"conflicting ALFRED vintage: {key}")
            unique[key] = item
        self._by_series: dict[str, list[MacroVintageObservation]] = {}
        for item in unique.values():
            self._by_series.setdefault(item.series_id, []).append(item)
        for values in self._by_series.values():
            values.sort(key=lambda item: (item.observation_date, item.realtime_start))

    @property
    def series_ids(self) -> set[str]:
        return set(self._by_series)

    def snapshot(self, series_id: str, as_of_date: str) -> list[tuple[str, float]]:
        decision = date.fromisoformat(as_of_date)
        cutoff = decision - timedelta(days=self.publication_lag_days)
        latest: dict[str, MacroVintageObservation] = {}
        for item in self._by_series.get(series_id.strip().upper(), []):
            if date.fromisoformat(item.observation_date) > decision:
                continue
            if date.fromisoformat(item.realtime_start) > cutoff:
                continue
            current = latest.get(item.observation_date)
            if current is None or item.realtime_start > current.realtime_start:
                latest[item.observation_date] = item
        return [
            (item_date, float(latest[item_date].value))
            for item_date in sorted(latest)
        ]

    def latest(self, series_id: str, as_of_date: str) -> float | None:
        values = self.snapshot(series_id, as_of_date)
        return values[-1][1] if values else None

    def latest_and_lagged(
        self,
        series_id: str,
        as_of_date: str,
        *,
        lag_observations: int,
    ) -> tuple[float, float] | None:
        if lag_observations < 1:
            raise ValueError("macro lag_observations must be positive")
        values = self.snapshot(series_id, as_of_date)
        if len(values) <= lag_observations:
            return None
        return values[-1][1], values[-1 - lag_observations][1]

    def regime_signals(
        self,
        as_of_date: str,
        *,
        lookback_months: int,
    ) -> dict[str, float] | None:
        if not REQUIRED_MACRO_SERIES <= self.series_ids:
            return None
        two_year = self.latest("DGS2", as_of_date)
        ten_year = self.latest("DGS10", as_of_date)
        inflation = self.snapshot("CPIAUCSL", as_of_date)
        unemployment = self.latest_and_lagged(
            "UNRATE", as_of_date, lag_observations=lookback_months
        )
        policy_rate = self.latest_and_lagged(
            "FEDFUNDS", as_of_date, lag_observations=lookback_months
        )
        if (
            None in {two_year, ten_year, unemployment, policy_rate}
            or len(inflation) <= 12 + lookback_months
        ):
            return None
        assert two_year is not None and ten_year is not None
        assert unemployment is not None and policy_rate is not None
        current_yoy = inflation[-1][1] / inflation[-13][1] - 1.0
        prior_yoy = (
            inflation[-1 - lookback_months][1]
            / inflation[-13 - lookback_months][1]
            - 1.0
        )

        def direction(value: float, *, tolerance: float = 1e-12) -> float:
            if value > tolerance:
                return 1.0
            if value < -tolerance:
                return -1.0
            return 0.0

        return {
            "yield_curve": direction(ten_year - two_year),
            "inflation_trend": -direction(current_yoy - prior_yoy),
            "unemployment_trend": -direction(unemployment[0] - unemployment[1]),
            "policy_rate_trend": -direction(policy_rate[0] - policy_rate[1]),
        }


def load_alfred_from_manifests(
    manifest_root: str | Path,
) -> tuple[list[MacroVintageObservation], list[str]]:
    root = Path(manifest_root)
    lake_root = root.parent.parent
    observations: list[MacroVintageObservation] = []
    manifest_ids: list[str] = []
    for path in sorted(root.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("source") != "fred-alfred"
            or manifest.get("dataset") != "series-observation-revisions"
        ):
            continue
        request = manifest.get("request_metadata")
        series_id = request.get("series_id") if isinstance(request, dict) else None
        if not isinstance(series_id, str) or not series_id:
            raise ValueError(f"FRED manifest lacks series_id: {path}")
        object_path = (lake_root / str(manifest["relative_path"])).resolve()
        object_path.relative_to(lake_root.resolve())
        observations.extend(
            parse_alfred_payload(
                object_path.read_bytes(),
                series_id=series_id,
                raw_manifest_id=str(manifest["manifest_id"]),
            )
        )
        manifest_ids.append(str(manifest["manifest_id"]))
    return observations, sorted(set(manifest_ids))
