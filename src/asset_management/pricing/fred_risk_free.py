"""Materialize the approved USD curve from one immutable FRED/ALFRED artifact."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from asset_management.data.immutable import ImmutableDatasetStore
from asset_management.domain.errors import DataQualityError
from asset_management.quality.models import QualityStatus

from .models import RiskFreePoint
from .risk_free import RiskFreeCurve


FRED_USD_RISK_FREE_SERIES = {21: "DGS1MO", 63: "DGS3MO", 126: "DGS6MO", 252: "DGS1"}


def _aware(value: object, reason: str) -> datetime:
    if not isinstance(value, str):
        raise DataQualityError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataQualityError(reason) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataQualityError(reason)
    return parsed.astimezone(timezone.utc)


def _percent(value: object) -> Decimal:
    if not isinstance(value, str):
        raise DataQualityError("RISK_FREE_FRED_VALUE_INVALID")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DataQualityError("RISK_FREE_FRED_VALUE_INVALID") from exc
    if not result.is_finite() or result <= Decimal(-100):
        raise DataQualityError("RISK_FREE_FRED_VALUE_INVALID")
    return result / Decimal(100)


def materialize_usd_fred_risk_free_curve(*, store: ImmutableDatasetStore, manifest_id: str,
                                          information_cutoff: datetime) -> RiskFreeCurve:
    """Read exactly four cutoff-visible FRED/ALFRED points; never interpolate.

    This boundary deliberately only consumes an already-persisted bronze
    artifact.  It does not accept rates from the caller and does not promote an
    incomplete FRED response into a curve.
    """
    if information_cutoff.tzinfo is None or information_cutoff.utcoffset() is None:
        raise DataQualityError("RISK_FREE_CUTOFF_INVALID")
    cutoff = information_cutoff.astimezone(timezone.utc)
    try:
        manifest, body = store.read(manifest_id)
    except (FileNotFoundError, ValueError) as exc:
        raise DataQualityError("RISK_FREE_MANIFEST_UNVERIFIED") from exc
    manifest_available = _aware(manifest.available_at, "RISK_FREE_MANIFEST_TIME_INVALID")
    if (manifest.layer != "bronze" or manifest.source != "fred-alfred" or
            manifest.dataset != "risk-free-curve" or manifest_available > cutoff):
        raise DataQualityError("RISK_FREE_MANIFEST_CONTEXT_INVALID")
    if (not isinstance(body, Mapping) or set(body) != {"observations"} or
            not isinstance(body["observations"], list)):
        raise DataQualityError("RISK_FREE_FRED_SCHEMA_INVALID")

    rows: dict[str, Mapping[str, object]] = {}
    for row in body["observations"]:
        if (not isinstance(row, Mapping) or
                set(row) != {"series_id", "as_of", "available_at", "value_percent"}):
            raise DataQualityError("RISK_FREE_FRED_SCHEMA_INVALID")
        series_id = row["series_id"]
        if not isinstance(series_id, str) or series_id in rows:
            raise DataQualityError("RISK_FREE_FRED_SCHEMA_INVALID")
        rows[series_id] = row
    if set(rows) != set(FRED_USD_RISK_FREE_SERIES.values()):
        raise DataQualityError("RISK_FREE_FRED_TENORS_INCOMPLETE")

    points: list[RiskFreePoint] = []
    curve_as_of: datetime | None = None
    for horizon, series_id in FRED_USD_RISK_FREE_SERIES.items():
        row = rows[series_id]
        as_of = _aware(row["as_of"], "RISK_FREE_FRED_TIME_INVALID")
        available_at = _aware(row["available_at"], "RISK_FREE_FRED_TIME_INVALID")
        if available_at < as_of or as_of > cutoff or available_at > cutoff:
            raise DataQualityError("RISK_FREE_FRED_POINT_NOT_ELIGIBLE")
        if curve_as_of is None:
            curve_as_of = as_of
        elif as_of != curve_as_of:
            raise DataQualityError("RISK_FREE_FRED_AS_OF_CONFLICT")
        points.append(RiskFreePoint(as_of, available_at, horizon, _percent(row["value_percent"]),
                                    "fred-alfred", manifest.manifest_id, QualityStatus.VALID))
    return RiskFreeCurve(tuple(points))
