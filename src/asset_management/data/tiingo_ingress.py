"""Admit captured Tiingo JSON to Bronze only; no price or OOS acceptance."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

from .immutable import ImmutableDatasetStore, StoredDatasetManifest, digest, _assert_no_secret, sanitize


def admit_captured_tiingo_response(store: ImmutableDatasetStore, response_id: str, *,
                                  license_tag: str, code_revision: str) -> StoredDatasetManifest:
    """Require actual captured HTTP metadata; never infer a market close or vintage."""
    if not store.credentials_classified:
        raise ValueError("CREDENTIALS_NOT_CLASSIFIED")
    if not isinstance(code_revision, str) or not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
        raise ValueError("INVALID_CODE_REVISION")
    receipt, rows, received, provider = _read_capture(store, response_id)
    endpoint, request = receipt["endpoint"], receipt["request"]
    request_hash = store.catalog("requests", {"source": "tiingo-eod", "dataset": "daily-prices",
        "endpoint": endpoint, "http_method": "GET", "request": request})
    # The source response identity binds the captured bytes and metadata. This is
    # a raw collection schema, not a normalized market-price contract.
    return store.write({"result": rows, "source_response_receipt": response_id},
        layer="bronze", source="tiingo-eod", dataset="daily-prices",
        schema_version="tiingo-captured-response-v1", retrieved_at=received,
        available_at=received, provider_timestamp=provider, license_tag=license_tag,
        code_revision=code_revision, request_hash=request_hash, quality_status="RAW")


def _read_capture(store: ImmutableDatasetStore, response_id: str):
    receipt = store.read_catalog("unadmitted-http-responses", response_id)
    if not isinstance(receipt, dict) or (
        receipt.get("source"), receipt.get("dataset"), receipt.get("http_method"), receipt.get("mode")
    ) != ("tiingo-eod", "daily-prices", "GET", "RESEARCH"):
        raise ValueError("TIINGO_RESPONSE_CONTRACT_INVALID")
    endpoint = receipt.get("endpoint")
    if not isinstance(endpoint, str) or not re.fullmatch(r"/tiingo/daily/[A-Z]+/prices", endpoint):
        raise ValueError("TIINGO_ENDPOINT_INVALID")
    request = receipt.get("request")
    if not isinstance(request, dict):
        raise ValueError("TIINGO_REQUEST_INVALID")
    endpoint_symbol = endpoint.split("/")[3]
    for identity in (request, request.get("params", {})):
        if not isinstance(identity, dict):
            raise ValueError("TIINGO_REQUEST_INVALID")
        for field in ("symbol", "ticker"):
            if field in identity and identity[field] != endpoint_symbol:
                raise ValueError("TIINGO_REQUEST_SYMBOL_CONFLICT")
    if type(receipt.get("status_code")) is not int or receipt["status_code"] != 200:
        raise ValueError("TIINGO_HTTP_ERROR")
    for field in ("body_base64", "body_sha256", "received_at", "http_date"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            raise ValueError("TIINGO_CAPTURE_FIELDS_REQUIRED")
    try:
        body = base64.b64decode(receipt["body_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("TIINGO_BODY_ENCODING_INVALID") from exc
    if digest(body) != receipt.get("body_sha256"):
        raise ValueError("TIINGO_BODY_HASH_MISMATCH")
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("TIINGO_BODY_ARRAY_REQUIRED")
    if sanitize(rows, store.secrets) != rows:
        raise ValueError("TIINGO_UNREDACTED_RESPONSE")
    _assert_no_secret(rows, store.secrets)
    try:
        received = datetime.fromisoformat(receipt["received_at"])
        provider = parsedate_to_datetime(receipt["http_date"])
    except (ValueError, TypeError) as exc:
        raise ValueError("TIINGO_TIMESTAMP_INVALID") from exc
    if any(value.tzinfo is None or value.utcoffset() is None for value in (received, provider)):
        raise ValueError("TIINGO_TIMESTAMP_UNKNOWN")
    received = received.astimezone(timezone.utc)
    provider = provider.astimezone(timezone.utc)
    if provider > received:
        raise ValueError("TIINGO_TIMESTAMP_ORDER_INVALID")
    return receipt, rows, received, provider


def validate_captured_tiingo_lineage(store: ImmutableDatasetStore,
                                    manifest: StoredDatasetManifest, body: object) -> None:
    """Revalidate the external content-addressed dependency on every read."""
    if not isinstance(body, dict) or set(body) != {"result", "source_response_receipt"}:
        raise ValueError("TIINGO_RESPONSE_LINEAGE_INVALID")
    receipt, rows, received, provider = _read_capture(store, body["source_response_receipt"])
    request = store.read_catalog("requests", manifest.request_hash)
    expected_request = {"source":"tiingo-eod", "dataset":"daily-prices",
        "endpoint":receipt["endpoint"], "http_method":"GET", "request":receipt["request"]}
    if (request != expected_request or body["result"] != rows
            or manifest.schema_version != "tiingo-captured-response-v1"
            or manifest.layer != "bronze" or manifest.source != "tiingo-eod"
            or manifest.dataset != "daily-prices"
            or manifest.retrieved_at != received.isoformat()
            or manifest.available_at != received.isoformat()
            or manifest.provider_timestamp != provider.isoformat()):
        raise ValueError("TIINGO_RESPONSE_LINEAGE_CONFLICT")
