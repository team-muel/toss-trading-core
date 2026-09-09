"""ALFRED normalization through the existing raw-first immutable data boundary.

Date-only vintages are not invented intraday publication timestamps. Missing
values remain versioned tombstones. No network client or new data store lives
here; transport and credential/rights approval remain upstream responsibilities.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .immutable import ImmutableDatasetStore, IngestionResult, ProviderDatasetAdapter
from .phase9 import ProviderBatch
from ..domain.errors import DataQualityError

SOURCE = "fred-alfred"
DATASET = "series-observation-revisions"
SCHEMA_VERSION = "alfred-vintages-v1"
SERIES = frozenset({"DGS2", "DGS10", "CPIAUCSL", "UNRATE", "FEDFUNDS"})
ROW_SCHEMA = {
    "provider_entity_id": "string", "observation_date": "string", "value": "string",
    "realtime_start": "string", "realtime_end": "string", "value_status": "string",
    "output_type": "integer",
}


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise DataQualityError("ALFRED_DATE_INVALID")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise DataQualityError("ALFRED_DATE_INVALID") from exc
    if result.isoformat() != value:
        raise DataQualityError("ALFRED_DATE_INVALID")
    return result


def normalize_vintages(body: object, *, series_id: str, output_type: int) -> list[dict]:
    if series_id not in SERIES or type(output_type) is not int or output_type not in (1, 3):
        raise DataQualityError("ALFRED_REQUEST_CONTRACT_INVALID")
    if not isinstance(body, dict) or not isinstance(body.get("observations"), list):
        raise DataQualityError("ALFRED_OBSERVATIONS_MISSING")
    observations = body["observations"]
    # Partial pages must be assembled by the acquisition owner before admission.
    if (type(body.get("count")) is not int or body["count"] != len(observations)
            or type(body.get("offset")) is not int or body["offset"] != 0):
        raise DataQualityError("ALFRED_INCOMPLETE_PAGE")
    unique: dict[tuple[str, str], dict] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise DataQualityError("ALFRED_OBSERVATION_INVALID")
        period = _day(observation.get("date"))
        if output_type == 1:
            versions = [(observation.get("realtime_start"), observation.get("realtime_end"), observation.get("value"))]
        else:
            prefix = f"{series_id}_"
            columns = [key for key in observation if key != "date"]
            if not columns or any(not isinstance(key, str) or not key.startswith(prefix) for key in columns):
                raise DataQualityError("ALFRED_VINTAGE_COLUMN_INVALID")
            versions = []
            for column in columns:
                suffix = column[len(prefix):]
                if len(suffix) != 8 or not suffix.isascii() or not suffix.isdigit():
                    raise DataQualityError("ALFRED_VINTAGE_COLUMN_INVALID")
                versions.append((f"{suffix[:4]}-{suffix[4:6]}-{suffix[6:]}", "9999-12-31", observation[column]))
        for start_value, end_value, raw_value in versions:
            start, end = _day(start_value), _day(end_value)
            if start > end or start < period or not isinstance(raw_value, str):
                raise DataQualityError("ALFRED_VINTAGE_INVALID")
            text = raw_value.strip()
            if text != ".":
                try:
                    value = Decimal(text)
                except InvalidOperation as exc:
                    raise DataQualityError("ALFRED_VALUE_INVALID") from exc
                if not value.is_finite():
                    raise DataQualityError("ALFRED_VALUE_INVALID")
                text = str(value)
            row = dict(provider_entity_id=series_id, observation_date=period.isoformat(),
                       value=text, realtime_start=start.isoformat(), realtime_end=end.isoformat(),
                       value_status="MISSING" if text == "." else "OBSERVED", output_type=output_type)
            key = (row["observation_date"], row["realtime_start"])
            if key in unique and unique[key] != row:
                raise DataQualityError("ALFRED_CONFLICTING_VINTAGE")
            unique[key] = row
    rows = [unique[key] for key in sorted(unique)]
    for index, row in enumerate(rows[:-1]):
        following = rows[index + 1]
        if row["observation_date"] != following["observation_date"]:
            continue
        if output_type == 3:
            row["realtime_end"] = (_day(following["realtime_start"]) - timedelta(days=1)).isoformat()
        elif row["realtime_end"] >= following["realtime_start"]:
            raise DataQualityError("ALFRED_OVERLAPPING_VINTAGE")
    return rows


def ingest_alfred(batch: ProviderBatch, store: ImmutableDatasetStore) -> IngestionResult:
    """Admit a complete decoded response; never request credentials or fetch data."""
    if (batch.source, batch.dataset, batch.schema_version, batch.endpoint, batch.http_method) != (
            SOURCE, DATASET, SCHEMA_VERSION, "/fred/series/observations", "GET"):
        raise DataQualityError("ALFRED_BATCH_CONTRACT_INVALID")
    request = batch.request
    if not isinstance(request, Mapping) or request.get("units", "lin") != "lin":
        raise DataQualityError("ALFRED_REQUEST_CONTRACT_INVALID")
    series_id = request.get("series_id")
    output_type = request.get("output_type", 1)
    if series_id not in SERIES or type(output_type) is not int or output_type not in (1, 3):
        raise DataQualityError("ALFRED_REQUEST_CONTRACT_INVALID")

    def normalize(raw):
        return normalize_vintages(raw, series_id=series_id, output_type=output_type)

    return ProviderDatasetAdapter(store).ingest(
        body=batch.body, request=batch.request, source=batch.source, dataset=batch.dataset,
        endpoint=batch.endpoint, http_method=batch.http_method,
        retrieved_at=batch.received_at, available_at=batch.available_at,
        provider_timestamp=batch.provider_timestamp, license_tag=batch.license_tag,
        code_revision=batch.code_revision, schema_version=SCHEMA_VERSION,
        raw_schema={"observations": "array"}, schema=ROW_SCHEMA,
        instrument_mapping={series_id: series_id}, normalize=normalize,
        status_code=batch.status_code, provider_entity_field="provider_entity_id",
        canonical_entity_field="series_id",
    )
