"""Admit captured Tiingo JSON to Bronze only; no price or OOS acceptance."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

from .immutable import ImmutableDatasetStore, StoredDatasetManifest, digest, _assert_no_secret


def admit_captured_tiingo_response(store: ImmutableDatasetStore, response_id: str, *,
                                  license_tag: str, code_revision: str) -> StoredDatasetManifest:
    """Require actual captured HTTP metadata; never infer a market close or vintage."""
    if not store.credentials_classified:
        raise ValueError("CREDENTIALS_NOT_CLASSIFIED")
    if not isinstance(code_revision, str) or not re.fullmatch(r"git:[0-9a-f]{7,40}", code_revision):
        raise ValueError("INVALID_CODE_REVISION")
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
    request_hash = store.catalog("requests", {"source": "tiingo-eod", "dataset": "daily-prices",
        "endpoint": endpoint, "http_method": "GET", "request": request})
    # The source response identity binds the captured bytes and metadata. This is
    # a raw collection schema, not a normalized market-price contract.
    return store.write({"result": rows, "source_response_receipt": response_id},
        layer="bronze", source="tiingo-eod", dataset="daily-prices",
        schema_version="tiingo-captured-response-v1", retrieved_at=received,
        available_at=received, provider_timestamp=provider, license_tag=license_tag,
        code_revision=code_revision, request_hash=request_hash, quality_status="RAW")
