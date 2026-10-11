"""Captured ALFRED HTTP lineage through the existing immutable adapter."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

from .alfred import DATASET, INGESTION_SERIES, ROW_SCHEMA, SOURCE, _day, normalize_vintages
from .immutable import (ImmutableDatasetStore, ProviderDatasetAdapter, canonical,
                        digest, sanitize, _assert_no_secret)

SCHEMA_VERSION = "alfred-captured-vintages-v1"
RAW_SCHEMA = {"response": "object", "source_response_receipt": "string"}
ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_ENDPOINT = "/fred/series/observations"
QUERY_DEFAULTS = {"file_type": "json", "units": "lin", "output_type": 1,
                  "observation_start": "1776-07-04", "observation_end": "9999-12-31",
                  "offset": 0, "limit": 100000, "sort_order": "asc"}


def _validate_query_echo(request, response):
    # Real-time defaults depend on the provider's current date; require their
    # explicit captured request values instead of guessing that date later.
    expected = {key: request.get(key, default) for key, default in QUERY_DEFAULTS.items()}
    for key in ("realtime_start", "realtime_end"):
        expected[key] = request.get(key)
    for start, end in (("realtime_start", "realtime_end"), ("observation_start", "observation_end")):
        if _day(expected[start]) > _day(expected[end]):
            raise ValueError("ALFRED_CAPTURE_REQUEST_RANGE_INVALID")
    if (type(expected["offset"]) is not int or expected["offset"] < 0
            or type(expected["limit"]) is not int or not 1 <= expected["limit"] <= 100000
            or expected["sort_order"] not in ("asc", "desc")):
        raise ValueError("ALFRED_CAPTURE_REQUEST_PAGE_INVALID")
    allowed = set(expected) | {"api_key", "series_id", "frequency", "aggregation_method", "vintage_dates"}
    if set(request) - allowed:
        raise ValueError("ALFRED_CAPTURE_REQUEST_PARAMETER_UNSUPPORTED")
    for key in ("frequency", "aggregation_method", "vintage_dates", "series_id"):
        if key in response or (key != "series_id" and key in request):
            if key not in request or key not in response:
                raise ValueError("ALFRED_CAPTURE_QUERY_ECHO_MISSING")
            expected[key] = request[key]
    # order_by is response metadata, not a documented request parameter.
    expected["order_by"] = "observation_date"
    if any(key not in response or canonical(response[key]) != canonical(value)
           for key, value in expected.items()):
        raise ValueError("ALFRED_CAPTURE_QUERY_CONFLICT")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ALFRED_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _read_capture(store, response_id):
    receipt = store.read_catalog("unadmitted-http-responses", response_id)
    if not isinstance(receipt, dict) or (
        receipt.get("source"), receipt.get("dataset"), receipt.get("endpoint"),
        receipt.get("http_method"), receipt.get("mode")
    ) != (SOURCE, DATASET, ENDPOINT, "GET", "RESEARCH"):
        raise ValueError("ALFRED_CAPTURE_IDENTITY_INVALID")
    if type(receipt.get("status_code")) is not int or receipt["status_code"] != 200:
        raise ValueError("ALFRED_CAPTURE_HTTP_ERROR")
    request = receipt.get("request")
    if (not isinstance(request, dict) or not isinstance(request.get("series_id"), str)
            or request["series_id"] not in INGESTION_SERIES
            or request.get("file_type") != "json" or request.get("units", "lin") != "lin"
            or type(request.get("output_type", 1)) is not int
            or request.get("output_type", 1) not in (1, 3)):
        raise ValueError("ALFRED_CAPTURE_REQUEST_INVALID")
    for field in ("body_base64", "body_sha256", "received_at", "http_date"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            raise ValueError("ALFRED_CAPTURE_FIELDS_REQUIRED")
    try:
        raw = base64.b64decode(receipt["body_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("ALFRED_CAPTURE_ENCODING_INVALID") from exc
    if digest(raw) != receipt["body_sha256"]:
        raise ValueError("ALFRED_CAPTURE_BODY_HASH_MISMATCH")
    response = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(response, dict) or response.get("units") != "lin"
            or type(response.get("output_type")) is not int
            or response["output_type"] != request.get("output_type", 1)):
        raise ValueError("ALFRED_CAPTURE_RESPONSE_CONFLICT")
    _validate_query_echo(request, response)
    if canonical(sanitize(response, store.secrets)) != canonical(response):
        raise ValueError("ALFRED_CAPTURE_UNREDACTED_RESPONSE")
    _assert_no_secret(response, store.secrets)
    try:
        received = datetime.fromisoformat(receipt["received_at"])
        provider = parsedate_to_datetime(receipt["http_date"])
    except (ValueError, TypeError) as exc:
        raise ValueError("ALFRED_CAPTURE_TIMESTAMP_INVALID") from exc
    if any(value.tzinfo is None or value.utcoffset() is None for value in (received, provider)):
        raise ValueError("ALFRED_CAPTURE_TIMESTAMP_UNKNOWN")
    received, provider = received.astimezone(timezone.utc), provider.astimezone(timezone.utc)
    if provider > received:
        raise ValueError("ALFRED_CAPTURE_TIMESTAMP_ORDER_INVALID")
    return receipt, response, received, provider


def ingest_captured_alfred_response(store: ImmutableDatasetStore, response_id: str, *,
                                    license_tag: str, code_revision: str):
    """Use captured bytes and receipt times, without inventing publication times."""
    if not store.credentials_classified:
        raise ValueError("CREDENTIALS_NOT_CLASSIFIED")
    if not isinstance(code_revision, str) or not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
        raise ValueError("INVALID_CODE_REVISION")
    receipt, response, received, provider = _read_capture(store, response_id)
    request = receipt["request"]
    series, output_type = request["series_id"], request.get("output_type", 1)

    def normalize(raw):
        return normalize_vintages(raw["response"], series_id=series, output_type=output_type)

    return ProviderDatasetAdapter(store).ingest(
        body={"response": response, "source_response_receipt": response_id},
        request=request, source=SOURCE, dataset=DATASET, endpoint=REQUEST_ENDPOINT,
        http_method="GET", retrieved_at=received, available_at=received,
        provider_timestamp=provider, license_tag=license_tag, code_revision=code_revision,
        schema_version=SCHEMA_VERSION, raw_schema=RAW_SCHEMA, schema=ROW_SCHEMA,
        instrument_mapping={series: series}, normalize=normalize,
        provider_entity_field="provider_entity_id", canonical_entity_field="series_id")


def validate_captured_alfred_lineage(store, manifest, body):
    """Revalidate the captured response dependency for both Bronze and Silver."""
    if manifest.layer == "bronze":
        raw = body
    else:
        if len(manifest.parent_manifest_ids) != 1:
            raise ValueError("ALFRED_CAPTURE_LINEAGE_INVALID")
        parent, raw = store.read(manifest.parent_manifest_ids[0])
        if (parent.layer != "bronze" or parent.source != SOURCE or parent.dataset != DATASET
                or parent.schema_version != f"{SCHEMA_VERSION}:raw:{digest(canonical(RAW_SCHEMA))}"):
            raise ValueError("ALFRED_CAPTURE_LINEAGE_INVALID")
    if not isinstance(raw, dict) or set(raw) != {"response", "source_response_receipt"}:
        raise ValueError("ALFRED_CAPTURE_LINEAGE_INVALID")
    receipt, response, received, provider = _read_capture(store, raw["source_response_receipt"])
    expected_request = {"source": SOURCE, "dataset": DATASET, "endpoint": REQUEST_ENDPOINT,
                        "http_method": "GET", "request": receipt["request"]}
    if (canonical(raw["response"]) != canonical(response)
            or canonical(store.read_catalog("requests", manifest.request_hash)) != canonical(expected_request)
            or manifest.retrieved_at != received.isoformat() or manifest.available_at != received.isoformat()
            or manifest.provider_timestamp != provider.isoformat()):
        raise ValueError("ALFRED_CAPTURE_LINEAGE_CONFLICT")
    if manifest.layer == "silver":
        request = receipt["request"]
        expected_rows = [dict(row, series_id=request["series_id"]) for row in normalize_vintages(
            response, series_id=request["series_id"], output_type=request.get("output_type", 1))]
        if canonical(body) != canonical(expected_rows):
            raise ValueError("ALFRED_CAPTURE_NORMALIZATION_MISMATCH")
